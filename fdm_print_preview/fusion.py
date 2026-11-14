# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""Fuse the evaluated beads around an internal mesh of bonding surfaces."""

from collections import defaultdict
from math import ceil

import bpy
import numpy as np
from mathutils import Vector
from mathutils.geometry import delaunay_2d_cdt

from .slicer import INFILL, SCALE, SliceError, WALL, WEB, clip, offset, polygon_op


def path_footprints(mesh, width, visible):
    """Trace the centers of the evaluated extrusion paths in integer XY units."""
    attrs = mesh.attributes
    names = ("fdm_layer", "fdm_path", "fdm_feature")
    if any(attrs.get(name) is None for name in names):
        raise SliceError("The preview has no visible extrusion paths.")
    layers, paths, features = (attrs[name].data for name in names)
    edge_indices = np.empty(len(mesh.edges) * 2, dtype=np.int32)
    mesh.edges.foreach_get("vertices", edge_indices)
    degrees = np.bincount(edge_indices, minlength=len(mesh.vertices))
    grouped = defaultdict(lambda: defaultdict(list))
    unit = SCALE / width
    for i, vertex in enumerate(mesh.vertices):
        if features[i].value == WEB or layers[i].value >= visible:
            continue
        grouped[layers[i].value][paths[i].value].append(i)

    def freeze(rings):
        return tuple(tuple(map(tuple, ring)) for ring in clip.CleanPolygons(rings, 60) if ring)

    # Cache repeated cross sections without retaining all path coordinates twice.
    previous_paths, previous_footprint = None, None
    for layer in range(visible):
        layer_paths = []
        for indices in grouped[layer].values():
            points = [(round(mesh.vertices[i].co.x * unit), round(mesh.vertices[i].co.y * unit))
                      for i in indices]
            closed = len(indices) >= 3 and all(degrees[i] == 2 for i in indices)
            if closed:
                points = clip.CleanPolygon(points, 60)
            if len(points) >= (3 if closed else 2):
                layer_paths.append((points, closed, features[indices[0]].value))
        if layer_paths == previous_paths:
            yield previous_footprint
            continue
        bead_cores = clip.PyclipperOffset(2, 400)
        skin_cores = clip.PyclipperOffset(2, 150)
        wall_cores = clip.PyclipperOffset(2, 150)
        for points, closed, feature in layer_paths:
            end = clip.ET_CLOSEDLINE if closed else clip.ET_OPENSQUARE
            bead_cores.AddPath(points, clip.JT_ROUND, end)
            if feature == WALL:
                wall_cores.AddPath(points, clip.JT_ROUND, end)
            if feature == INFILL:
                skin_cores.AddPath(points, clip.JT_SQUARE, end)
        bonds = freeze(bead_cores.Execute(round(0.29 * SCALE)))
        skin = freeze(skin_cores.Execute(round(0.53 * SCALE)))
        # Unite the wall tracks before shrinking the footprint. This leaves
        # a shared core between neighboring tracks and preserves the cavity.
        walls = freeze(offset(wall_cores.Execute(round(0.56 * SCALE)), -round(0.18 * SCALE)))
        previous_paths, previous_footprint = layer_paths, (bonds, skin, walls)
        yield previous_footprint


def add_prisms(rings, z0, z1, width, vertices, faces):
    if not rings:
        return
    coords, polygons, signs = [], [], []
    for ring in rings:
        positive = clip.Orientation(ring)
        first = len(coords)
        coords.extend(Vector(point) for point in (ring if positive else reversed(ring)))
        polygons.append(list(range(first, len(coords))))
        signs.append(1 if positive else -1)

    points, _, triangles, _, _, origins = delaunay_2d_cdt(coords, [], polygons, 1, 0.5)
    start = len(vertices)
    count = len(points)
    factor = width / SCALE
    vertices.extend((p.x * factor, p.y * factor, z) for z in (z0, z1) for p in points)
    boundary = set()
    for triangle, ids in zip(triangles, origins):
        if sum(signs[i] for i in ids) <= 0:
            continue
        a, b, c = triangle
        faces.append((start + c, start + b, start + a))
        faces.append((start + count + a, start + count + b, start + count + c))
        for edge in ((a, b), (b, c), (c, a)):
            reverse = edge[::-1]
            if reverse in boundary:
                boundary.remove(reverse)
            else:
                boundary.add(edge)
    faces.extend((start + a, start + b, start + count + b, start + count + a)
                 for a, b in sorted(boundary))


def core_mesh(paths, width, height, visible):
    """Keep bonds inside the beads and retain the spaces between sparse paths."""
    vertices, faces = [], []
    batches = []
    previous = ()
    previous_walls = ()
    for layer, (footprint, skin, walls) in enumerate(path_footprints(paths, width, visible)):
        z = layer * height
        overlap = polygon_op(previous, footprint, clip.CT_INTERSECTION)
        wall_overlap = polygon_op(previous_walls, walls, clip.CT_INTERSECTION)
        overlap = polygon_op(overlap + wall_overlap)
        # The bond crosses the layer interface. At each end it is already
        # inside the rounded section of both adjacent beads.
        first = len(faces)
        add_prisms(overlap, z - height * 0.25, z + height * 0.25, width, vertices, faces)
        batches.extend([3 * layer] * (len(faces) - first))
        first = len(faces)
        add_prisms(walls, z + height * 0.20, z + height * 0.80, width, vertices, faces)
        batches.extend([3 * layer + 1] * (len(faces) - first))
        first = len(faces)
        # Add only the slivers between adjacent hatch beads. Keeping the
        # rest of the skin core out of the Boolean saves many intersections.
        gaps = polygon_op(skin, footprint, clip.CT_DIFFERENCE)
        add_prisms(gaps, z + height * 0.38, z + height * 0.62, width, vertices, faces)
        batches.extend([3 * layer + 2] * (len(faces) - first))
        previous_walls = walls
        previous = footprint
        if len(vertices) > 2_500_000:
            raise SliceError("The internal bonds exceed the fusion limit. Reveal fewer layers or increase Layer Height.")
    mesh = bpy.data.meshes.new("FDMfier internal bonds")
    try:
        mesh.from_pydata(vertices, [], faces)
        mesh.update()
        batch = mesh.attributes.new("fdm_bond_batch", "INT", "FACE")
        batch.data.foreach_set("value", batches)
        return mesh
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
