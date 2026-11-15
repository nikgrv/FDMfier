"""Run with the built extension enabled in a clean Blender profile."""

import importlib
import math
import tempfile
from types import SimpleNamespace
from pathlib import Path

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

addon_name = next(name for name in bpy.context.preferences.addons.keys()
                  if name.endswith(".FDMfier"))
addon = importlib.import_module(addon_name)
preview, slicer = addon.preview, addon.slicer


def mesh_object(name, vertices, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    activate(obj)
    return obj


def activate(obj):
    bpy.context.view_layer.update()
    for selected in bpy.context.selected_objects:
        selected.select_set(False)
    obj.hide_set(False)
    obj.select_set(True)
    bpy.context.view_layer.objects.active = obj


def annulus():
    n = 40
    vertices = [(r * math.cos(i * math.tau / n), r * math.sin(i * math.tau / n), z)
                for z in (0, 8) for r in (6, 3) for i in range(n)]
    faces = []
    for i in range(n):
        j = (i + 1) % n
        faces.extend(((i, j, j + 2*n, i + 2*n),
                      (j + n, i + n, i + 3*n, j + 3*n),
                      (j, i, i + n, j + n),
                      (i + 2*n, j + 2*n, j + 3*n, i + 3*n)))
    return mesh_object("Hollow cylinder", vertices, faces)


def solid_cells(name, cells):
    vertices, indices, faces = [], {}, []
    sides = (
        ((-1, 0, 0), ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0))),
        ((1, 0, 0), ((1, 0, 0), (1, 1, 0), (1, 1, 1), (1, 0, 1))),
        ((0, -1, 0), ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1))),
        ((0, 1, 0), ((0, 1, 0), (0, 1, 1), (1, 1, 1), (1, 1, 0))),
        ((0, 0, -1), ((0, 0, 0), (0, 1, 0), (1, 1, 0), (1, 0, 0))),
        ((0, 0, 1), ((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1))),
    )
    for cell in sorted(cells):
        for normal, corners in sides:
            if tuple(a + b for a, b in zip(cell, normal)) in cells:
                continue
            face = []
            for corner in corners:
                point = tuple(a + b for a, b in zip(cell, corner))
                if point not in indices:
                    indices[point] = len(vertices)
                    vertices.append(point)
                face.append(indices[point])
            faces.append(face)
    return mesh_object(name, vertices, faces)


def slice_object(obj, **settings):
    activate(obj)
    obj.fdm_print.size = 24
    obj.fdm_print.layer_height = 0.4
    obj.fdm_print.width = 0.6
    for key, value in settings.items():
        setattr(obj.fdm_print, key, value)
    config = SimpleNamespace(**{name: getattr(obj.fdm_print, name) for name in addon.SETTING_NAMES})
    vertices, faces, _, _ = addon.evaluated_input(bpy.context, obj, config)
    return slicer.generate(vertices, faces, config)


def live(obj, name):
    modifier = addon.node_modifier(obj)
    return getattr(modifier.properties.inputs, preview.sockets(modifier.node_group)[name])


def evaluated(obj):
    bpy.context.view_layer.update()
    return obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data


def coordinates(obj):
    return tuple(tuple(v.co) for v in evaluated(obj).vertices)


cube = bpy.context.object
assert cube.fdm_print.density == 15
assert cube.fdm_print.fusion_method == "V1"
assert set(addon.FDMSettings.bl_rna.properties["fusion_method"].enum_items.keys()) == {"V1", "BATCHES"}
original = coordinates(cube)
for density in (0, 15, 100):
    paths, layers, narrow = slice_object(cube, density=density, skirt=True)
    assert layers == 60 and not narrow
    assert sum(p.layer == 15 and p.feature == slicer.WALL for p in paths) == 2
    fill = sum(p.layer == 15 and p.feature == slicer.INFILL for p in paths)
    assert (fill == 0) == (density == 0)
    if density == 15:
        sparse_fill = fill
    if density == 100:
        assert fill > sparse_fill
    assert sum(p.layer == 0 and p.feature == slicer.INFILL for p in paths) > 20
    skirts = [p for p in paths if p.feature == slicer.SKIRT]
    assert len(skirts) == 2 and all(p.layer == 0 for p in skirts)
    assert min(p[0] for p in skirts[0].points) == -slicer.units(15.3)

paths, _, _ = slice_object(cube, brim=True, brim_width=3, skirt=True)
brim = [p for p in paths if p.feature == slicer.BRIM]
skirts = [p for p in paths if p.feature == slicer.SKIRT]
assert len(brim) == 6 and all(p.layer == 0 and p.closed for p in brim)
assert min(x for x, _ in brim[0].points) == -slicer.units(12.276)
assert min(x for x, _ in skirts[0].points) < min(x for x, _ in brim[-1].points) - slicer.units(3)
cube.fdm_print.brim = False

# Two closed solids straddle the coordinate grid at a gap narrower than
# one Clipper unit. Their sliced endpoints round to identical positions.
vertices = [tuple(vertex.co) for vertex in cube.data.vertices]
faces = [tuple(face.vertices) for face in cube.data.polygons]
shifted = [(x + 2.000001, y, z) for x, y, z in vertices]
close_shells = mesh_object("Near-contact shells", vertices + shifted,
                           faces + [tuple(i + len(vertices) for i in face) for face in faces])
paths, _, _ = slice_object(close_shells)
assert any(path.wall for path in paths)


tube = annulus()
for pattern in ("LINES", "GRID", "TRIANGLES", "CONCENTRIC", "HONEYCOMB", "PARALLEL"):
    paths, _, _ = slice_object(tube, pattern=pattern)
    assert sum(p.layer == 8 and p.wall for p in paths) == 4
    for path in paths:
        if path.feature == slicer.WEB:
            continue
        for a, b in zip(path.points, path.points[1:]):
            assert math.hypot((a[0] + b[0])/2, (a[1] + b[1])/2) > slicer.units(5.7)

assert any(path.feature == slicer.WEB for path in paths)
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
tube_preview = bpy.context.object
live(tube_preview, "Imperfections").value = 0
assert slicer.WEB not in {v.value for v in evaluated(tube_preview).attributes["fdm_feature"].data}
live(tube_preview, "Imperfections").value = 1
assert slicer.WEB in {v.value for v in evaluated(tube_preview).attributes["fdm_feature"].data}
live(tube_preview, "Visible Layers").value = 1
assert slicer.WEB not in {v.value for v in evaluated(tube_preview).attributes["fdm_feature"].data}
assert bpy.ops.fdm_print.remove() == {"FINISHED"}

islands = solid_cells("Disconnected", {(0, 0, 0), (2, 0, 0)})
paths, _, _ = slice_object(islands)
assert sum(p.layer == 3 and p.wall for p in paths) == 4

step = solid_cells("Terrace", {(x, y, 0) for x in range(3) for y in range(2)} | {(0, 0, 1)})
paths, _, _ = slice_object(step, density=0)
assert any(p.layer == 19 and p.feature == slicer.INFILL for p in paths)
assert not any(p.layer == 10 and p.feature == slicer.INFILL for p in paths)

beam = solid_cells("Cantilever", {(0, y, z) for y in range(2) for z in range(3)} |
                   {(x, y, 3) for x in range(4) for y in range(2)})
support_results = []
for mode in ("OFF", "STANDARD", "TREE"):
    paths, _, _ = slice_object(beam, supports=mode)
    support = [p for p in paths if p.feature == slicer.SUPPORT]
    assert bool(support) == (mode != "OFF")
    if support:
        assert min(p.layer for p in support) == 0
        assert max(p.layer for p in support) == 43
    support_results.append(support)
assert support_results[1] != support_results[2]
# Sample multiple tips on one roof so tree routes merge before reaching the bed.
support_width = slicer.units(0.4)
support_height = slicer.units(0.2)
roof = [[(-1000, -1000), (25000, -1000), (25000, 1000), (-1000, 1000)]]
support_layers = [[] for _ in range(62)] + [roof]
tree = list(slicer.support_paths(support_layers, support_width, support_height, True, 45,
                                 lambda: False, lambda *_: None, {}))
trunk_widths = {}
for path in tree:
    if path.closed:
        x0, _, x1, _ = slicer.bounds([path.points])
        trunk_widths[path.layer] = max(trunk_widths.get(path.layer, 0), x1 - x0)
assert trunk_widths[0] > trunk_widths[60]
# After tips have converged, consecutive sections widen by at most the taper rate.
assert all(0 <= trunk_widths[i - 1] - trunk_widths[i] <= 2 * support_height * 0.18 + 2
           for i in range(1, 30))

for mode in ("STANDARD", "TREE"):
    paths, _, _ = slice_object(beam, supports=mode, support_angle=90)
    assert any(p.feature == slicer.SUPPORT and p.layer == 43 for p in paths)
# A roof over a broad lower shelf has no unobstructed route to the bed.
shelf = [[(-300000, -300000), (300000, -300000), (300000, 300000), (-300000, 300000)]]
blocked_layers = [shelf] * 3 + [[] for _ in range(12)] + [roof]
for tree_mode in (False, True):
    args = (blocked_layers, support_width, support_height, tree_mode, 45,
            lambda: False, lambda *_: None, {})
    assert list(slicer.support_paths(*args))
    assert not list(slicer.support_paths(*args, build_plate_only=True))
    assert any(path.layer == 0 for path in slicer.support_paths(
        support_layers, support_width, support_height, tree_mode, 45,
        lambda: False, lambda *_: None, {}, build_plate_only=True))
beam.fdm_print.support_angle = 45
paths, _, _ = slice_object(beam)
assert any(sag > 0 for path in paths if path.wall and path.layer == 45
           for *_, sag in preview.path_points(path, slicer.units(0.6)))
assert all(sag == 0 for path in paths if path.wall and path.layer == 10
           for *_, sag in preview.path_points(path, slicer.units(0.6)))

activate(cube)
cube.fdm_print.density = 15
cube.fdm_print.skirt = False
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
obj = bpy.context.object
assert obj.fdm_print.source == cube and coordinates(cube) == original
assert max(abs(a - b) for a, b in zip(obj.dimensions, cube.dimensions)) < 0.06
estimate = addon.estimate_vertices(cube, cube.fdm_print)
assert estimate[0] == 60 and estimate[1] > 1000
full = len(evaluated(obj).polygons)
live(obj, "Visible Layers").value = 10
partial = len(evaluated(obj).polygons)
assert 0 < partial < full
before = coordinates(obj)
live(obj, "Imperfections").value = 1
assert coordinates(obj) != before
before = coordinates(obj)
live(obj, "Seam Placement").value = "Staggered"
assert coordinates(obj) != before
assert bpy.ops.fdm_print.bake() == {"FINISHED"}
baked = bpy.context.object
assert baked != obj and not baked.modifiers and len(baked.data.polygons) == partial
assert cube.hide_get() and cube.hide_render
assert (obj.location - Vector((0, 0, -1))).length < 1e-6
assert baked.matrix_world == obj.matrix_world
assert obj.hide_get() and obj.hide_render and not addon.node_modifier(obj).show_viewport

with tempfile.TemporaryDirectory(prefix="fdm-smoke-") as directory:
    path = str(Path(directory) / "preview.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    cube = bpy.data.objects["Cube"]
    obj = addon.existing_preview(bpy.context.scene, cube)
    assert live(obj, "Visible Layers").value == 10
    assert live(obj, "Seam Placement").value == "Staggered"
    assert obj.hide_get() and obj.hide_render
    assert bpy.ops.fdm_print.show() == {"FINISHED"}
    assert not obj.hide_get() and not obj.hide_render
    assert len(evaluated(obj).polygons) == partial
    activate(obj)
    assert bpy.ops.fdm_print.generate() == {"FINISHED"}
    obj = bpy.context.object
    assert live(obj, "Visible Layers").value == 10
    assert live(obj, "Imperfections").value == 1
    assert live(obj, "Seam Placement").value == "Staggered"

    # Create invalid evaluated input without altering the original mesh data.
    mod = cube.modifiers.new("Open source for regression check", "MASK")
    try:
        bpy.ops.fdm_print.generate()
    except RuntimeError as error:
        assert "solid faces" in str(error)
    else:
        raise AssertionError("Expected generation to report invalid input")
    assert addon.existing_preview(bpy.context.scene, cube) == obj
    cube.modifiers.remove(mod)

    # Check the modal Esc dispatch without requiring a desktop event loop.
    calls = []
    stub = SimpleNamespace(cancel=lambda context: calls.append("cancel"),
                           report=lambda kinds, message: calls.append(message))
    assert addon.FDM_OT_generate.modal(stub, bpy.context, SimpleNamespace(type="ESC")) == {"CANCELLED"}
    assert calls == ["cancel", "Print preview generation cancelled."]
    config = SimpleNamespace(**{name: getattr(cube.fdm_print, name) for name in addon.SETTING_NAMES})
    vertices, faces, _, _ = addon.evaluated_input(bpy.context, cube, config)
    try:
        slicer.generate(vertices, faces, config, cancelled=lambda: True)
    except slicer.SliceError as error:
        assert "cancelled" in str(error)
    else:
        raise AssertionError("Expected cancelled slicing to stop")
    assert addon.existing_preview(bpy.context.scene, cube) == obj

    scene = bpy.context.scene
    for other in scene.objects:
        if other.type == "MESH" and other != obj:
            other.hide_render = True
    scene.render.engine = "CYCLES"
    scene.cycles.device = "CPU"
    scene.cycles.samples = 2
    scene.render.resolution_x = scene.render.resolution_y = 96
    scene.render.resolution_percentage = 100
    camera = scene.camera
    target = obj.location + Vector((0, 0, 0.6))
    camera.location = target + Vector((3, -4, 2.5))
    camera.rotation_euler = (target - camera.location).to_track_quat("-Z", "Y").to_euler()
    camera.data.clip_start = 0.0001
    camera.data.type = "ORTHO"
    camera.data.ortho_scale = 2.9
    scene.world.node_tree.nodes["Background"].inputs[0].default_value = (0.6, 0.6, 0.6, 1)
    scene.render.filepath = str(Path(directory) / "partial.png")
    bpy.ops.render.render(write_still=True)
    def render_pixels():
        image = bpy.data.images.load(scene.render.filepath)
        pixels = tuple(image.pixels)
        bpy.data.images.remove(image)
        return pixels

    first = render_pixels()
    live(obj, "Visible Layers").value = obj["fdm_layers"]
    scene.render.filepath = str(Path(directory) / "full.png")
    bpy.ops.render.render(write_still=True)
    assert first != render_pixels()

    activate(obj)
    assert bpy.ops.fdm_print.remove() == {"FINISHED"}
    assert addon.existing_preview(scene, cube) is None
    assert bpy.data.objects.get("Cube • Baked FDM") is not None

# A closed copy is generated from a box with a missing face.
opened = mesh_object("Open cube", [tuple(vertex.co) for vertex in cube.data.vertices],
                     [tuple(face.vertices) for face in list(cube.data.polygons)[:-1]])
assert bpy.ops.fdm_print.repair() == {"FINISHED"}
repaired = bpy.context.object
assert repaired != opened and len(repaired.data.polygons) == 6 and len(opened.data.polygons) == 5
assert opened.hide_get() and opened.hide_render

# Independent skins and an imperfection mask transferred from the source.
paths, _, _ = slice_object(repaired, skins=2, bottom_layers=0, density=0, top_pattern="CONCENTRIC")
assert not any(path.feature == slicer.INFILL and path.layer == 0 for path in paths)
assert any(path.feature == slicer.INFILL and path.closed and path.layer == 59 for path in paths)
group = repaired.vertex_groups.new(name="Painted")
group.add([vertex.index for vertex in repaired.data.vertices if vertex.co.x > 0], 1.0, "REPLACE")
repaired.fdm_print.weight_group = group.name
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
masked = bpy.context.object
mask = [value.value for value in masked.data.attributes["fdm_mask"].data]
assert min(mask) == 0 and max(mask) == 1 and any(0 < value < 1 for value in mask)
repaired.fdm_print.invert_weight_group = True
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
masked = bpy.context.object
inverted = [value.value for value in masked.data.attributes["fdm_mask"].data]
assert all(abs(a + b - 1) < 1e-6 for a, b in zip(mask, inverted))
assert not evaluated(masked).materials
live(masked, "Imperfections").value = 0
baseline = coordinates(masked)
for name in preview.EFFECTS:
    live(masked, name).value = 0
live(masked, "Imperfections").value = 1
assert all(abs(a[2] - b[2]) < 1e-6 for a, b in zip(coordinates(masked), baseline))
live(masked, "Overhang Droop").value = 1
assert all(abs(a[2] - b[2]) < 1e-6 for a, b in zip(coordinates(masked), baseline))  # Painting a supported wall cannot make it droop.
seam_only = coordinates(masked)
live(masked, "Width Variation").value = 1
assert coordinates(masked) != seam_only

beam = bpy.data.objects["Cantilever"]
activate(beam)
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
overhang = bpy.context.object
live(overhang, "Imperfections").value = 1
for name in preview.EFFECTS:
    live(overhang, name).value = 0
baseline = coordinates(overhang)
live(overhang, "Overhang Droop").value = 1
assert any(a[2] != b[2] for a, b in zip(coordinates(overhang), baseline))

# An extra face attached to an existing edge forces the voxel fallback.
vertices = [tuple(vertex.co) for vertex in cube.data.vertices]
faces = [tuple(face.vertices) for face in cube.data.polygons]
a, b = faces[0][:2]
midpoint = tuple((x + y) / 2 for x, y in zip(vertices[a], vertices[b]))
dirty = mesh_object("Non-manifold cube", vertices + [midpoint],
                    faces + [(a, b, len(vertices))])
assert bpy.ops.fdm_print.repair() == {"FINISHED"}
assert bpy.context.object != dirty and len(bpy.context.object.data.polygons) > 6

# Lay a rotated and non-uniformly scaled mesh on one face, then two
# coplanar triangles. Both operations preserve the mesh and Edit Mode.
orientable = mesh_object("Orientable", [tuple(vertex.co) for vertex in cube.data.vertices],
                         [tuple(face.vertices) for face in cube.data.polygons])
orientable.scale = (2, 1, 0.3)
for triangulate in (False, True):
    orientable.location = (1, 2, 4)
    orientable.rotation_euler = (0.6, -0.4, 0.8)
    bpy.context.view_layer.update()
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.context.tool_settings.mesh_select_mode = (False, False, True)
    bpy.ops.mesh.select_all(action="DESELECT")
    assert bpy.ops.fdm_print.lay_on_face() == {"CANCELLED"}
    bm = bmesh.from_edit_mesh(orientable.data)
    side = next(face for face in bm.faces if face.normal.x > 0.99)
    faces = bmesh.ops.triangulate(bm, faces=[side])["faces"] if triangulate else [side]
    for face in faces:
        face.select_set(True)
    bmesh.update_edit_mesh(orientable.data)
    assert bpy.ops.fdm_print.lay_on_face() == {"FINISHED"}
    assert bpy.context.mode == "EDIT_MESH"
    assert all(face.select for face in faces)
    assert all(abs((orientable.matrix_world @ vertex.co).z) < 1e-5
               for face in faces for vertex in face.verts)
    assert min((orientable.matrix_world @ vertex.co).z for vertex in bm.verts) > -1e-5
    assert all(abs(a - b) < 1e-5 for a, b in zip(orientable.scale, (2, 1, 0.3)))
    bpy.ops.object.mode_set(mode="OBJECT")

orientable.location = (-3, 4, 5)
orientable.rotation_euler = (0.5, 0.6, -0.7)
bpy.context.view_layer.update()
assert bpy.ops.fdm_print.auto_orient() == {"FINISHED"}
heights = [(orientable.matrix_world @ vertex.co).z for vertex in orientable.data.vertices]
assert abs(min(heights)) < 1e-5
assert abs(max(heights) - 0.6) < 1e-5  # The broadest face makes the lowest build.
bpy.ops.object.mode_set(mode="EDIT")
assert bpy.ops.fdm_print.auto_orient() == {"FINISHED"}
assert bpy.context.mode == "EDIT_MESH"
bpy.ops.object.mode_set(mode="OBJECT")

# The same extrusion paths should have fewer glass/air interfaces when fused.
def mesh_tree(mesh):
    return BVHTree.FromPolygons([v.co for v in mesh.vertices],
                                [tuple(p.vertices) for p in mesh.polygons])


def vertical_hits(tree, x, y):
    position = Vector((x, y, -1))
    axis = Vector((0, 0, 1))
    hits = []
    while True:
        point, _, _, _ = tree.ray_cast(position, axis)
        if point is None:
            return hits
        hits.append(point.z)
        position = point + axis * 0.00001


for source, density in ((cube, 100), (cube, 15), (cube, 0), (bpy.data.objects["Hollow cylinder"], 15)):
    activate(source)
    settings = source.fdm_print
    settings.size = 12
    settings.layer_height = 0.6
    settings.width = 0.45
    settings.density = density
    settings.skins = settings.bottom_layers = 2
    settings.pattern = "GRID"
    settings.brim = source == cube and density == 100
    assert bpy.ops.fdm_print.generate() == {"FINISHED"}
    live_obj = bpy.context.object
    live(live_obj, "Imperfections").value = 0
    if source == cube and density == 100:
        bead_hits = vertical_hits(mesh_tree(evaluated(live_obj)), 0.01, 0.02)
    source.fdm_print.fuse_bake = True
    source.fdm_print.fusion_method = "BATCHES" if source == cube and density == 15 else "V1"
    assert bpy.ops.fdm_print.bake() == {"FINISHED"}
    fused = bpy.context.object
    assert live_obj.hide_get() and live_obj.hide_render and fused.matrix_world == live_obj.matrix_world
    assert not fused.data.materials and source.hide_get() and source.hide_render
    bm = bmesh.new()
    bm.from_mesh(fused.data)
    assert all(edge.is_manifold and edge.is_contiguous for edge in bm.edges)
    volume = bm.calc_volume(signed=True)
    bm.free()
    assert volume > 0
    if source == cube and density == 100:
        assert live_obj.vertex_groups.get("Brim")
        assert max(v.co.x for v in fused.data.vertices) > 1.2
        dense_volume = volume
        assert len(bead_hits) > 10 and len(vertical_hits(mesh_tree(fused.data), 0.01, 0.02)) == 2
        # The cap has shallow channels while the vertical walls keep a straight outline.
        tree = mesh_tree(fused.data)
        top_heights = {round(vertical_hits(tree, i / 100, 0.02)[-1], 5)
                       for i in range(-35, 36)}
        assert len(top_heights) >= 2, top_heights
        right = {round(tree.ray_cast((2, 0.1, i / 100), (-1, 0, 0))[0].x, 5)
                 for i in range(31, 69)}
        assert len(right) >= 4, right
        flat_x = tree.ray_cast((2, 0.1, 1), (-1, 0, 0))[0].x
        corner = tree.ray_cast((2, 2, 1), (-1, -1, 0))[0]
        rim = tree.ray_cast((2, 0.1, 1.997), (-1, 0, 0))[0]
        assert flat_x - corner.x > 0.005  # Actual round corner in the XY silhouette.
        assert flat_x - rim.x > 0.003  # Actual roundover at the top lip.

    elif source == cube:
        assert volume < dense_volume * 0.8
        if density == 15:
            sparse_volume = volume
        else:
            assert volume < sparse_volume
            tree = mesh_tree(fused.data)
            for z in (0.49, 0.50, 0.525, 0.55):
                point = Vector((-2, 0.117, z))
                hits = []
                while True:
                    hit, *_ = tree.ray_cast(point, (1, 0, 0))
                    if hit is None:
                        break
                    hits.append(hit.x)
                    point = hit + Vector((0.00001, 0, 0))
                assert len(hits) == 4, hits
            attr = live_obj.data.attributes
            assert not any(feature.value == slicer.INFILL and 2 <= layer.value < 18
                           for feature, layer in zip(attr["fdm_feature"].data, attr["fdm_layer"].data))
    else:
        assert not vertical_hits(mesh_tree(fused.data), 0, 0)
    assert bpy.ops.fdm_print.show() == {"FINISHED"}
    assert not live_obj.hide_get() and fused.hide_get() and fused.hide_render

tube = bpy.data.objects["Hollow cylinder"]
activate(tube)
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
live_obj = bpy.context.object
live(live_obj, "Imperfections").value = 1
feature = evaluated(live_obj).attributes["fdm_feature"].data
web_points = sum(item.value == slicer.WEB for item in feature)
assert web_points > 0
tube.fdm_print.fuse_bake = True
assert bpy.ops.fdm_print.bake() == {"FINISHED"}
fused = bpy.context.object
assert len(fused.data.vertices) > web_points
# Fusion keeps the exposed strand geometry and can clip the embedded ends.
assert bpy.ops.fdm_print.show() == {"FINISHED"}
live_mesh = evaluated(live_obj)
original_web = {tuple(v.co) for v in live_mesh.vertices
                if live_mesh.attributes["fdm_feature"].data[v.index].value == slicer.WEB}
assert len(original_web & {tuple(v.co) for v in fused.data.vertices}) > len(original_web) * 0.5

# Changing seam placement must move localized relief on the fused hull,
# even with stringing, layer drift and droop disabled.
for effect in preview.EFFECTS:
    live(live_obj, effect).value = 0
bakes = []
for seam in ("Aligned Rear", "Staggered"):
    live(live_obj, "Seam Placement").value = seam
    assert bpy.ops.fdm_print.bake() == {"FINISHED"}
    mesh = bpy.context.object.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    assert all(edge.is_manifold for edge in bm.edges)
    bm.free()
    bakes.append({tuple(vertex.co) for vertex in mesh.vertices})
    assert bpy.ops.fdm_print.show() == {"FINISHED"}
assert bakes[0] != bakes[1]

# A variation in bead width must never shrink the extrusion vertically.
activate(cube)
cube.fdm_print.density = 0
cube.fdm_print.skins = cube.fdm_print.bottom_layers = 0
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
live_obj = bpy.context.object
for effect in preview.EFFECTS:
    live(live_obj, effect).value = int(effect == "Width Variation")
live(live_obj, "Imperfections").value = 0
original = coordinates(live_obj)
assert all(face.use_smooth for face in evaluated(live_obj).polygons)
live(live_obj, "Seam Placement").value = "Staggered"
assert coordinates(live_obj) == original
live(live_obj, "Imperfections").value = 1
varied = coordinates(live_obj)
assert original != varied
assert len(original) == len(varied)
assert all(abs(a[2] - b[2]) < 1e-6 for a, b in zip(original, varied))
cube.fdm_print.fuse_bake = False
assert bpy.ops.fdm_print.bake() == {"FINISHED"}
assert coordinates(bpy.context.object) == varied

print("FDM smoke tests passed")
