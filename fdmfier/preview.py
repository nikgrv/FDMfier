# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""Editable path meshes and their shared Geometry Nodes modifier."""

from collections import defaultdict
from math import ceil, exp, hypot
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import barycentric_transform

import bpy

from .slicer import SCALE, WEB, SliceError, inside

BEAD_PROFILE_SEGMENTS = 16
GROUP_NAME = "FDMfier"
EFFECTS = ("Width Variation", "Layer Drift", "Stringing", "Overhang Droop")
FEATURE_NAMES = ("Walls", "Infill", "Supports", "Skirt", "Stringing", "Brim")


def path_points(path, width):
    points = path.points
    if not path.wall:
        return [(x, y, 0.0, 0.0, 4 * t * (1 - t) if path.feature == WEB else 0.0)
                for (x, y), t in zip(points, (i / (len(points) - 1) for i in range(len(points))))]

    sampled = []
    distances = []
    length = 0.0
    for a, b in zip(points, points[1:] + points[:1]):
        segment = hypot(b[0] - a[0], b[1] - a[1])
        count = max(1, ceil(segment / (2 * width)))
        for index in range(count):
            t = index / count
            sampled.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
            distances.append(length + t * segment)
        length += segment

    rear = max(range(len(sampled)), key=lambda i: (sampled[i][1], -abs(sampled[i][0])))
    aligned = distances[rear]
    staggered = (aligned + (path.layer % 8) * length / 8) % length
    staggered = min(distances, key=lambda d: min(abs(d - staggered), length - abs(d - staggered)))

    def weight(distance, center):
        delta = abs(distance - center)
        return exp(-(min(delta, length - delta) / width) ** 2)

    exposed = ([float(not inside((round(x), round(y)), path.support_below)) for x, y in sampled]
               if path.support_below is not None else [0.0] * len(sampled))
    return [(x, y, weight(d, aligned), weight(d, staggered),
             exposed[i] * (exposed[i - 1] + 2 + exposed[(i + 1) % len(sampled)]) / 4)
            for i, ((x, y), d) in enumerate(zip(sampled, distances))]


def make_path_mesh(paths, settings, mm_per_unit):
    vertices, edges = [], []
    values = defaultdict(list)
    factor = 1 / (SCALE * mm_per_unit)
    width = settings.width * SCALE
    surface = getattr(settings, "weight_surface", None)
    if surface:
        coordinates, faces, weights = surface
        tree = BVHTree.FromPolygons(coordinates, faces, all_triangles=True)

        def sample(x, y, z):
            nearest, _, face, _ = tree.find_nearest((x / SCALE, y / SCALE, z))
            if face is None:
                return 0.0
            a, b, c = faces[face]
            interpolated = barycentric_transform(nearest, coordinates[a], coordinates[b], coordinates[c],
                                                Vector((weights[a], 0, 0)), Vector((weights[b], 0, 0)),
                                                Vector((weights[c], 0, 0)))
            weight = min(1.0, max(0.0, interpolated.x))
            return 1.0 - weight if settings.invert_weight_group else weight
    for path_id, path in enumerate(sorted(paths, key=lambda path: path.feature)):
        points = path_points(path, width)
        z = (path.layer + 0.5) * settings.layer_height / mm_per_unit
        if not surface:
            mask = [1.0] * len(points)
        elif path.feature == WEB:
            # Keep strands attached at both ends when the mask is painted locally.
            strength = min(sample(*points[i][:2], z * mm_per_unit) for i in (0, -1))
            mask = [strength] * len(points)
        else:
            mask = [sample(x, y, z * mm_per_unit) for x, y, *_ in points]
        first = len(vertices)
        for point_order, (x, y, aligned, staggered, sag) in enumerate(points):
            vertices.append((x * factor, y * factor, z))
            values["fdm_layer"].append(path.layer)
            values["fdm_path"].append(path_id)
            values["fdm_order"].append(point_order)
            values["fdm_feature"].append(path.feature)
            values["fdm_top_skin"].append(int(path.top_skin))
            values["fdm_aligned"].append(aligned)
            values["fdm_staggered"].append(staggered)
            values["fdm_sag"].append(sag)
            values["fdm_mask"].append(mask[point_order])
            values["fdm_web_threshold"].append(path.activation)
        edges.extend((i, i + 1) for i in range(first, len(vertices) - 1))
        if path.closed:
            edges.append((len(vertices) - 1, first))

    mesh = bpy.data.meshes.new("FDM extrusion paths")
    try:
        mesh.from_pydata(vertices, edges, [])
        mesh.update()
        for name, data in values.items():
            attr_type = "FLOAT" if name in {"fdm_aligned", "fdm_staggered", "fdm_sag", "fdm_web_threshold", "fdm_mask"} else "INT"
            mesh.attributes.new(name, attr_type, "POINT").data.foreach_set("value", data)
        return mesh
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise


def node_group():
    existing = next((group for group in bpy.data.node_groups if group.get("fdm_schema") == 7), None)
    if existing is not None:
        return existing
    group = bpy.data.node_groups.new(GROUP_NAME, "GeometryNodeTree")
    try:
        build_nodes(group)
        group["fdm_schema"] = 7
        return group
    except Exception:
        bpy.data.node_groups.remove(group)
        raise


def build_nodes(group):
    def interface(name, kind, direction="INPUT", **properties):
        socket = group.interface.new_socket(name=name, in_out=direction, socket_type=kind)
        for key, value in properties.items():
            setattr(socket, key, value)
        return socket

    interface("Geometry", "NodeSocketGeometry")
    interface("Visible Layers", "NodeSocketInt", default_value=2000, min_value=0, max_value=2000)
    interface("Imperfections", "NodeSocketFloat", default_value=0.15, min_value=0, max_value=1,
              subtype="FACTOR")
    for name in EFFECTS:
        interface(name, "NodeSocketFloat", default_value=1.0, min_value=0, max_value=1, subtype="FACTOR")
    seam_menu = interface("Seam Placement", "NodeSocketMenu")
    interface("Bead Width", "NodeSocketFloat", default_value=0.0045, min_value=0.0000001, hide_in_modifier=True)
    interface("Layer Height", "NodeSocketFloat", default_value=0.002, min_value=0.0000001, hide_in_modifier=True)
    interface("Geometry", "NodeSocketGeometry", "OUTPUT")

    def node(kind, label, x, y):
        item = group.nodes.new(kind)
        item.label = label
        item.location = (x, y)
        return item

    def connect(value, target):
        if isinstance(value, bpy.types.NodeSocket):
            group.links.new(value, target)
        else:
            target.default_value = value

    def attr(name, attr_type, x, y):
        item = node("GeometryNodeInputNamedAttribute", name, x, y)
        item.data_type = attr_type
        item.inputs["Name"].default_value = name
        return item.outputs["Attribute"]

    def math(operation, a, b, x, y, label):
        item = node("ShaderNodeMath", label, x, y)
        item.operation = operation
        connect(a, item.inputs[0])
        connect(b, item.inputs[1])
        return item.outputs[0]

    def compare(operation, a, b, x, y, label):
        item = node("FunctionNodeCompare", label, x, y)
        item.data_type = "INT"
        item.operation = operation
        connect(a, item.inputs["A"])
        connect(b, item.inputs["B"])
        return item.outputs["Result"]

    inputs = node("NodeGroupInput", "Preview settings", -1060, 400).outputs
    layer = attr("fdm_layer", "INT", -1060, 140)
    hidden = compare("GREATER_EQUAL", layer, inputs["Visible Layers"], -840, 140, "Above reveal height")
    mask = attr("fdm_mask", "FLOAT", -1340, -830)
    strength = math("MULTIPLY", mask, inputs["Imperfections"], -1090, -790, "Weight by painted mask")
    stringing = math("MULTIPLY", strength, inputs["Stringing"], -1030, -1350, "Stringing strength")
    feature = attr("fdm_feature", "INT", -1080, -950)
    web = compare("EQUAL", feature, WEB, -840, -970, "Stringing paths")
    threshold = attr("fdm_web_threshold", "FLOAT", -1080, -1150)
    gate = node("FunctionNodeCompare", "Stringing appears as imperfections increase", -840, -1160)
    gate.data_type = "FLOAT"
    gate.operation = "LESS_THAN"
    connect(stringing, gate.inputs["A"])
    connect(threshold, gate.inputs["B"])

    def boolean(operation, a, b, x, y, label):
        item = node("FunctionNodeBooleanMath", label, x, y)
        item.operation = operation
        connect(a, item.inputs[0])
        connect(b, item.inputs[1])
        return item.outputs[0]

    hide_web = boolean("AND", web, gate.outputs["Result"], -610, -970, "Hide inactive strings")
    hidden = boolean("OR", hidden, hide_web, -610, 140, "Filter visible paths")
    delete = node("GeometryNodeDeleteGeometry", "Reveal printed layers", -620, 400)
    delete.domain = "POINT"
    connect(inputs["Geometry"], delete.inputs["Geometry"])
    connect(hidden, delete.inputs["Selection"])
    curve = node("GeometryNodeMeshToCurve", "Extrusion paths", -400, 400)
    connect(delete.outputs["Geometry"], curve.inputs["Mesh"])
    normal = node("GeometryNodeSetCurveNormal", "Keep beads upright", -200, 400)
    normal.inputs["Mode"].default_value = "Z Up"
    connect(curve.outputs["Curve"], normal.inputs["Curve"])

    aligned = attr("fdm_aligned", "FLOAT", -1060, -150)
    staggered = attr("fdm_staggered", "FLOAT", -1060, -350)
    seam = node("GeometryNodeMenuSwitch", "Choose seam placement", -820, -180)
    seam.data_type = "FLOAT"
    seam.enum_items.clear()
    for label, value in (("Aligned Rear", aligned), ("Staggered", staggered)):
        seam.enum_items.new(label)
        connect(value, seam.inputs[label])
    connect(inputs["Seam Placement"], seam.inputs["Menu"])
    seam_menu.default_value = "Aligned Rear"
    seam_weight = math("MULTIPLY", seam.outputs["Output"], strength, -650, -350, "Mask seam buildup")
    buildup = math("MULTIPLY_ADD", seam_weight, 0.13, -570, -150, "Visible seam buildup")
    buildup.node.inputs[2].default_value = 1.0

    random = node("FunctionNodeRandomValue", "Repeatable layer variation", -1060, -610)
    random.data_type = "FLOAT"
    random.inputs["Min"].default_value = -1
    random.inputs["Max"].default_value = 1
    connect(layer, random.inputs["ID"])
    variation = math("MULTIPLY", random.outputs["Value"], strength, -820, -590, "Imperfection strength")
    width_variation = math("MULTIPLY", variation, inputs["Width Variation"], -790, -410, "Width variation weight")
    radius = math("MULTIPLY_ADD", width_variation, 0.12, -560, -450, "Bead width variation")
    radius.node.inputs[2].default_value = 1.0
    radius = math("MULTIPLY", radius, buildup, -300, -140, "Width with seam buildup")

    def vector(x, y, z, at_x, at_y, label):
        item = node("ShaderNodeCombineXYZ", label, at_x, at_y)
        for value, name in zip((x, y, z), ("X", "Y", "Z")):
            connect(value, item.inputs[name])
        return item.outputs[0]

    drift = math("MULTIPLY", variation, inputs["Layer Drift"], -790, -760, "Layer drift weight")
    shift = math("MULTIPLY", drift, inputs["Bead Width"], -560, -700, "Scale layer drift")
    shift = math("MULTIPLY", shift, 0.12, -340, -700, "Subtle layer drift")
    def choose(condition, regular, string, x, y, label):
        item = node("GeometryNodeSwitch", label, x, y)
        item.input_type = "FLOAT"
        for value, name in ((condition, "Switch"), (regular, "False"), (string, "True")):
            connect(value, item.inputs[name])
        return item.outputs["Output"]

    sag = attr("fdm_sag", "FLOAT", -610, -1240)
    droop = math("MULTIPLY", strength, inputs["Overhang Droop"], -610, -1480, "Overhang droop strength")
    bend = choose(web, droop, stringing, -610, -1660, "Independent droop and stringing")
    sag = math("MULTIPLY", sag, bend, -400, -1260, "Restrict droop to unsupported points")
    depth = choose(web, -0.45, -2.0, -400, -1060, "Overhang sag or string droop")
    sag = math("MULTIPLY", sag, depth, -180, -1110, "Sag amount")
    sag = math("MULTIPLY", sag, inputs["Layer Height"], 40, -1110, "Sag in scene units")
    translation = vector(shift, 0, sag, -120, -650, "Layer drift and sag")
    web_size = math("MULTIPLY_ADD", stringing, 0.09, 130, -870, "Fine filament size")
    web_size.node.inputs[2].default_value = 0.09
    radius = choose(web, radius, web_size, 360, -660, "Use thin beads for stringing")
    position = node("GeometryNodeSetPosition", "Layer alignment imperfections", 20, 400)
    connect(normal.outputs["Curve"], position.inputs["Geometry"])
    connect(translation, position.inputs["Offset"])

    profile = node("GeometryNodeCurvePrimitiveCircle", "Round extrusion profile", -320, -390)
    profile.inputs["Resolution"].default_value = BEAD_PROFILE_SEGMENTS
    profile.inputs["Radius"].default_value = 1
    half_width = math("MULTIPLY", inputs["Bead Width"], 0.5, -100, -380, "Bead half-width")
    half_height = math("MULTIPLY", inputs["Layer Height"], 0.54, -100, -520, "Bead half-height")
    scale = vector(half_width, half_height, 1, 120, -360, "Flatten the profile")
    transform = node("GeometryNodeTransform", "Elliptical bead", 340, -320)
    connect(profile.outputs["Curve"], transform.inputs["Geometry"])
    connect(scale, transform.inputs["Scale"])

    sweep = node("GeometryNodeCurveToMesh", "Sweep printable-looking beads", 560, 400)
    # Width changes should stay horizontal. Store the displaced center so
    # the sweep can retain droop without narrowing the layer vertically.
    center = node("GeometryNodeStoreNamedAttribute", "Capture bead center", 250, 400)
    center.data_type = "FLOAT"
    center.domain = "POINT"
    center.inputs["Name"].default_value = "fdm_bead_z"
    point = node("GeometryNodeInputPosition", "Centerline position", -80, 700)
    xyz = node("ShaderNodeSeparateXYZ", "Centerline height", 80, 700)
    connect(point.outputs["Position"], xyz.inputs["Vector"])
    connect(xyz.outputs["Z"], center.inputs["Value"])
    connect(position.outputs["Geometry"], center.inputs["Geometry"])
    connect(center.outputs["Geometry"], sweep.inputs["Curve"])
    connect(transform.outputs["Geometry"], sweep.inputs["Profile Curve"])
    connect(radius, sweep.inputs["Scale"])
    sweep.inputs["Fill Caps"].default_value = True

    height = attr("fdm_bead_z", "FLOAT", 710, 100)
    current_height = math("SUBTRACT", xyz.outputs["Z"], height, 910, 100, "Distance from bead center")
    normalized = math("DIVIDE", current_height, radius, 1110, 100, "Keep original layer height")
    correction = math("SUBTRACT", normalized, current_height, 1310, 100, "Vertical correction")
    correction = choose(web, correction, 0, 1510, 100, "Keep stringing round")
    adjust = node("GeometryNodeSetPosition", "Preserve bead height when width varies", 1720, 400)
    connect(sweep.outputs["Mesh"], adjust.inputs["Geometry"])
    connect(vector(0, 0, correction, 1720, 100, "Adjust height only"), adjust.inputs["Offset"])

    remove = node("GeometryNodeRemoveAttribute", "Remove temporary bead center", 1930, 400)
    remove.inputs["Name"].default_value = "fdm_bead_z"
    connect(adjust.outputs["Geometry"], remove.inputs["Geometry"])
    smooth = node("GeometryNodeSetShadeSmooth", "Smooth the rounded bead surfaces", 2140, 400)
    smooth.domain = "FACE"
    connect(remove.outputs["Geometry"], smooth.inputs["Geometry"])
    smooth.inputs["Shade Smooth"].default_value = True
    geometry = smooth.outputs["Geometry"]
    output = node("NodeGroupOutput", "Printed geometry", 2350, 400)
    connect(geometry, output.inputs["Geometry"])


def sockets(group):
    return {item.name: item.identifier for item in group.interface.items_tree
            if item.item_type == "SOCKET" and item.in_out == "INPUT"}


def mesh_groups(obj):
    attr = obj.data.attributes["fdm_feature"]
    indices = defaultdict(list)
    for index, item in enumerate(attr.data):
        indices[item.value].append(index)
    for feature, vertices in indices.items():
        obj.vertex_groups.new(name=FEATURE_NAMES[feature]).add(vertices, 1.0, "REPLACE")


def evaluate_bake_group(obj, modifier, group, graph):
    temp = obj.copy()
    try:
        obj.users_collection[0].objects.link(temp)
        temp_mod = temp.modifiers[modifier.name]
        inputs = modifier.properties.inputs
        values = {key: getattr(inputs, key).value
                  for name, key in sockets(group).items() if name != "Geometry"}
        temp_mod.node_group = group
        for key, value in values.items():
            getattr(temp_mod.properties.inputs, key).value = value
        graph.update()
        evaluated = temp.evaluated_get(graph)
        warnings = [warning.message for warning in evaluated.modifiers[temp_mod.name].node_warnings]
        mesh = bpy.data.meshes.new_from_object(evaluated, preserve_all_data_layers=True, depsgraph=graph)
        return mesh, warnings
    finally:
        bpy.data.objects.remove(temp, do_unlink=True)


def evaluated_fusion_paths(obj, modifier, graph):
    """Evaluate deformed centerlines for the internal bonds."""
    group = modifier.node_group.copy()
    try:
        nodes, links = group.nodes, group.links
        sweep = next(node for node in nodes if node.bl_idname == "GeometryNodeCurveToMesh")
        output = next(node for node in nodes if node.bl_idname == "NodeGroupOutput")
        curve = sweep.inputs["Curve"].links[0].from_socket
        wire = nodes.new("GeometryNodeCurveToMesh")
        links.new(curve, wire.inputs["Curve"])
        links.new(wire.outputs["Mesh"], output.inputs["Geometry"])
        mesh, _ = evaluate_bake_group(obj, modifier, group, graph)
        return mesh
    finally:
        bpy.data.node_groups.remove(group)


def integer_attribute(group, name):
    node = group.nodes.new("GeometryNodeInputNamedAttribute")
    node.data_type = "INT"
    node.inputs["Name"].default_value = name
    return node.outputs["Attribute"]


def split_faces(group, geometry, key):
    node = group.nodes.new("GeometryNodeSplitToInstances")
    node.domain = "FACE"
    group.links.new(geometry, node.inputs["Geometry"])
    group.links.new(key, node.inputs["Group ID"])
    return node.outputs["Instances"]


def boolean_union(group, *operands):
    node = group.nodes.new("GeometryNodeMeshBoolean")
    node.operation = "UNION"
    node.solver = "MANIFOLD"
    for geometry in operands:
        group.links.new(geometry, node.inputs["Mesh 2"])
    return node.outputs["Mesh"]


def strip_path_attributes(group, geometry, attributes, keep=()):
    for name in attributes:
        if name.startswith("fdm_") and name not in keep:
            node = group.nodes.new("GeometryNodeRemoveAttribute")
            node.inputs["Name"].default_value = name
            group.links.new(geometry, node.inputs["Geometry"])
            geometry = node.outputs["Geometry"]
    return geometry


def fusion_result(mesh, warnings):
    if not mesh.polygons:
        bpy.data.meshes.remove(mesh)
        raise SliceError("Could not fuse the beads. " + " ".join(warnings[:1]))
    for attribute in list(mesh.attributes):
        if attribute.name.startswith("fdm_"):
            mesh.attributes.remove(attribute)
    return mesh


def fuse_four_layers(scene, obj, core, graph, visible):
    """Use separate dependency-graph objects to evaluate each four-layer union."""
    temporary = []

    def collection(name, parent):
        result = bpy.data.collections.new(name)
        temporary.append(result)
        parent.children.link(result)
        return result

    def object_for(name, data, parent):
        result = bpy.data.objects.new(name, data)
        temporary.append(result)
        parent.objects.link(result)
        result.hide_render = True
        result.hide_set(True)
        return result

    def geometry_group(name):
        group = bpy.data.node_groups.new(name, "GeometryNodeTree")
        temporary.append(group)
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        return group, group.nodes.new("NodeGroupOutput")

    try:
        scratch = collection("FDMfier temporary fusion", scene.collection)
        batches = collection("FDMfier four-layer batches", scratch)
        empty = bpy.data.meshes.new("FDMfier temporary fusion")
        temporary.append(empty)
        bonds = object_for("FDMfier internal bonds", core, scratch) if core.polygons else None

        group, output = geometry_group("FDMfier union four layers")
        batch_input = group.interface.new_socket(name="Batch", in_out="INPUT", socket_type="NodeSocketInt")
        batch = group.nodes.new("NodeGroupInput").outputs["Batch"]
        operands = []
        sources = [(obj, "fdm_path", "fdm_layer", 4)]
        if bonds is not None:
            sources.append((bonds, "fdm_bond_batch", "fdm_bond_batch", 12))
        nodes, link = group.nodes, group.links.new
        for source, path, layer, divisor in sources:
            info = nodes.new("GeometryNodeObjectInfo")
            info.transform_space = "ORIGINAL"
            info.inputs["Object"].default_value = source

            divide = nodes.new("FunctionNodeIntegerMath")
            divide.operation = "DIVIDE_FLOOR"
            divide.inputs[1].default_value = divisor
            link(integer_attribute(group, layer), divide.inputs[0])
            compare = nodes.new("FunctionNodeCompare")
            compare.data_type = "INT"
            compare.operation = "EQUAL"
            link(divide.outputs[0], compare.inputs["A"])
            link(batch, compare.inputs["B"])

            select = nodes.new("GeometryNodeSeparateGeometry")
            select.domain = "FACE"
            link(info.outputs["Geometry"], select.inputs["Geometry"])
            link(compare.outputs["Result"], select.inputs["Selection"])
            geometry = strip_path_attributes(group, select.outputs["Selection"],
                                             source.data.attributes.keys(), keep=(path,))
            operands.append(split_faces(group, geometry, integer_attribute(group, path)))
        link(boolean_union(group, *operands), output.inputs["Geometry"])

        workers = []
        for index in range((visible + 3) // 4):
            worker = object_for("FDMfier layer batch", empty, batches)
            modifier = worker.modifiers.new("Fuse four layers", "NODES")
            modifier.node_group = group
            getattr(modifier.properties.inputs, batch_input.identifier).value = index
            workers.append((worker, modifier.name))

        group, output = geometry_group("FDMfier join layer batches")
        info = group.nodes.new("GeometryNodeCollectionInfo")
        info.transform_space = "ORIGINAL"
        info.inputs["Collection"].default_value = batches
        info.inputs["Separate Children"].default_value = True
        group.links.new(boolean_union(group, info.outputs["Instances"]), output.inputs["Geometry"])
        joined = object_for("FDMfier fused batches", empty, scratch)
        modifier = joined.modifiers.new("Join layer batches", "NODES")
        modifier.node_group = group
        joined.hide_set(False)
        graph.update()

        for worker, modifier_name in workers:
            warnings = worker.evaluated_get(graph).modifiers[modifier_name].node_warnings
            if warnings:
                raise SliceError(f"Could not fuse a four-layer batch. {warnings[0].message}")
        evaluated = joined.evaluated_get(graph)
        warnings = [warning.message for warning in evaluated.modifiers[modifier.name].node_warnings]
        return fusion_result(bpy.data.meshes.new_from_object(
            evaluated, preserve_all_data_layers=True, depsgraph=graph,
        ), warnings)
    finally:
        bpy.data.batch_remove(ids=temporary)


def fuse_preview(obj, modifier, core, graph):
    """Boolean union individual solid beads and the internal bonding prisms."""
    group = modifier.node_group.copy()
    core_obj = None
    try:
        nodes, links = group.nodes, group.links
        output = next(node for node in nodes if node.bl_idname == "NodeGroupOutput")
        beads = output.inputs["Geometry"].links[0].from_socket
        beads = strip_path_attributes(group, beads, obj.data.attributes.keys(), keep=("fdm_path",))
        operands = [split_faces(group, beads, integer_attribute(group, "fdm_path"))]
        if core.polygons:
            core_obj = bpy.data.objects.new("FDMfier internal bonds", core)
            obj.users_collection[0].objects.link(core_obj)
            core_obj.matrix_world = obj.matrix_world.copy()
            core_obj.hide_render = True
            core_obj.hide_set(True)
            info = nodes.new("GeometryNodeObjectInfo")
            info.transform_space = "RELATIVE"
            info.inputs["Object"].default_value = core_obj
            operands.append(split_faces(group, info.outputs["Geometry"], integer_attribute(group, "fdm_bond_batch")))
        links.new(boolean_union(group, *operands), output.inputs["Geometry"])
        return fusion_result(*evaluate_bake_group(obj, modifier, group, graph))
    finally:
        bpy.data.node_groups.remove(group)
        if core_obj is not None:
            bpy.data.objects.remove(core_obj, do_unlink=True)
