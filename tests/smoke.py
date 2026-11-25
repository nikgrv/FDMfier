"""Run with the built extension enabled in a clean Blender profile."""

import importlib
import math
import tempfile
from types import MethodType, SimpleNamespace
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
assert math.isclose(cube.fdm_print.layer_height, 0.30, abs_tol=1e-7)
assert cube.fdm_print.density == 15
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
for pattern in ("LINES", "GRID", "TRIANGLES", "CONCENTRIC", "HONEYCOMB", "PARALLEL",
                "GYROID", "CUBIC", "CROSS_3D", "ADAPTIVE_CUBIC"):
    paths, _, _ = slice_object(tube, pattern=pattern)
    assert sum(p.layer == 8 and p.wall for p in paths) == 4
    for path in paths:
        if path.feature == slicer.WEB:
            continue
        for a, b in zip(path.points, path.points[1:]):
            assert math.hypot((a[0] + b[0])/2, (a[1] + b[1])/2) > slicer.units(5.7)

# New surface patterns remain on their own top/bottom skins and preserve holes.
for pattern in ("HILBERT", "ARCHIMEDEAN", "OCTAGRAM"):
    paths, count, _ = slice_object(tube, density=0, top_pattern=pattern, bottom_pattern=pattern)
    for layer, top in ((0, False), (count - 1, True)):
        skin = [p for p in paths if p.feature == slicer.INFILL and p.top_skin == top and p.layer == layer]
        assert any(not p.closed for p in skin)
        assert sum(p.closed for p in skin) >= 2  # A track around the outside and around the hole.
    assert not any(p.feature == slicer.INFILL and p.layer == count // 2 for p in paths)
    for path in paths:
        if path.feature != slicer.INFILL:
            continue
        for a, b in zip(path.points, path.points[1:]):
            assert math.hypot((a[0] + b[0])/2, (a[1] + b[1])/2) > slicer.units(5.7)
tube.fdm_print.top_pattern = tube.fdm_print.bottom_pattern = "LINES"
tube.fdm_print.density = 15

assert bpy.ops.fdm_print.generate() == {"FINISHED"}
tube_preview = bpy.context.object
live(tube_preview, "Visible Layers").value = 1
tube_mesh = evaluated(tube_preview)
tree = BVHTree.FromPolygons([v.co for v in tube_mesh.vertices], [tuple(f.vertices) for f in tube_mesh.polygons])
assert tree.ray_cast((0, 0, 12), (0, 0, -1))[0] is None  # The reveal cap must leave the bore open.
assert tree.ray_cast((4.5, 0, 12), (0, 0, -1))[0] is not None
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


def support_paths(layers, tree, *, build_plate_only=False):
    steps = slicer.support_steps(layers, support_width, support_height, tree, 45, {},
                                build_plate_only=build_plate_only)
    return [step for step in steps if isinstance(step, slicer.Path)]


tree = support_paths(support_layers, True)
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
    assert support_paths(blocked_layers, tree_mode)
    assert not support_paths(blocked_layers, tree_mode, build_plate_only=True)
    assert any(path.layer == 0 for path in support_paths(support_layers, tree_mode, build_plate_only=True))
activate(beam)
settings = beam.fdm_print
settings.support_angle = 45
settings.layer_height = 0.6
settings.size = 18
settings.build_plate_only = True
body_material = bpy.data.materials.new("Source material")
beam.data.materials.append(body_material)
support_material = bpy.data.materials.new("Support material")
preview_material = bpy.data.materials.new("Preview material")
for mode in ("STANDARD", "TREE"):
    settings.supports = mode
    assert bpy.ops.fdm_print.generate() == {"FINISHED"}
    supported = bpy.context.object
    assert supported.material_slots[0].material == (body_material if mode == "STANDARD" else preview_material)
    assert supported.get("fdm_support_slot")
    assert supported.material_slots[1].material == (None if mode == "STANDARD" else support_material)
    geom = evaluated(supported)
    assert {face.material_index for face in geom.polygons} == {0, 1}
    bm = bmesh.new()
    try:
        bm.from_mesh(geom)
        assert all(edge.is_manifold and edge.is_contiguous for edge in bm.edges), mode
    finally:
        bm.free()
    slot_vertices = {i for f in geom.polygons if f.material_index == 1 for i in f.vertices}
    assert min(geom.vertices[i].co.z for i in slot_vertices) <= settings.layer_height / settings.size
    if mode == "STANDARD":
        supported.material_slots[0].material = preview_material
        supported.material_slots[1].material = support_material
        supported.active_material_index = 1

live(supported, "Visible Layers").value = 2
assert {face.material_index for face in evaluated(supported).polygons} == {0, 1}
live(supported, "Visible Layers").value = supported["fdm_layers"]
shape = coordinates(supported)
live(supported, "Imperfections").value = 1
assert coordinates(supported) != shape
droop = supported.data.attributes[addon.loft.DROOP_ATTRIBUTE]
assert any(value.value for value in droop.data)
bed_min = min(v.co.z for v in supported.data.vertices)
assert not any(value.value and abs(vertex.co.z - bed_min) < 0.02
               for value, vertex in zip(droop.data, supported.data.vertices))
assert bpy.ops.fdm_print.remove() == {"FINISHED"}

# A blank weight mask keeps the preview unchanged. Invert it to apply
# imperfections without altering the source vertex group.
painted = beam.vertex_groups.new(name="Painted")
settings.supports = "OFF"
settings.weight_group = painted.name
settings.invert_weight_group = False
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
masked = bpy.context.object
shape = coordinates(masked)
live(masked, "Imperfections").value = 1
assert coordinates(masked) == shape
settings.invert_weight_group = True
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
masked = bpy.context.object
assert live(masked, "Imperfections").value == 1
changed = coordinates(masked)
live(masked, "Imperfections").value = 0
assert coordinates(masked) != changed
assert bpy.ops.fdm_print.remove() == {"FINISHED"}
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
original_geometry = obj.data
live(obj, "Visible Layers").value = 0
assert not evaluated(obj).vertices
live(obj, "Visible Layers").value = 10
partial = len(evaluated(obj).polygons)
assert 0 < partial < full and obj.data == original_geometry
assert cube.hide_get() and cube.hide_render
assert (obj.location - Vector((0, 0, -1))).length < 1e-6
assert not hasattr(bpy.types, "FDM_OT_bake")

with tempfile.TemporaryDirectory(prefix="fdm-smoke-") as directory:
    path = str(Path(directory) / "preview.blend")
    bpy.ops.wm.save_as_mainfile(filepath=path)
    bpy.ops.wm.open_mainfile(filepath=path)
    cube = bpy.data.objects["Cube"]
    obj = addon.existing_preview(bpy.context.scene, cube)
    assert live(obj, "Visible Layers").value == 10
    assert not obj.hide_get() and not obj.hide_render
    assert len(evaluated(obj).polygons) == partial
    activate(obj)
    assert bpy.ops.fdm_print.generate() == {"FINISHED"}
    obj = bpy.context.object
    assert live(obj, "Visible Layers").value == 10

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

    # Drive the modal operator manually in background Blender. Cancel a staged
    # result before publication, then run another job and publish it.
    timer = object()
    removed_timers = []
    manager = SimpleNamespace(windows=(), event_timer_add=lambda *args, **kwargs: timer,
                              event_timer_remove=removed_timers.append, modal_handler_add=lambda _: None)
    modal_context = SimpleNamespace(scene=bpy.context.scene, active_object=obj, mode="OBJECT",
                                    window=None, window_manager=manager)
    operator = addon.FDM_OT_generate
    timer_event = SimpleNamespace(type="TIMER")
    reports = []
    job = SimpleNamespace(report=lambda kinds, message: reports.append(message),
                          prepare_snapshot=lambda _: operator.prepare_snapshot(job, bpy.context),
                          finish=lambda _, result: operator.finish(job, bpy.context, result))
    job.cancel = MethodType(operator.cancel, job)
    job.generation_steps = MethodType(operator.generation_steps, job)

    for cancel in (True, False):
        assert operator.invoke(job, modal_context, None) == {"RUNNING_MODAL"}
        assert operator.modal(job, modal_context, timer_event) == {"RUNNING_MODAL"}
        assert job.steps is not None and job.result is None
        for _ in range(1000):
            assert operator.modal(job, modal_context, timer_event) == {"RUNNING_MODAL"}
            if job.result is not None:
                break
        assert job.progress == (0.99, "Creating Blender preview")
        assert addon.existing_preview(bpy.context.scene, cube) == obj
        if cancel:
            esc = SimpleNamespace(type="ESC", value="PRESS")
            assert operator.modal(job, modal_context, esc) == {"CANCELLED"}
            assert reports[-1] == "Print preview generation cancelled."
            assert addon.existing_preview(bpy.context.scene, cube) == obj
        else:
            assert operator.modal(job, modal_context, timer_event) == {"FINISHED"}
            obj = bpy.context.object
            assert addon.existing_preview(bpy.context.scene, cube) == obj
            assert live(obj, "Visible Layers").value == 10
        assert addon.JOB is None and job.steps is None
    assert removed_timers == [timer, timer]
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

# A closed copy is generated from a box with a missing face.
opened = mesh_object("Open cube", [tuple(vertex.co) for vertex in cube.data.vertices],
                     [tuple(face.vertices) for face in list(cube.data.polygons)[:-1]])
assert bpy.ops.fdm_print.repair() == {"FINISHED"}
repaired = bpy.context.object
assert repaired != opened and len(repaired.data.polygons) == 6 and len(opened.data.polygons) == 5
assert opened.hide_get() and opened.hide_render

# Independent skins.
paths, _, _ = slice_object(repaired, skins=2, bottom_layers=0, density=0, top_pattern="CONCENTRIC")
assert not any(path.feature == slicer.INFILL and path.layer == 0 for path in paths)
assert any(path.feature == slicer.INFILL and path.closed and path.layer == 59 for path in paths)

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

print("FDM smoke tests passed")
