# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""Place a print source on the world XY plane."""

from math import cos, pi

import bmesh
import numpy as np
from mathutils import Matrix, Vector

from .slicer import SliceError

DOWN = Vector((0, 0, -1))
MAX_CANDIDATES = 24


def selected_face_plane(obj):
    bm = bmesh.from_edit_mesh(obj.data)
    bm.normal_update()
    faces = [face for face in bm.faces if face.select and not face.hide]
    if not faces:
        raise SliceError("Select one or more faces in Edit Mode.")

    world = obj.matrix_world.copy()
    normals = world.to_3x3().inverted_safe().transposed()
    points = [world @ vertex.co for vertex in bm.verts]
    down = Vector()
    pivot = Vector()
    total_area = 0.0
    selected_points = []
    for face in faces:
        polygon = [world @ vertex.co for vertex in face.verts]
        normal = (normals @ face.normal).normalized()
        origin = polygon[0]
        area_vector = sum(((a - origin).cross(b - origin)
                           for a, b in zip(polygon[1:], polygon[2:])), Vector())
        area = area_vector.length / 2
        down += normal * area
        pivot += sum(polygon, Vector()) * (area / len(polygon))
        total_area += area
        selected_points.extend(polygon)
    if not total_area or down.length < total_area * 0.95:
        raise SliceError("Select flat faces that point in the same direction.")

    pivot /= total_area
    down.normalize()
    tolerance = max(max((point - pivot).length for point in points) * 1e-5, 1e-8)
    if any(abs((point - pivot).dot(down)) > tolerance for point in selected_points):
        raise SliceError("Select a single flat face or multiple coplanar faces.")
    if any((point - pivot).dot(down) > tolerance for point in points):
        raise SliceError("Geometry extends below that face when laid flat. Select an exterior bed face.")
    return down, pivot, pivot.z


def auto_plane(context, obj):
    evaluated = obj.evaluated_get(context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        if not mesh.loop_triangles:
            raise SliceError("Auto Orient needs a mesh with surface faces.")
        world = evaluated.matrix_world
        vertices = np.array([world @ vertex.co for vertex in mesh.vertices], dtype=np.float64)
        triangles = np.array([tuple(triangle.vertices) for triangle in mesh.loop_triangles])
        normal_matrix = world.to_3x3().inverted_safe().transposed()
        normals = np.array([(normal_matrix @ triangle.normal).normalized()
                            for triangle in mesh.loop_triangles], dtype=np.float64)
    finally:
        evaluated.to_mesh_clear()

    corners = vertices[triangles]
    areas = np.linalg.norm(np.cross(corners[:, 1] - corners[:, 0],
                                   corners[:, 2] - corners[:, 0]), axis=1) / 2
    total = areas.sum()
    used = vertices[np.unique(triangles)]
    low, high = used.min(axis=0), used.max(axis=0)
    extent = float(np.linalg.norm(high - low))
    if not total or not extent:
        raise SliceError("Auto Orient needs a mesh with nonzero surface area.")

    # Group near-parallel triangles and keep a representative surface normal
    # so the search stays bounded on curved or highly tessellated objects.
    bins = {}
    for normal, area in zip(normals, areas):
        if area <= 0:
            continue
        key = tuple(np.rint(normal * 12).astype(int))
        accumulated, largest, representative = bins.get(key, (0.0, 0.0, normal))
        if area > largest:
            largest, representative = area, normal
        bins[key] = (accumulated + area, largest, representative)
    candidates = [np.asarray(DOWN)]
    for _, _, direction in sorted(bins.values(), key=lambda item: item[0], reverse=True)[:MAX_CANDIDATES]:
        if not any(np.dot(direction, other) > 0.99999 for other in candidates):
            candidates.append(direction)

    best = None
    threshold = cos(pi / 4)
    for direction in candidates:
        depth = -np.einsum("ij,j->i", vertices, direction)
        triangle_depth = depth[triangles]
        bottom = float(triangle_depth.min())
        height = float(triangle_depth.max() - bottom)
        if height <= extent * 1e-8:
            continue
        facing = np.einsum("ij,j->i", normals, direction)
        contact = (triangle_depth.max(axis=1) <= bottom + extent * 1e-5) & (facing > 0.99)
        unsupported = (facing > threshold) & ~contact
        contact_area = float(areas[contact].sum() / total)
        overhang = float(np.sum(areas[unsupported] * facing[unsupported]) / total)
        score = overhang + 0.08 * height / extent - 0.20 * contact_area
        if contact_area < 0.0025:
            score += 0.06
        if best is None or score < best[0] - 1e-9:
            best = (score, Vector(direction), bottom)
    if best is None:
        raise SliceError("Auto Orient could not find a build direction with printable height.")
    _, down, bottom = best
    pivot = Vector((low + high) / 2)
    return down, pivot, bottom + pivot.dot(down) + pivot.z


def place_on_bed(context, obj, down, pivot, floor):
    rotation = down.rotation_difference(DOWN).to_matrix().to_4x4()
    transform = Matrix.Translation(pivot) @ rotation @ Matrix.Translation(-pivot)
    transform.translation.z -= floor
    target = transform @ obj.matrix_world
    old_basis = obj.matrix_basis.copy()
    obj.matrix_world = target
    context.view_layer.update()
    # Constraints and non-uniform parent transforms can prevent Blender from
    # representing the requested world transform on the object's channels.
    actual = obj.matrix_world
    linear_scale = max(target.to_3x3().col[i].length for i in range(3))
    linear_error = max(abs(actual[row][col] - target[row][col])
                       for row in range(3) for col in range(3))
    if (linear_error > max(1e-7, linear_scale * 1e-5)
            or (actual.translation - target.translation).length > max(1e-6, linear_scale * 1e-5)):
        obj.matrix_basis = old_basis
        context.view_layer.update()
        raise SliceError("A parent or constraint prevents this orientation. Adjust the object hierarchy first.")
