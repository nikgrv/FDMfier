# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""Common Geometry Nodes socket helpers."""

def sockets(group):
    return {item.name: item.identifier for item in group.interface.items_tree
            if item.item_type == "SOCKET" and item.in_out == "INPUT"}


def integer_attribute(group, name):
    node = group.nodes.new("GeometryNodeInputNamedAttribute")
    node.data_type = "INT"
    node.inputs["Name"].default_value = name
    return node.outputs["Attribute"]
