"""Exercise the Benchy hull and the 250-layer reveal with a local 3DBenchy STL."""
import importlib
import sys
from time import perf_counter

import bpy
import numpy as np
from mathutils import Vector
from mathutils.bvhtree import BVHTree

addon = importlib.import_module(next(n for n in bpy.context.preferences.addons.keys() if n.endswith(".FDMfier")))
stl = sys.argv[sys.argv.index("--") + 1]
bpy.ops.wm.stl_import(filepath=stl)
source = bpy.context.object
settings = source.fdm_print
axis = Vector((0, 1, 0))
source_tree = BVHTree.FromObject(source, bpy.context.evaluated_depsgraph_get())


def generate(pattern, size, layers):
    settings.pattern = pattern
    settings.size = size
    settings.layer_height = size * source.dimensions.z / max(source.dimensions) / (layers - 0.2)
    start = perf_counter()
    assert bpy.ops.fdm_print.generate() == {"FINISHED"}
    obj = bpy.context.object
    assert obj["fdm_layers"] == layers
    print(f"{pattern}: generated {layers} layers in {perf_counter() - start:.1f}s", flush=True)
    modifier = addon.node_modifier(obj)
    key = addon.preview.sockets(modifier.node_group)["Visible Layers"]
    return obj, getattr(modifier.properties.inputs, key)


def assert_hull(obj, z_values):
    graph = bpy.context.evaluated_depsgraph_get()
    print_tree = BVHTree.FromObject(obj, graph)
    world_to_source = source.matrix_world.inverted()
    checked = 0
    for z in z_values:
        for x in (-15, 0, 10):
            ray = Vector((x, -40, z))
            expected = source_tree.ray_cast(world_to_source @ (obj.matrix_world @ ray), axis)[3]
            if expected is not None:
                observed = print_tree.ray_cast(ray, axis)[3]
                tolerance = 1.25 * settings.width * max(source.dimensions) / settings.size
                assert observed is not None and abs(expected - observed) < tolerance, (settings.pattern, x, z)
                checked += 1
    assert checked >= 5


if "--all-patterns" in sys.argv:
    for item in addon.FDMSettings.bl_rna.properties["pattern"].enum_items:
        preview, visible = generate(item.identifier, size=18, layers=48)
        assert_hull(preview, (1.2, 2.2, 3.2, 4.2))
        visible.value = 5
        assert_hull(preview, (1.2, 2.2, 3.2, 4.2))
        assert bpy.ops.fdm_print.remove() == {"FINISHED"}
else:
    preview, visible = generate("GYROID", size=62, layers=250)
    data = preview.data
    max_height = 0
    full_faces = None
    for shown in (0, 1, 4, 25, 100, 180, 241, 242, 243, 250, 250):
        visible.value = shown
        mesh = preview.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
        assert preview.data == data
        if not shown:
            assert not mesh.vertices
            continue
        assert len(mesh.polygons) > 1000
        coordinates = np.empty(len(mesh.vertices) * 3, np.float32)
        mesh.vertices.foreach_get("co", coordinates)
        limit = source.dimensions.z * shown / 250
        assert max_height <= coordinates[2::3].max() <= limit + 0.25
        max_height = coordinates[2::3].max()
        edge_indices = np.empty(len(mesh.loops), np.int32)
        mesh.loops.foreach_get("edge_index", edge_indices)
        bad = np.count_nonzero(np.bincount(edge_indices, minlength=len(mesh.edges)) != 2)
        print(f"{shown} visible layers: {len(mesh.polygons):,} faces, {bad} non-manifold edges", flush=True)
        assert not bad
        if shown == 250:
            if full_faces is not None:
                assert len(mesh.polygons) == full_faces
            full_faces = len(mesh.polygons)
    visible.value = 0
    assert bpy.ops.fdm_print.remove() == {"FINISHED"}
    assert source == bpy.context.object and not source.hide_get()

print("3DBenchy regression passed", flush=True)
