"""Run with FDMfier enabled to check the 3D infill and surface generators."""

import importlib
from math import dist
from types import SimpleNamespace

import bmesh
import bpy

package = next(name for name in bpy.context.preferences.addons.keys() if name.endswith(".FDMfier"))
addon = importlib.import_module(package)
slicer = addon.slicer
patterns = importlib.import_module(package + ".patterns")
source = bpy.context.object
settings = source.fdm_print
settings.size = 10
settings.layer_height = 1
settings.width = 0.6
settings.skins = settings.bottom_layers = 1
config = SimpleNamespace(**{name: getattr(settings, name) for name in addon.SETTING_NAMES})
vertices, triangles, _, _ = addon.evaluated_input(bpy.context, source, config)
infill_patterns = ("GYROID", "CUBIC", "CROSS_3D", "ADAPTIVE_CUBIC")
surface_patterns = ("HILBERT", "ARCHIMEDEAN", "OCTAGRAM")

# Empty and fully solid infill retain the established path geometry.
for density in (0, 100):
    config.pattern, config.density = "GRID", density
    reference = slicer.generate(vertices, triangles, config)
    for pattern in infill_patterns:
        config.pattern = pattern
        assert slicer.generate(vertices, triangles, config) == reference

width = slicer.units(0.45)
side = slicer.units(30)
region = [[(0, 0), (side, 0), (side, side), (0, side)]]
area = slicer.bounds(region)

# Density must change path length, while height changes the 3D cross section.
for pattern in ("GYROID", "CUBIC", "CROSS_3D"):
    lengths = []
    for density in (0.05, 0.15, 0.5):
        paths = list(slicer.fill_paths(region, pattern, density, width, 0, slicer.units(0.3)))
        length = sum(sum(dist(a, b) for a, b in zip(line, line[1:] + ([line[0]] if closed else [])))
                     for line, closed in paths)
        lengths.append(length)
        assert paths == list(slicer.fill_paths(region, pattern, density, width, 0, slicer.units(0.3)))
    assert lengths[0] < lengths[1] < lengths[2], (pattern, lengths)
    assert paths != list(slicer.fill_paths(region, pattern, 0.5, width, 1, slicer.units(0.3)))

# Rounded tracks should cover the solid skin, including inside maze corners.
for pattern in surface_patterns:
    lines = list(slicer.fill_paths(region, pattern, 1, width, 0, pattern_bounds=area))
    strokes = slicer.clip.PyclipperOffset(2, 20)
    for line, closed in lines:
        strokes.AddPath(line, slicer.clip.JT_ROUND,
                       slicer.clip.ET_CLOSEDLINE if closed else slicer.clip.ET_OPENROUND)
    covered = strokes.Execute(round(width * 0.515))
    interior = slicer.offset(region, -width)
    uncovered = slicer.polygon_op(interior, covered, slicer.clip.CT_DIFFERENCE)
    assert sum(slicer.clip.Area(p) for p in uncovered) / sum(slicer.clip.Area(p) for p in interior) < 0.001

# Large interiors get coarser octree cells, while surface cells stay fine.
large_cube = [tuple(vertex.co * 50) for vertex in source.data.vertices]
source_triangles = [(face.vertices[0], face.vertices[i], face.vertices[i + 1])
                    for face in source.data.polygons for i in range(1, len(face.vertices) - 1)]
adaptive = patterns.AdaptiveCubic(large_cube, source_triangles, width, 0.15)
list(adaptive.build_steps())
spans = {span for low, span in adaptive.leaves}
assert 1 in spans and max(spans) > 1
lines = adaptive.lines(slicer.units(3.15))
assert lines and lines == adaptive.lines(slicer.units(3.15))

# Both fusion methods must retain closed geometry with the new path types.
settings.density = 15
settings.fuse_bake = True
for method in ("V1", "BATCHES"):
    settings.fusion_method = method
    for index, pattern in enumerate(infill_patterns):
        settings.pattern = pattern
        settings.top_pattern = surface_patterns[index % 3]
        settings.bottom_pattern = surface_patterns[(index + 2) % 3]
        assert bpy.ops.fdm_print.generate() == {"FINISHED"}
        live = bpy.context.object
        modifier = addon.node_modifier(live)
        sockets = addon.preview.sockets(modifier.node_group)
        getattr(modifier.properties.inputs, sockets["Imperfections"]).value = 0.25
        assert bpy.ops.fdm_print.bake() == {"FINISHED"}
        baked = bpy.context.object
        mesh = bmesh.new()
        try:
            mesh.from_mesh(baked.data)
            assert all(edge.is_manifold and edge.is_contiguous for edge in mesh.edges), (method, pattern)
            assert mesh.calc_volume(signed=True) > 0
        finally:
            mesh.free()
        assert baked.matrix_world == live.matrix_world
        assert live.hide_get() and source.hide_get()
        assert bpy.ops.fdm_print.show() == {"FINISHED"}
        assert bpy.ops.fdm_print.remove() == {"FINISHED"}

print("FDM infill and surface tests passed")
