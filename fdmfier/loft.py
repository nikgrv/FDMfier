# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""Join exposed extrusion cross sections without a voxel field."""

from array import array
from collections import defaultdict
from itertools import chain
from math import cos, floor, hypot, isqrt, pi, sin, sqrt

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform, delaunay_2d_cdt

from . import slicer
from .preview import integer_attribute
from .solid import SurfaceMesh

MAX_MESH_BYTES = 512 * 1024 * 1024
PROFILE_SAMPLES = 10
LAYER_ATTRIBUTE = "fdm_reveal_layer"
DROOP_ATTRIBUTE = "fdm_unsupported"
MASK_ATTRIBUTE = "fdm_imperfection_mask"


class IndexedRing:
    """Limit the even-odd test to edges that cross the query's y band."""

    def __init__(self, ring, bounds):
        self.step = max(1, (bounds[3] - bounds[1]) // min(128, 2 * isqrt(len(ring))))
        self.edges = defaultdict(list)
        self.long_edges = []
        self.horizontal = defaultdict(list)
        x0, y0 = ring[-1]
        for x1, y1 in ring:
            if y0 == y1:
                self.horizontal[y0].append((min(x0, x1), max(x0, x1)))
            else:
                if y0 < y1:
                    lo_x, lo_y, hi_x, hi_y = x0, y0, x1, y1
                else:
                    lo_x, lo_y, hi_x, hi_y = x1, y1, x0, y0
                edge = (lo_x, lo_y, hi_x, hi_y, hi_x - lo_x, hi_y - lo_y,
                        min(lo_x, hi_x), max(lo_x, hi_x))
                first, last = lo_y // self.step, hi_y // self.step
                # Keep storage linear even for tall edges in narrow contours.
                if last - first > 12:
                    self.long_edges.append(edge)
                else:
                    for band in range(first, last + 1):
                        self.edges[band].append(edge)
            x0, y0 = x1, y1

    def locate(self, point):
        x, y = point
        for left, right in self.horizontal.get(y, ()):
            if left <= x <= right:
                return -1
        inside = False
        local_edges = self.edges.get(y // self.step, ())
        candidates = chain(local_edges, self.long_edges) if self.long_edges else local_edges
        for ax, ay, bx, by, dx, dy, left, right in candidates:
            if ay <= y < by:
                if x < left:
                    inside = not inside
                elif x <= right:
                    determinant = dx * (y - ay) - dy * (x - ax)
                    if determinant == 0:
                        return -1
                    if determinant > 0:
                        inside = not inside
            elif y == by and x == bx:
                return -1
        return inside


class Region:
    """Query protected areas without testing distant contours at each vertex."""

    def __init__(self, rings):
        self.global_rings = []
        for ring in rings:
            bounds = slicer.bounds([ring])
            sign = 1 if slicer.clip.Orientation(ring) else -1
            # Clipper has less setup overhead on short contours.
            indexed = IndexedRing(ring, bounds) if len(ring) >= 48 else None
            self.global_rings.append((bounds, ring, sign, indexed))
        self.tiles = {}
        if len(rings) > 12:
            x0, y0, x1, y1 = slicer.bounds(rings)
            self.cell = max(1, round(max(x1 - x0, y1 - y0) / sqrt(len(rings))))
            self.tiles = defaultdict(list)
            large = []
            for item in self.global_rings:
                left, bottom, right, top = (value // self.cell for value in item[0])
                if (right - left + 1) * (top - bottom + 1) > 24:
                    large.append(item)
                else:
                    for i in range(left, right + 1):
                        for j in range(bottom, top + 1):
                            self.tiles[i, j].append(item)
            self.global_rings = large

    def contains(self, point):
        x, y = point
        winding = 0
        candidates = self.global_rings
        if self.tiles:
            candidates = chain(candidates, self.tiles.get((x // self.cell, y // self.cell), ()))
        for (left, bottom, right, top), ring, sign, indexed in candidates:
            if left <= x <= right and bottom <= y <= top:
                hit = indexed.locate(point) if indexed is not None else slicer.clip.PointInPolygon(point, ring)
                if hit == -1:
                    return True
                if hit:
                    winding += sign
        return winding != 0


class JoinedMesh(SurfaceMesh):
    def __init__(self):
        super().__init__()
        self.face_layers = array("i")
        self.droop = array("b")
        self.mask = array("b")
        self.support_start = None

    def write(self, mesh):
        super().write(mesh)
        layers = mesh.attributes.new(LAYER_ATTRIBUTE, "INT", "FACE")
        layers.data.foreach_set("value", self.face_layers)
        mesh.polygons.foreach_set("use_smooth", np.asarray(self.face_layers) > 0)
        for name, values, data_type in ((DROOP_ATTRIBUTE, self.droop, "BOOLEAN"), (MASK_ATTRIBUTE, self.mask, "INT8")):
            if values:
                attribute = mesh.attributes.new(name, data_type, "POINT")
                attribute.data.foreach_set("value", values)


class Surface:
    def __init__(self, scale, layers, weight_surface):
        self.mesh = JoinedMesh()
        self.scale = scale
        self.layer = 1
        self.layer_stride = layers + 1
        self.activation = 0
        self.unsupported = None
        self.weight_surface = weight_surface
        self.mask_tree = BVHTree.FromPolygons(*weight_surface[:2], all_triangles=True) if weight_surface else None

    def point(self, xy, z, anchors):
        vertex = len(self.mesh.points) // 3
        self.mesh.points.extend((xy[0] * self.scale, xy[1] * self.scale, z * self.scale))
        planar = (round(xy[0]), round(xy[1]))
        protected = anchors is not None and anchors.contains(planar)
        self.mesh.anchors.append(protected)
        self.mesh.droop.append(protected and self.unsupported is not None and self.unsupported.contains(planar))
        if self.mask_tree is not None:
            self.mesh.mask.append(round(100 * self.weight((xy[0] / slicer.SCALE, xy[1] / slicer.SCALE, z / slicer.SCALE))))
        return vertex

    def face(self, vertices, *, cap=False):
        mesh = self.mesh
        mesh.starts.append(len(mesh.corners))
        mesh.corners.extend(vertices)
        mesh.face_layers.append(-self.layer if cap else self.layer + self.layer_stride * self.activation)

    def weight(self, point):
        if self.mask_tree is None:
            return 1.0
        location, _, index, _ = self.mask_tree.find_nearest(point)
        coords, faces, weights = self.weight_surface
        a, b, c = faces[index]
        value = barycentric_transform(location, coords[a], coords[b], coords[c],
                                      Vector((weights[a], 0, 0)), Vector((weights[b], 1, 0)),
                                      Vector((weights[c], 0, 1))).x
        return min(1, max(0, value))

    def check_budget(self):
        mesh = self.mesh
        bytes_used = (len(mesh.points) * mesh.points.itemsize + len(mesh.anchors)
                      + len(mesh.corners) * mesh.corners.itemsize + len(mesh.starts) * mesh.starts.itemsize
                      + len(mesh.face_layers) * mesh.face_layers.itemsize + len(mesh.droop) + len(mesh.mask))
        if bytes_used > MAX_MESH_BYTES:
            raise slicer.SliceError("Joined Profiles exceeds the mesh memory budget at these print settings.")


def triangulate_regions(coords, edges, edge_sides):
    """Classify CDT faces by crossing actual rim edges from the outside."""
    points, segments, triangles, originals, origins, _ = delaunay_2d_cdt(coords, edges, [], 0, 0.01)
    npoints = len(points)
    crossings = {}
    for (u, v), source_edges in zip(segments, origins):
        mask = 0
        for index in source_edges:
            mask ^= edge_sides[index]
        if mask:
            crossings[u * npoints + v if u < v else v * npoints + u] = mask

    # Using the source face IDs returned by CDT misclassifies some very thin
    # triangles where rims almost coincide. Propagating the two inside bits
    # across the constrained edges gives the same boundary to caps and strips.
    open_edges = {}
    # The low two bits store which rims the edge crosses.
    neighbors = [[] for _ in triangles]
    for i, (a, b, c) in enumerate(triangles):
        for u, v in ((a, b), (b, c), (c, a)):
            edge = u * npoints + v if u < v else v * npoints + u
            other = open_edges.pop(edge, None)
            if other is None:
                open_edges[edge] = i
            else:
                mask = crossings.get(edge, 0)
                neighbors[i].append((other << 2) | mask)
                neighbors[other].append((i << 2) | mask)
    state = [-1] * len(triangles)
    pending = [(face << 2) | crossings.get(edge, 0) for edge, face in open_edges.items()]
    while pending:
        item = pending.pop()
        i, mask = item >> 2, item & 3
        if state[i] != -1:
            continue
        state[i] = mask
        for neighbor in neighbors[i]:
            if state[neighbor >> 2] == -1:
                pending.append(neighbor ^ mask)
    return points, segments, triangles, originals, origins, state


def cap_section(section, ids, z, surface, anchors):
    """Seal this cut using the same boundary vertices as its side walls."""
    coords, edges, vertex_ids = [], [], []
    for ring, ring_ids in zip(section, ids):
        indices = list(range(len(coords), len(coords) + len(ring)))
        coords.extend(ring)
        vertex_ids.extend(ring_ids)
        edges.extend(zip(indices, indices[1:] + indices[:1]))
    if not edges:
        return
    points, _, triangles, originals, _, states = triangulate_regions(coords, edges, [1] * len(edges))
    cap_ids = {}
    for triangle, state in zip(triangles, states):
        if state != 1:
            continue
        for vertex in triangle:
            if vertex not in cap_ids:
                cap_ids[vertex] = (vertex_ids[originals[vertex][0]] if originals[vertex]
                                   else surface.point(points[vertex], z, anchors))
        surface.face(tuple(cap_ids[i] for i in triangle), cap=True)


def reveal_nodes(layers):
    group = next((group for group in bpy.data.node_groups
                  if group.get("fdm_joined_reveal") == layers and group.get("fdm_reveal_version") == 2), None)
    if group is not None:
        return group
    group = bpy.data.node_groups.new(f"FDMfier Reveal · {layers} layers", "GeometryNodeTree")
    group["fdm_joined_reveal"] = layers
    group["fdm_reveal_version"] = 2
    for direction in ("INPUT", "OUTPUT"):
        group.interface.new_socket(name="Geometry", in_out=direction, socket_type="NodeSocketGeometry")
    visible = group.interface.new_socket(name="Visible Layers", in_out="INPUT", socket_type="NodeSocketInt")
    visible.min_value, visible.max_value, visible.default_value = 0, layers, layers
    imperfections = group.interface.new_socket(name="Imperfections", in_out="INPUT", socket_type="NodeSocketFloat")
    imperfections.subtype = "FACTOR"
    imperfections.min_value, imperfections.max_value = 0, 1
    for name in ("Extrusion Width", "Layer Height"):
        socket = group.interface.new_socket(name=name, in_out="INPUT", socket_type="NodeSocketFloat")
        socket.hide_in_modifier = True
    source = group.nodes.new("NodeGroupInput")
    output = group.nodes.new("NodeGroupOutput")
    value = source.outputs["Visible Layers"]
    strength = source.outputs["Imperfections"]
    layer = integer_attribute(group, LAYER_ATTRIBUTE)

    def connect(operand, socket):
        if isinstance(operand, (int, float, tuple)):
            socket.default_value = operand
        else:
            group.links.new(operand, socket)

    def compare(operation, a, b):
        node = group.nodes.new("FunctionNodeCompare")
        node.data_type, node.operation = "INT", operation
        for socket, operand in zip((node.inputs["A"], node.inputs["B"]), (a, b)):
            connect(operand, socket)
        return node.outputs["Result"]

    def scalar(operation, a, b):
        node = group.nodes.new("ShaderNodeMath")
        node.operation = operation
        connect(a, node.inputs[0])
        connect(b, node.inputs[1])
        return node.outputs[0]

    def switch(data_type, condition, when_false, when_true):
        node = group.nodes.new("GeometryNodeSwitch")
        node.input_type = data_type
        connect(condition, node.inputs["Switch"])
        connect(when_false, node.inputs["False"])
        connect(when_true, node.inputs["True"])
        return node.outputs[0]

    # Caps use negative layer numbers. The quotient of a strand's face
    # code is its activation percentage, leaving the remainder for layer.
    stride = layers + 1
    face_layer = scalar("FLOORED_MODULO", layer, stride)
    threshold = scalar("FLOOR", scalar("DIVIDE", layer, stride), 0)
    above_strength = scalar("GREATER_THAN", threshold, scalar("MULTIPLY", strength, 100))
    above_height = compare("GREATER_THAN", face_layer, value)
    hide_uncapped = scalar("MAXIMUM", above_height, above_strength)
    hide_cap = compare("NOT_EQUAL", layer, scalar("MULTIPLY", value, -1))
    hidden = switch("BOOLEAN", compare("LESS_THAN", layer, 0), hide_uncapped, hide_cap)
    delete = group.nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "FACE"
    group.links.new(source.outputs["Geometry"], delete.inputs["Geometry"])
    group.links.new(hidden, delete.inputs["Selection"])
    geometry = delete.outputs["Geometry"]

    def attribute(name, data_type):
        node = group.nodes.new("GeometryNodeInputNamedAttribute")
        node.data_type = data_type
        node.inputs["Name"].default_value = name
        return node.outputs

    mask = attribute(MASK_ATTRIBUTE, "INT")
    weight = switch("FLOAT", mask["Exists"], 1, scalar("MULTIPLY", mask["Attribute"], 0.01))
    factor = scalar("MULTIPLY", strength, weight)
    width = source.outputs["Extrusion Width"]
    height = source.outputs["Layer Height"]
    noise = group.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Detail"].default_value = 1
    group.links.new(group.nodes.new("GeometryNodeInputPosition").outputs["Position"], noise.inputs["Vector"])
    group.links.new(scalar("DIVIDE", 0.8, width), noise.inputs["Scale"])
    shift = group.nodes.new("ShaderNodeSeparateXYZ")
    group.links.new(noise.outputs["Color"], shift.inputs[0])
    wobble = scalar("MULTIPLY", scalar("MULTIPLY", width, 0.18), factor)
    offset = group.nodes.new("ShaderNodeCombineXYZ")
    for axis in ("X", "Y"):
        group.links.new(scalar("MULTIPLY", scalar("SUBTRACT", shift.outputs[axis], 0.5), wobble),
                        offset.inputs[axis])
    droop = scalar("MULTIPLY", attribute(DROOP_ATTRIBUTE, "BOOLEAN")["Attribute"], -0.28)
    group.links.new(scalar("MULTIPLY", scalar("MULTIPLY", height, factor), droop), offset.inputs["Z"])
    deform = group.nodes.new("GeometryNodeSetPosition")
    group.links.new(geometry, deform.inputs["Geometry"])
    group.links.new(offset.outputs[0], deform.inputs["Offset"])
    geometry = switch("GEOMETRY", scalar("GREATER_THAN", strength, 0), geometry, deform.outputs["Geometry"])
    for name in (LAYER_ATTRIBUTE, MASK_ATTRIBUTE, DROOP_ATTRIBUTE):
        strip = group.nodes.new("GeometryNodeRemoveAttribute")
        strip.inputs["Name"].default_value = name
        group.links.new(geometry, strip.inputs["Geometry"])
        geometry = strip.outputs["Geometry"]
    group.links.new(geometry, output.inputs["Geometry"])
    return group


def fractions(ring):
    length = [0.0]
    for a, b in zip(ring, ring[1:] + ring[:1]):
        length.append(length[-1] + hypot(b[0] - a[0], b[1] - a[1]))
    return [value / length[-1] for value in length]


def zipper(lower, upper, a_ids, b_ids, limit):
    """Walk both rims by arc length so different point counts can join."""
    a, b = fractions(lower), fractions(upper)
    i = j = 0
    faces = []
    while i < len(lower) or j < len(upper):
        t = min(a[i + 1] if i < len(lower) else 1, b[j + 1] if j < len(upper) else 1)
        corners = []
        for ring, seq, index in ((lower, a, i), (upper, b, j)):
            p = ring[index % len(ring)]
            if index == len(ring):
                corners.append(p)
                continue
            q = ring[(index + 1) % len(ring)]
            mix = (t - seq[index]) / (seq[index + 1] - seq[index])
            corners.append((p[0] + (q[0] - p[0]) * mix, p[1] + (q[1] - p[1]) * mix))
        if hypot(corners[0][0] - corners[1][0], corners[0][1] - corners[1][1]) > limit:
            return None
        face = [a_ids[i % len(lower)]]
        if i < len(lower) and a[i + 1] <= t + 1e-8:
            i += 1
            face.append(a_ids[i % len(lower)])
        if j < len(upper) and b[j + 1] <= t + 1e-8:
            j += 1
            face.append(b_ids[j % len(upper)])
            face.append(b_ids[(j - 1) % len(upper)])
        else:
            face.append(b_ids[j % len(upper)])
        faces.append(face)
    return faces


def bridge(a, b, a_ids, b_ids, surface, limit):
    """Bridge close, aligned rings. Leave topology changes for the join plane."""
    if len(a) != len(b):
        return False
    if a == b:
        for lower, upper in zip(a_ids, b_ids):
            for i in range(len(lower)):
                surface.face((lower[i - 1], lower[i], upper[i], upper[i - 1]))
        return True
    buckets = defaultdict(list)
    for index, ring in enumerate(b):
        low_x, low_y, _, _ = slicer.bounds([ring])
        buckets[floor(low_x / limit), floor(low_y / limit), slicer.clip.Orientation(ring)].append(index)
    faces = []
    remaining = set(range(len(b)))
    for ring, ids in zip(a, a_ids):
        best = None
        low_x, low_y, _, _ = slicer.bounds([ring])
        x, y = floor(low_x / limit), floor(low_y / limit)
        direction = slicer.clip.Orientation(ring)
        candidates = sorted(index for dx in (-1, 0, 1) for dy in (-1, 0, 1)
                            for index in buckets.get((x + dx, y + dy, direction), ()) if index in remaining)
        for index in candidates:
            match = b[index]
            distance, shift = min((hypot(p[0] - ring[0][0], p[1] - ring[0][1]), k)
                                  for k, p in enumerate(match))
            if distance > limit or (best is not None and distance >= best[0]):
                continue
            shifted = match[shift:] + match[:shift]
            target = b_ids[index][shift:] + b_ids[index][:shift]
            if len(ring) == len(shifted) and all(
                    hypot(p[0] - q[0], p[1] - q[1]) <= limit for p, q in zip(ring, shifted)):
                strips = [(ids[i - 1], ids[i], target[i], target[i - 1]) for i in range(len(ids))]
            else:
                strips = zipper(ring, shifted, ids, target, limit)
            if strips is not None:
                best = distance, index, strips
        if best is None:
            return False
        _, index, strips = best
        remaining.remove(index)
        faces.extend(strips)
    for face in faces:
        surface.face(face)
    return True


def stitch(a, b, a_ids, b_ids, lower_z, upper_z, surface, anchors):
    """Split crossing rims and slope the open area between the two profiles."""
    regions = (a, b)
    coords = []
    input_edges = []
    edge_sides = []
    vertex_sides = []
    for side, rings in enumerate(regions):
        for ring in rings:
            start = len(coords)
            coords.extend(ring)
            indices = list(range(start, len(coords)))
            vertex_sides.extend([1 << side] * len(indices))
            edge_sides.extend([1 << side] * len(indices))
            input_edges.extend(zip(indices, indices[1:] + indices[:1]))
    if not input_edges:
        return

    points, segments, triangles, original_vertices, original_edges, states = triangulate_regions(
        coords, input_edges, edge_sides)
    caps = []
    used = set()
    for triangle, state in zip(triangles, states):
        if state in (1, 2):
            caps.append(triangle if state == 1 else triangle[::-1])
            used.update(triangle)

    # CDT records which segments came from each original rim edge. Side
    # faces use those same split vertices, so they cannot leave T-junctions.
    split = [None] * len(input_edges)
    sides = [0] * len(points)
    source_points = [None] * len(coords)
    for result, originals in enumerate(original_vertices):
        for original in originals:
            source_points[original] = result
            sides[result] |= vertex_sides[original]
    for segment, original in zip(segments, original_edges):
        for edge_index in original:
            if edge_index < len(input_edges):
                subsegments = split[edge_index]
                if subsegments is None:
                    split[edge_index] = segment
                elif isinstance(subsegments, tuple):
                    split[edge_index] = set(subsegments).union(segment)
                else:
                    subsegments.update(segment)
                used.update(segment)
                u, v = segment
                side = edge_sides[edge_index]
                sides[u] |= side
                sides[v] |= side
    # Points shared by crossing rims keep one height. Other rim points sit
    # closer to their original section, turning the former shelf into a ramp.
    phases = (0.5, 0.18, 0.82, 0.5)

    def join_vertex(index):
        return surface.point(points[index], lower_z + phases[sides[index]] * (upper_z - lower_z), anchors)

    plane_ids = [-1] * len(points)
    for index in sorted(used):
        plane_ids[index] = join_vertex(index)
    for cap in caps:
        surface.face(tuple(plane_ids[i] for i in cap))

    lower_faces = defaultdict(list)
    cursor = 0
    for side, groups in enumerate((a_ids, b_ids)):
        for ring in groups:
            for local, first in enumerate(ring):
                last = ring[(local + 1) % len(ring)]
                start, end = input_edges[cursor]
                first_plane, last_plane = source_points[start], source_points[end]
                subsegments = split[cursor]
                cursor += 1
                if (first_plane != last_plane and isinstance(subsegments, tuple)
                        and first_plane in subsegments and last_plane in subsegments):
                    # The common case is a single segment with no CDT splits.
                    key = first_plane, last_plane
                    face = [first, last, plane_ids[last_plane], plane_ids[first_plane]]
                else:
                    endpoints = {first_plane, last_plane}
                    middle = set(subsegments or ()) - endpoints
                    if any(plane_ids[i] == -1 for i in endpoints | middle):
                        for i in sorted(endpoints | middle):
                            if plane_ids[i] == -1:
                                plane_ids[i] = join_vertex(i)
                    start_point = Vector(coords[start])
                    direction = Vector(coords[end]) - start_point
                    plane = [first_plane, *sorted(middle,
                                                 key=lambda i: ((points[i] - start_point).dot(direction), i))]
                    if last_plane != first_plane:
                        plane.append(last_plane)
                    face = [first, last, *(plane_ids[i] for i in reversed(plane))]
                    if len(plane) != 2:
                        surface.face(face if side == 0 else face[::-1])
                        continue
                    key = tuple(plane)
                if side == 0:
                    lower_faces[key].append(face)
                else:
                    matches = lower_faces.get(key)
                    if not matches:
                        surface.face(face[::-1])
                    else:
                        lower = matches.pop()
                        # The edge has the same material on both sections.
                        # Keep its end vertices for neighboring split faces.
                        surface.face((*lower[:3], last, first, lower[3]))
    for faces in lower_faces.values():
        for face in faces:
            surface.face(face)


def footprint(paths, band, skin, thin, relative, width, exposed):
    offsets = defaultdict(lambda: slicer.clip.PyclipperOffset(2, max(1, width * 0.004)))
    for path in paths:
        if path.feature == slicer.INFILL and path.sparse:
            half_height, exponent, radius = 0.72, 3, 0.52
        else:
            half_height, exponent = (0.59, 2) if path.feature in (slicer.WALL, slicer.INFILL, slicer.BRIM) else (0.54, 2)
            radius = 0.49 if path.feature == slicer.INFILL else 0.50
        if abs(relative) >= half_height:
            continue
        remainder = 1 - (abs(relative) / half_height) ** exponent
        curve = remainder ** (1 / 1.44) if path.feature == slicer.INFILL and not path.sparse else sqrt(remainder)
        distance = round(width * radius * curve)
        if distance < 1:
            continue
        end = slicer.clip.ET_CLOSEDLINE if path.closed else slicer.clip.ET_OPENROUND
        offsets[distance, path.feature != slicer.INFILL].AddPath(path.points, slicer.clip.JT_ROUND, end)
    rings, anchors = [], []
    for (radius, protected), group in offsets.items():
        profiles = group.Execute(radius)
        rings.extend(profiles)
        if protected:
            anchors.extend(profiles)
    if abs(relative) < (0.30 if exposed else 0.57):
        rings.extend(band)
        anchors.extend(band)
    if thin and abs(relative) < 0.59:
        # A full-width wall centerline cannot fit here. Use the actual
        # section as a variable-width bead, tapering it towards its ends.
        inset = round(width * 0.04 * (relative / 0.59) ** 2)
        profile = slicer.offset(thin, -inset)
        rings.extend(profile)
        anchors.extend(profile)
    if abs(relative) < 0.25:
        rings.extend(skin)
        anchors.extend(skin)
    union = slicer.polygon_op(rings)
    return [ring for ring in slicer.clip.CleanPolygons(union, max(1, width * 0.0025)) if len(ring) >= 3], anchors


def generate_steps(vertices, triangles, settings, mm_per_unit):
    regions, supports = {}, {}
    paths, layers, narrow = yield from slicer.generate_steps(vertices, triangles, settings, regions,
                                                            support_regions=supports)
    by_layer = defaultdict(list)
    for path in paths:
        if path.feature != slicer.WEB:
            by_layer[path.layer].append(path)

    width, height = slicer.units(settings.width), slicer.units(settings.layer_height)
    bands, skins, thin_sections, unsupported, joins = {}, {}, {}, {}, {}
    for index, (contours, skin) in regions.items():
        yield 0.94 + 0.01 * index / layers, "Preparing walls and solid surfaces"
        outer = slicer.offset(contours, -round(width * 0.5))
        if settings.walls > 1:
            inner = slicer.offset(contours, -round(width * (settings.walls - 0.49)))
            bands[index] = slicer.polygon_op(outer, inner, slicer.clip.CT_DIFFERENCE)
        thin = slicer.polygon_op(contours, slicer.offset(outer, round(width * 0.55)), slicer.clip.CT_DIFFERENCE)
        if thin:
            thin_sections[index] = slicer.polygon_op(slicer.offset(thin, round(width * 0.22)),
                                                     contours, slicer.clip.CT_INTERSECTION)
        if skin:
            # The solid pad must overlap the wall backing, including where
            # the underside hatching runs across a steep terrace.
            boundary = slicer.offset(contours, -round(width * (settings.walls - 0.58)))
            skins[index] = slicer.polygon_op(slicer.offset(skin, round(width * 0.48)),
                                            boundary, slicer.clip.CT_INTERSECTION)
            if index - 1 in skins:
                joins[index - 1] = slicer.polygon_op(skins[index - 1], skins[index], slicer.clip.CT_INTERSECTION)
        if index and index - 1 in regions:
            # Only sag where the preceding layer did not carry the edge.
            lower = slicer.offset(regions[index - 1][0], round(width * 0.32))
            unsupported[index] = slicer.polygon_op(slicer.offset(contours, width // 5), lower,
                                                   slicer.clip.CT_DIFFERENCE)

    surface = Surface(1 / (slicer.SCALE * mm_per_unit), layers, getattr(settings, "weight_surface", None))
    def model_section(nearby, relative):
        exposed = (nearby == 0 and relative < 0) or (nearby == layers - 1 and relative > 0)
        return footprint(by_layer[nearby], bands.get(nearby, ()), skins.get(nearby, ()),
                         thin_sections.get(nearby, ()), relative, width, exposed)

    support_share = 0.012 if supports else 0
    yield from loft_sections(surface, width, height, layers, model_section, joins, unsupported,
                             0.95, 0.039 - support_share)
    for path in paths:
        if path.feature == slicer.WEB:
            add_strand(surface, path, width, height)

    if supports:
        surface.mesh.support_start = len(surface.mesh.starts)
        support_top = max(supports) + 1

        def support_section(nearby, relative):
            if abs(relative) >= 0.60 or nearby not in supports:
                return (), ()
            radius = round(width * 0.35 * sqrt(1 - (relative / 0.60) ** 2))
            area = slicer.offset(supports[nearby], radius)
            return area, area

        yield from loft_sections(surface, width, height, support_top, support_section, {}, {},
                                 0.989 - support_share, support_share)
    if not surface.mesh.starts:
        raise slicer.SliceError("The print settings produced no continuous surface.")
    return surface.mesh, layers, narrow


def loft_sections(surface, width, height, count, profile, joins, unsupported, start, share):
    prior, prior_ids, prior_height, prior_anchors = [], [], -height, []
    layers = surface.layer_stride - 1
    profile_samples = tuple((i + 0.37) / PROFILE_SAMPLES - 0.5 for i in range(PROFILE_SAMPLES))
    for index in range(-1, count + 1):
        unsupported_rings = unsupported.get(min(layers - 1, max(0, index)))
        surface.unsupported = Region(unsupported_rings) if unsupported_rings else None
        for sample, anchor in enumerate(profile_samples):
            z = (index + 0.5 + anchor) * height
            yield start + share * (index + 1) / (count + 2), "Joining rounded print boundaries"
            rings = []
            anchors = []
            for nearby in range(max(0, index - 1), min(count, index + 2)):
                relative = index + anchor - nearby
                profiles, protected = profile(nearby, relative)
                rings.extend(profiles)
                anchors.extend(protected)
                if 0.20 < relative < 0.80 and nearby in joins:
                    rings.extend(joins[nearby])
                    anchors.extend(joins[nearby])
            section = slicer.polygon_op(rings)
            anchors = slicer.offset(slicer.polygon_op(anchors), max(2, round(width * 0.015)))
            cut = sample == 0 and 0 < index < layers
            surface.layer = min(layers, max(1, index if cut else index + 1))
            protection = Region(anchors)
            ids = [[surface.point(point, z, protection) for point in ring] for ring in section]
            if not bridge(prior, section, prior_ids, ids, surface, width * 0.30):
                stitch(prior, section, prior_ids, ids, prior_height, z, surface,
                       Region(slicer.polygon_op(prior_anchors + anchors)))
            if cut:
                cap_section(section, ids, z, surface, protection)
            prior, prior_ids, prior_height, prior_anchors = section, ids, z, anchors
            surface.check_budget()


def add_strand(surface, path, width, height):
    a, b = path.points[0], path.points[-1]
    dx, dy = b[0] - a[0], b[1] - a[1]
    length = hypot(dx, dy)
    radius = width * 0.065
    normal = (-dy / length * radius, dx / length * radius)
    z = (path.layer + 0.5) * height
    weight = min(surface.weight((*[p / slicer.SCALE for p in end], z / slicer.SCALE)) for end in (a, b))
    if weight < path.activation:
        return
    surface.layer = path.layer + 1
    surface.activation = min(100, round(path.activation / weight * 100))
    surface.unsupported = None
    rings = []
    for index, (x, y) in enumerate(path.points):
        t = index / (len(path.points) - 1)
        center = z - min(height * 0.75, length * 0.02) * sin(pi * t)
        rings.append([surface.point((x + normal[0] * cos(angle), y + normal[1] * cos(angle)),
                                    center + radius * sin(angle), None)
                      for angle in (i * pi / 3 for i in range(6))])
        surface.mesh.anchors[-6:] = array("b", [1] * 6)
    surface.face(rings[0][::-1])
    for a_ring, b_ring in zip(rings, rings[1:]):
        for i in range(6):
            surface.face((a_ring[i - 1], a_ring[i], b_ring[i], b_ring[i - 1]))
    surface.face(rings[-1])
    surface.activation = 0
