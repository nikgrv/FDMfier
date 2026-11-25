# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""Mesh buffers and cleanup for Joined Profiles."""

from array import array

import bpy


class SurfaceMesh:
    """Store coordinates and connectivity in dense buffers for large prints."""

    def __init__(self):
        self.points = array("f")
        self.corners = array("i")
        self.starts = array("i")
        self.anchors = array("b")

    def write(self, mesh):
        mesh.vertices.add(len(self.points) // 3)
        mesh.vertices.foreach_set("co", self.points)
        mesh.loops.add(len(self.corners))
        mesh.loops.foreach_set("vertex_index", self.corners)
        mesh.polygons.add(len(self.starts))
        mesh.polygons.foreach_set("loop_start", self.starts)
        mesh.update(calc_edges=True)
        if self.anchors:
            attr = mesh.attributes.new("fdm_solid_anchor", "BOOLEAN", "POINT")
            attr.data.foreach_set("value", self.anchors)


def cleanup_nodes():
    """Discard detached fill and air pockets far smaller than a bead."""
    existing = next((group for group in bpy.data.node_groups if group.get("fdm_anchor_cleanup") == 3), None)
    if existing is not None:
        return existing
    group = bpy.data.node_groups.new("FDMfier Remove Detached Infill", "GeometryNodeTree")
    group["fdm_anchor_cleanup"] = 3
    for direction in ("INPUT", "OUTPUT"):
        group.interface.new_socket(name="Geometry", in_out=direction, socket_type="NodeSocketGeometry")
    group.interface.new_socket(name="Minimum Void", in_out="INPUT", socket_type="NodeSocketFloat")
    nodes, link = group.nodes, group.links.new
    source = nodes.new("NodeGroupInput")
    output = nodes.new("NodeGroupOutput")
    island = nodes.new("GeometryNodeInputMeshIsland").outputs["Island Index"]
    anchor = nodes.new("GeometryNodeInputNamedAttribute")
    anchor.data_type = "BOOLEAN"
    anchor.inputs["Name"].default_value = "fdm_solid_anchor"
    anchors = nodes.new("GeometryNodeAccumulateField")
    anchors.data_type, anchors.domain = "INT", "POINT"
    link(anchor.outputs["Attribute"], anchors.inputs["Value"])
    link(island, anchors.inputs["Group ID"])
    unanchored = nodes.new("FunctionNodeCompare")
    unanchored.data_type, unanchored.operation = "INT", "EQUAL"
    link(anchors.outputs["Total"], unanchored.inputs["A"])

    # The integral of normal · position over a closed boundary is three
    # times signed volume. Air cavities have inward winding and stay intact.
    dot = nodes.new("ShaderNodeVectorMath")
    dot.operation = "DOT_PRODUCT"
    link(nodes.new("GeometryNodeInputPosition").outputs["Position"], dot.inputs[0])
    link(nodes.new("GeometryNodeInputNormal").outputs["Normal"], dot.inputs[1])
    area = nodes.new("GeometryNodeInputMeshFaceArea").outputs["Area"]
    flux = nodes.new("ShaderNodeMath")
    flux.operation = "MULTIPLY"
    link(dot.outputs["Value"], flux.inputs[0])
    link(area, flux.inputs[1])
    volume = nodes.new("GeometryNodeAccumulateField")
    volume.data_type, volume.domain = "FLOAT", "FACE"
    link(flux.outputs[0], volume.inputs["Value"])
    link(island, volume.inputs["Group ID"])
    positive = nodes.new("ShaderNodeMath")
    positive.operation = "GREATER_THAN"
    link(volume.outputs["Total"], positive.inputs[0])
    positive.inputs[1].default_value = 0
    tiny = nodes.new("ShaderNodeMath")
    tiny.operation = "GREATER_THAN"
    link(volume.outputs["Total"], tiny.inputs[0])
    negative_limit = nodes.new("ShaderNodeMath")
    negative_limit.operation = "MULTIPLY"
    negative_limit.inputs[1].default_value = -3
    link(source.outputs["Minimum Void"], negative_limit.inputs[0])
    link(negative_limit.outputs[0], tiny.inputs[1])
    negative = nodes.new("ShaderNodeMath")
    negative.operation = "LESS_THAN"
    link(volume.outputs["Total"], negative.inputs[0])
    def boolean(operation, a, b):
        node = nodes.new("FunctionNodeBooleanMath")
        node.operation = operation
        link(a, node.inputs[0])
        link(b, node.inputs[1])
        return node.outputs[0]

    island_area = nodes.new("GeometryNodeAccumulateField")
    island_area.data_type, island_area.domain = "FLOAT", "FACE"
    link(area, island_area.inputs["Value"])
    link(island, island_area.inputs["Group ID"])
    # Signed volume alone is unreliable on nearly flat or partially revealed
    # components. A tiny air pocket must also have a small surface area.
    area_limit = nodes.new("ShaderNodeMath")
    area_limit.operation = "POWER"
    link(source.outputs["Minimum Void"], area_limit.inputs[0])
    area_limit.inputs[1].default_value = 2 / 3
    scale = nodes.new("ShaderNodeMath")
    scale.operation = "MULTIPLY"
    link(area_limit.outputs[0], scale.inputs[0])
    scale.inputs[1].default_value = 24
    small_area = nodes.new("ShaderNodeMath")
    small_area.operation = "LESS_THAN"
    link(island_area.outputs["Total"], small_area.inputs[0])
    link(scale.outputs[0], small_area.inputs[1])
    tiny_void = boolean("AND", negative.outputs[0], boolean("AND", tiny.outputs[0], small_area.outputs[0]))
    detached = boolean("AND", unanchored.outputs["Result"], positive.outputs[0])
    remove = boolean("OR", tiny_void, detached)
    delete = nodes.new("GeometryNodeDeleteGeometry")
    delete.domain = "POINT"
    link(remove, delete.inputs["Selection"])
    link(source.outputs["Geometry"], delete.inputs["Geometry"])
    strip = nodes.new("GeometryNodeRemoveAttribute")
    strip.inputs["Name"].default_value = "fdm_solid_anchor"
    link(delete.outputs["Geometry"], strip.inputs["Geometry"])
    link(strip.outputs["Geometry"], output.inputs["Geometry"])
    return group
