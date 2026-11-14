# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""FDMfier Blender extension."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from math import ceil
from threading import Event
from types import SimpleNamespace

import bmesh
import bpy
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector

from . import fusion, orientation, preview, slicer

SETTING_NAMES = (
    "size", "layer_height", "width", "walls", "skins", "density",
    "pattern", "supports", "skirt", "skirt_loops", "skirt_offset", "brim", "brim_width",
    "bottom_layers", "top_pattern", "bottom_pattern", "weight_group", "invert_weight_group", "support_angle",
    "build_plate_only", "support_spacing",
)
LIVE = ("Visible Layers", "Imperfections", "Seam Placement", *preview.EFFECTS)
SKIN_PATTERNS = (
    ("LINES", "Alternating Lines", "Change hatch direction each layer"),
    ("PARALLEL", "Parallel Lines", "Keep one hatch direction across layers"),
    ("CONCENTRIC", "Concentric", "Follow successive insets of the surface outline"),
)
JOB = None


def redraw_preview_panels(context):
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


class FDMSettings(PropertyGroup):
    size: FloatProperty(name="Simulated Print Size (mm)", description="Longest dimension used for slicing detail. The preview matches the source size in the scene", default=100, min=1, max=1000)
    layer_height: FloatProperty(name="Layer Height (mm)", default=0.20, min=0.03, max=5, precision=3)
    width: FloatProperty(name="Extrusion Width (mm)", default=0.45, min=0.05, max=10, precision=3)
    walls: IntProperty(name="Wall Lines", default=2, min=1, max=12)
    skins: IntProperty(name="Top Layers", default=4, min=0, max=30)
    bottom_layers: IntProperty(name="Bottom Layers", default=4, min=0, max=30)
    top_pattern: EnumProperty(name="Top Pattern", items=SKIN_PATTERNS, default="LINES")
    bottom_pattern: EnumProperty(name="Bottom Pattern", items=SKIN_PATTERNS, default="LINES")
    weight_group: bpy.props.StringProperty(
        name="Imperfection Mask", description="Optional vertex group. Regenerate after changing weights"
    )
    invert_weight_group: BoolProperty(name="Invert Vertex Group", description="Use one minus the selected vertex weight")
    support_angle: FloatProperty(name="Overhang Angle (°)", description="Minimum overhang angle from vertical. Horizontal undersides are 90 degrees", default=45, min=0, max=90, precision=0)
    support_spacing: FloatProperty(
        name="Support Spacing (mm)", min=0, max=30, default=0,
        description="Minimum spacing between support tips. Zero uses automatic spacing",
    )
    build_plate_only: BoolProperty(
        name="Build Plate Only", description="Generate only supports rooted on the build plate"
    )
    fuse_bake: BoolProperty(name="Fuse for Translucency", default=False)
    density: FloatProperty(name="Infill (%)", default=15, min=0, max=100, precision=1)
    pattern: EnumProperty(name="Infill Pattern", items=(
        ("LINES", "Lines", "Alternate line directions between layers"),
        ("GRID", "Grid", "Crossed line families"),
        ("TRIANGLES", "Triangles", "Three line families 60 degrees apart"),
        ("PARALLEL", "Parallel Lines", "Use the same line direction on every layer"),
        ("CONCENTRIC", "Concentric", "Follow the outline in successive loops"),
        ("HONEYCOMB", "Honeycomb", "A hexagonal lattice"),
    ), default="GRID")
    supports: EnumProperty(name="Supports", items=(
        ("OFF", "Off", "Skip supports"),
        ("STANDARD", "Standard", "Straight build-plate supports"),
        ("TREE", "Tree", "Tapered build-plate branches"),
    ), default="OFF")
    skirt: BoolProperty(name="Skirt", default=False)
    skirt_loops: IntProperty(name="Skirt Loops", default=2, min=1, max=12)
    skirt_offset: FloatProperty(name="Skirt Offset (mm)", default=3, min=0, max=30)
    brim: BoolProperty(name="Brim", description="Add connected tracks around the outside of the first layer")
    brim_width: FloatProperty(name="Brim Width (mm)", default=5, min=0.1, max=30,
                              description="Target width in simulated print millimeters, rounded up to whole tracks")
    source: PointerProperty(type=bpy.types.Object)


def active_source(context):
    obj = context.active_object
    if obj is None or obj.type != "MESH" or context.mode != "OBJECT":
        return None
    source = obj.fdm_print.source
    if source is not None:
        obj = source
    return None if obj.get("fdm_preview") else obj


def existing_preview(scene, source):
    return next((obj for obj in scene.objects if obj.get("fdm_preview")
                 and obj.fdm_print.source == source), None)


def node_modifier(obj):
    return next((modifier for modifier in obj.modifiers if modifier.type == "NODES"
                 and modifier.node_group and modifier.node_group.get("fdm_schema") in {1, 2, 3, 4, 5, 6, 7}), None)


def evaluated_input(context, source, settings):
    evaluated = source.evaluated_get(context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        world = [tuple(evaluated.matrix_world @ vert.co) for vert in mesh.vertices]
        triangles = [tuple(triangle.vertices) for triangle in mesh.loop_triangles]
        group_name = getattr(settings, "weight_group", "")
        group = source.vertex_groups.get(group_name) if group_name else None
        if group_name and group is None:
            raise slicer.SliceError("The selected imperfection vertex group no longer exists.")
        weights = ([next((item.weight for item in vertex.groups if item.group == group.index), 0.0)
                    for vertex in mesh.vertices] if group else None)
    finally:
        evaluated.to_mesh_clear()

    if not triangles:
        raise slicer.SliceError("The evaluated mesh has no solid faces.")
    used = {i for triangle in triangles for i in triangle}
    low = tuple(min(world[i][axis] for i in used) for axis in range(3))
    high = tuple(max(world[i][axis] for i in used) for axis in range(3))
    extent = max(b - a for a, b in zip(low, high))
    if extent <= 0 or high[2] <= low[2]:
        raise slicer.SliceError("The evaluated mesh has no printable height.")

    # Weld coincident triangle vertices in the private snapshot. This handles
    # imported solids with split normals and unwelded face boundaries.
    origin = ((low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2])
    scale = settings.size / extent
    remap, vertices, lookup = {}, [], {}
    for i in sorted(used):
        point = tuple((value - base) * scale for value, base in zip(world[i], origin))
        key = tuple(round(value, 7) for value in point)
        if key not in lookup:
            lookup[key] = len(vertices)
            vertices.append(point)
        remap[i] = lookup[key]
    faces, incidence = [], {}
    for triangle in triangles:
        face = tuple(remap[i] for i in triangle)
        if len(set(face)) < 3:
            continue
        faces.append(face)
        for a, b in zip(face, (*face[1:], face[0])):
            edge = (min(a, b), max(a, b))
            incidence[edge] = incidence.get(edge, 0) + 1
    if not faces or any(count != 2 for count in incidence.values()):
        raise slicer.SliceError("The evaluated mesh has open or non-manifold edges. Close holes and merge overlaps first.")

    if weights is not None:
        welded_weights = [0.0] * len(vertices)
        for i, j in remap.items():
            welded_weights[j] = max(welded_weights[j], weights[i])
        settings.weight_surface = ([Vector(vertex) for vertex in vertices], faces, welded_weights)
    return vertices, faces, scale, origin


def remove_object(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if not data.users:
        bpy.data.meshes.remove(data)


def hide_previous_bakes(view_layer, source, exclude=None):
    for obj in view_layer.objects:
        if (obj != exclude and (obj.get("fdm_baked") or obj.get("fdm_fused")) and obj.fdm_print.source == source):
            obj.hide_set(True)
            obj.hide_render = True


def publish(context, source, settings, result, mm_per_unit, location):
    paths, layers, narrow = result
    previous = existing_preview(context.scene, source)
    live = {}
    if previous is not None:
        modifier = node_modifier(previous)
        if modifier:
            ids = preview.sockets(modifier.node_group)
            live = {name: getattr(modifier.properties.inputs, ids[name]).value for name in LIVE if name in ids}
            if live.get("Visible Layers") == previous["fdm_layers"]:
                live.pop("Visible Layers")
    mesh = preview.make_path_mesh(paths, settings, mm_per_unit)
    try:
        obj = bpy.data.objects.new(f"{source.name} • FDM Preview", mesh)
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    try:
        obj.location = location
        obj["fdm_preview"] = True
        obj["fdm_layers"] = layers
        obj.fdm_print.source = source
        for name in SETTING_NAMES:
            setattr(obj.fdm_print, name, getattr(settings, name))
        preview.mesh_groups(obj)
        modifier = obj.modifiers.new("FDMfier", "NODES")
        modifier.node_group = preview.node_group()
        ids = preview.sockets(modifier.node_group)
        getattr(modifier.properties.inputs, ids["Bead Width"]).value = settings.width / mm_per_unit
        getattr(modifier.properties.inputs, ids["Layer Height"]).value = settings.layer_height / mm_per_unit
        getattr(modifier.properties.inputs, ids["Visible Layers"]).value = min(layers, live.get("Visible Layers", layers))
        for name in LIVE[1:]:
            if name in live:
                getattr(modifier.properties.inputs, ids[name]).value = live[name]
        collection = next((coll for coll in source.users_collection
                           if context.view_layer.layer_collection.collection == coll
                           or coll in context.view_layer.layer_collection.collection.children_recursive),
                          context.scene.collection)
        collection.objects.link(obj)
        obj.update_tag()
    except Exception:
        remove_object(obj)
        raise
    if previous is not None:
        remove_object(previous)
        obj.name = f"{source.name} • FDM Preview"
    context.view_layer.update()
    if context.view_layer.objects.get(obj.name):
        hide_previous_bakes(context.view_layer, source)
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        hide_source(context, source)
    source.pop("fdm_needs_repair", None)
    source.pop("fdm_orientation_dirty", None)
    return layers, narrow


class FDM_OT_generate(Operator):
    bl_idname = "fdm_print.generate"
    bl_label = "Generate Print Preview"
    bl_description = "Slice the selected solid mesh. Press Esc to cancel interactive generation"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return active_source(context) is not None and JOB is None

    def prepare(self, context):
        self.source = active_source(context)
        self.scene = context.scene
        self.prepare_snapshot(context)

    def prepare_snapshot(self, context):
        if context.scene != self.scene or self.scene.objects.get(self.source.name) != self.source:
            raise slicer.SliceError("The source object or scene changed before generation.")
        self.settings = SimpleNamespace(**{name: getattr(self.source.fdm_print, name)
                                           for name in SETTING_NAMES})
        try:
            self.vertices, self.faces, self.mm_per_unit, self.location = evaluated_input(
                context, self.source, self.settings)
        except slicer.SliceError as error:
            if "non-manifold" in str(error):
                self.source["fdm_needs_repair"] = True
            raise

    def finish(self, context, result):
        if context.scene != self.scene or self.scene.objects.get(self.source.name) != self.source:
            raise slicer.SliceError("The source object or scene changed while generating.")
        layers, narrow = publish(context, self.source, self.settings, result, self.mm_per_unit, self.location)
        if narrow:
            self.report({"WARNING"}, f"Generated {layers} layers. Some thin sections could not fit a wall.")
        else:
            self.report({"INFO"}, f"Generated {layers} print layers.")
        return {"FINISHED"}

    def execute(self, context):
        try:
            self.prepare(context)
            return self.finish(context, slicer.generate(self.vertices, self.faces, self.settings))
        except (ValueError, RuntimeError, ReferenceError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

    def invoke(self, context, event):
        global JOB
        self.source = active_source(context)
        self.scene = context.scene
        self.stop = Event()
        self.updates = deque(maxlen=1)
        self.progress = (0.0, "Preparing mesh")
        self.future = None
        self.executor = None
        self.ready_to_publish = False
        self.timer = context.window_manager.event_timer_add(0.15, window=context.window)
        context.window_manager.modal_handler_add(self)
        JOB = self
        redraw_preview_panels(context)
        return {"RUNNING_MODAL"}

    def start_worker(self, context):
        self.prepare_snapshot(context)
        settings = SimpleNamespace(**{name: getattr(self.settings, name) for name in SETTING_NAMES})
        updates = self.updates

        def progress(factor, label):
            updates.append((factor, label))

        self.executor = ThreadPoolExecutor(max_workers=1)
        self.future = self.executor.submit(slicer.generate, self.vertices, self.faces,
                                           settings, self.stop.is_set, progress)

    def modal(self, context, event):
        if event.type == "ESC":
            self.cancel(context)
            self.report({"INFO"}, "Print preview generation cancelled.")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            if self.future is None:
                self.start_worker(context)
            elif self.future.done():
                result = self.future.result()
                if self.ready_to_publish:
                    status = self.finish(context, result)
                    self.cancel(context)
                    return status
                # Give Blender a redraw before publishing the mesh on the main thread.
                self.ready_to_publish = True
                self.progress = (0.96, "Creating Blender preview")
                redraw_preview_panels(context)
            elif self.updates and self.progress != self.updates[-1]:
                self.progress = self.updates[-1]
                redraw_preview_panels(context)
        except Exception as error:
            self.cancel(context)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        global JOB
        if JOB is self:
            self.stop.set()
            if self.executor is not None:
                self.executor.shutdown(wait=False, cancel_futures=True)
            context.window_manager.event_timer_remove(self.timer)
            JOB = None
            redraw_preview_panels(context)


class FDM_OT_bake(Operator):
    bl_idname = "fdm_print.bake"
    bl_label = "Bake to Mesh"
    bl_description = "Create a separate editable mesh from the current visible layers"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        source = active_source(context)
        obj = existing_preview(context.scene, source) if source is not None else None
        return JOB is None and obj is not None and not obj.get("fdm_baked_hidden")

    def execute(self, context):
        source = active_source(context)
        obj = existing_preview(context.scene, source)
        fused = source.fdm_print.fuse_bake
        try:
            if fused:
                modifier = node_modifier(obj)
                if modifier is None:
                    raise slicer.SliceError("Regenerate the preview before fusing.")
                ids = preview.sockets(modifier.node_group)
                inputs = modifier.properties.inputs
                width = getattr(inputs, ids["Bead Width"]).value
                height = getattr(inputs, ids["Layer Height"]).value
                visible = min(obj["fdm_layers"], getattr(inputs, ids["Visible Layers"]).value)
                graph = context.evaluated_depsgraph_get()
                paths = preview.evaluated_fusion_paths(obj, modifier, graph)
                try:
                    core = fusion.core_mesh(paths, width, height, visible)
                finally:
                    bpy.data.meshes.remove(paths)
                try:
                    data = preview.fuse_preview(obj, modifier, core, graph)
                finally:
                    bpy.data.meshes.remove(core)
            else:
                graph = context.evaluated_depsgraph_get()
                data = bpy.data.meshes.new_from_object(obj.evaluated_get(graph),
                                                      preserve_all_data_layers=True, depsgraph=graph)
                if not data.polygons:
                    bpy.data.meshes.remove(data)
                    raise slicer.SliceError("Reveal at least one layer before baking.")
        except (ValueError, RuntimeError, slicer.clip.ClipperException) as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}

        data.materials.clear()
        label = "Fused FDM" if fused else "Baked FDM"
        baked = bpy.data.objects.new(f"{source.name} • {label}", data)
        baked["fdm_baked"] = True
        obj.users_collection[0].objects.link(baked)
        baked.matrix_world = obj.matrix_world.copy()
        baked.fdm_print.source = source
        context.view_layer.update()
        hide_previous_bakes(context.view_layer, source, exclude=baked)
        set_preview_hidden(obj, True)
        hide_source(context, source)
        for selected in context.selected_objects:
            selected.select_set(False)
        baked.select_set(True)
        context.view_layer.objects.active = baked
        return {"FINISHED"}


def hide_source(context, source):
    if source.name in context.view_layer.objects:
        source.hide_set(True)
    source.hide_render = True


class FDM_OT_remove(Operator):
    bl_idname = "fdm_print.remove"
    bl_label = "Remove Preview"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        source = active_source(context)
        return JOB is None and source is not None and existing_preview(context.scene, source) is not None

    def execute(self, context):
        source = active_source(context)
        remove_object(existing_preview(context.scene, source))
        if context.view_layer.objects.get(source.name):
            source.hide_set(False)
            source.hide_render = False
            source.select_set(True)
            context.view_layer.objects.active = source
        return {"FINISHED"}


def set_preview_hidden(obj, hidden):
    obj["fdm_baked_hidden"] = hidden
    obj.hide_set(hidden)
    obj.hide_render = hidden
    modifier = node_modifier(obj)
    if modifier:
        modifier.show_viewport = not hidden


class FDM_OT_show(Operator):
    bl_idname = "fdm_print.show"
    bl_label = "Show Live Preview"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        source = active_source(context)
        obj = existing_preview(context.scene, source)
        if obj is None:
            return {"CANCELLED"}
        hide_previous_bakes(context.view_layer, source)
        set_preview_hidden(obj, False)
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        return {"FINISHED"}


class FDM_OT_repair(Operator):
    bl_idname = "fdm_print.repair"
    bl_label = "Repair / Remesh Copy"
    bl_description = "Weld and close holes on a copy, with voxel remeshing if needed"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return JOB is None and active_source(context) is not None

    def execute(self, context):
        source = active_source(context)
        graph = context.evaluated_depsgraph_get()
        evaluated = source.evaluated_get(graph)
        mesh = bpy.data.meshes.new_from_object(evaluated)
        origin = evaluated.matrix_world.translation.copy()
        mesh.transform(Matrix.Translation(-origin) @ evaluated.matrix_world)
        obj = bpy.data.objects.new(f"{source.name} • Repaired", mesh)
        obj.location = origin
        context.collection.objects.link(obj)
        try:
            bm = bmesh.new()
            try:
                bm.from_mesh(mesh)
                extent = max((max(v.co[axis] for v in bm.verts) - min(v.co[axis] for v in bm.verts)
                              for axis in range(3)), default=0) if bm.verts else 0
                if not extent or not bm.faces:
                    raise slicer.SliceError("The source needs surface faces to repair.")
                bmesh.ops.remove_doubles(bm, verts=list(bm.verts), dist=extent * 1e-6)
                boundary = [edge for edge in bm.edges if edge.is_boundary]
                if boundary:
                    bmesh.ops.holes_fill(bm, edges=boundary, sides=0)
                bmesh.ops.recalc_face_normals(bm, faces=list(bm.faces))
                needs_voxels = (any(not edge.is_manifold for edge in bm.edges)
                                or abs(bm.calc_volume(signed=True)) < extent ** 3 * 1e-8)
                if not needs_voxels:
                    bm.to_mesh(mesh)
                    mesh.update()
            finally:
                bm.free()
            context.view_layer.update()
            if needs_voxels:
                modifier = obj.modifiers.new("Voxel repair", "REMESH")
                modifier.mode = "VOXEL"
                modifier.voxel_size = extent / 96
                modifier.use_remove_disconnected = False
                context.view_layer.update()
                rebuilt = bpy.data.meshes.new_from_object(obj.evaluated_get(graph))
                obj.modifiers.remove(modifier)
                obj.data = rebuilt
                bpy.data.meshes.remove(mesh)
            config = SimpleNamespace(size=source.fdm_print.size, weight_group="")
            evaluated_input(context, obj, config)
        except (RuntimeError, ValueError) as error:
            remove_object(obj)
            self.report({"ERROR"}, f"Could not make a closed solid. {error}")
            return {"CANCELLED"}
        for name in SETTING_NAMES:
            if name != "weight_group":
                setattr(obj.fdm_print, name, getattr(source.fdm_print, name))
        for selected in context.selected_objects:
            selected.select_set(False)
        obj.select_set(True)
        context.view_layer.objects.active = obj
        previous = existing_preview(context.scene, source)
        if previous is not None:
            set_preview_hidden(previous, True)
        hide_previous_bakes(context.view_layer, source)
        hide_source(context, source)
        self.report({"INFO"}, "Created a closed copy. Generate the preview from this copy.")
        return {"FINISHED"}


def estimate_vertices(source, settings):
    corners = [source.matrix_world @ Vector(corner) for corner in source.bound_box]
    dims = [max(point[axis] for point in corners) - min(point[axis] for point in corners)
            for axis in range(3)]
    longest = max(dims)
    if not longest or not dims[2] or not source.data.polygons:
        return None
    x, y, z = (value * settings.size / longest for value in dims)
    layers = ceil(z / settings.layer_height - 1e-9)
    width = settings.width
    wall_points = layers * settings.walls * (x + y) / width
    full_layers = min(layers, settings.skins + settings.bottom_layers)
    density = settings.density / 100
    fill_layers = full_layers + (layers - full_layers) * density
    fill_points = fill_layers * (2 * (x + y) / width + 0.4)
    if settings.pattern == "HONEYCOMB" and density:
        fill_points += layers * x * y * (density / width) ** 2
    support_points = layers * x * y / max(width * 6, 2, settings.support_spacing) ** 2 * 3 if settings.supports != "OFF" else 0
    skirt_points = 4 * settings.skirt_loops if settings.skirt else 0
    brim_points = 4 * ceil(settings.brim_width / width) if settings.brim else 0
    return layers, round(preview.BEAD_PROFILE_SEGMENTS * (wall_points + fill_points + support_points + skirt_points + brim_points))


def orientation_source(context):
    if context.mode == "EDIT_MESH":
        obj = context.active_object
        if obj and obj.type == "MESH" and not obj.get("fdm_preview"):
            return obj
        return None
    return active_source(context)


def orient(context, operator, source, find_plane):
    try:
        down, pivot, floor = find_plane()
        orientation.place_on_bed(context, source, down, pivot, floor)
    except (ValueError, RuntimeError) as error:
        operator.report({"WARNING"}, str(error))
        return {"CANCELLED"}
    if existing_preview(context.scene, source) is not None:
        source["fdm_orientation_dirty"] = True
        operator.report({"INFO"}, "Source oriented. Regenerate the print preview to use this orientation.")
    else:
        operator.report({"INFO"}, "Source placed on the build plane at Z = 0.")
    return {"FINISHED"}


class FDM_OT_lay_on_face(Operator):
    bl_idname = "fdm_print.lay_on_face"
    bl_label = "Lay on Selected Face"
    bl_description = "Rotate the active mesh so the selected flat face or coplanar faces sit at world Z = 0"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return JOB is None and context.mode == "EDIT_MESH" and orientation_source(context) is not None

    def execute(self, context):
        source = orientation_source(context)
        return orient(context, self, source, lambda: orientation.selected_face_plane(source))


class FDM_OT_auto_orient(Operator):
    bl_idname = "fdm_print.auto_orient"
    bl_label = "Auto Orient"
    bl_description = "Compare build directions for fewer overhangs and more bed contact"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return JOB is None and orientation_source(context) is not None

    def execute(self, context):
        source = orientation_source(context)
        if context.mode == "EDIT_MESH":
            source.update_from_editmode()
        return orient(context, self, source, lambda: orientation.auto_plane(context, source))


class FDM_PT_preview(Panel):
    bl_label = "FDMfier"
    bl_idname = "FDM_PT_print_preview"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "FDMfier"

    def draw(self, context):
        layout = self.layout
        if JOB is not None:
            factor, stage = JOB.progress
            box = layout.box()
            box.label(text=stage)
            box.progress(factor=factor, type="BAR", text=f"{factor:.0%}")
            box.label(text="Esc to cancel")
        if context.mode == "EDIT_MESH" and orientation_source(context) is not None:
            layout.operator("fdm_print.lay_on_face")
            layout.operator("fdm_print.auto_orient")
            return
        source = active_source(context)
        if source is None:
            layout.label(text="Select a solid mesh.")
            return
        settings = source.fdm_print
        obj = existing_preview(context.scene, source)
        layout.operator("fdm_print.auto_orient")
        if source.get("fdm_orientation_dirty") and obj:
            layout.label(text="Orientation changed. Regenerate preview.", icon="INFO")
        layout.use_property_split = True
        layout.use_property_decorate = False
        for name in ("size", "layer_height", "width", "walls"):
            layout.prop(settings, name)
        layout.label(text=f"Nominal wall thickness  {settings.width * settings.walls:.2f} mm")
        surfaces = layout.box()
        surfaces.label(text="Top and Bottom Surfaces")
        for depth, pattern in (("skins", "top_pattern"), ("bottom_layers", "bottom_pattern")):
            surfaces.prop(settings, depth)
            if getattr(settings, depth):
                surfaces.prop(settings, pattern)
        layout.prop(settings, "density")
        if 0 < settings.density < 100:
            layout.prop(settings, "pattern")
        layout.prop(settings, "supports")
        if settings.supports != "OFF":
            layout.prop(settings, "support_angle", slider=True)
            layout.prop(settings, "build_plate_only")
            layout.prop(settings, "support_spacing")
        layout.prop(settings, "brim")
        if settings.brim:
            layout.prop(settings, "brim_width")
        layout.prop(settings, "skirt")
        if settings.skirt:
            layout.prop(settings, "skirt_loops")
            layout.prop(settings, "skirt_offset")
        mask = layout.row(align=True)
        mask.prop_search(settings, "weight_group", source, "vertex_groups", text="Imperfection Mask")
        if settings.weight_group:
            mask.prop(settings, "invert_weight_group", text="", icon="ARROW_LEFTRIGHT", toggle=True)
        estimate = estimate_vertices(source, settings)
        if estimate:
            layers, vertices = estimate
            layout.label(text=f"Est. {layers:,} layers • ~{vertices:,} vertices")
        if source.get("fdm_needs_repair"):
            layout.label(text="Open or non-manifold mesh", icon="ERROR")
        layout.operator("fdm_print.repair")
        if JOB is None:
            layout.operator("fdm_print.generate", text="Regenerate" if obj else "Generate")
        if obj:
            box = layout.box()
            box.label(text=f"{obj['fdm_layers']} generated layers")
            modifier = node_modifier(obj)
            if modifier:
                ids = preview.sockets(modifier.node_group)
                for name in (name for name in LIVE if name in ids):
                    box.prop(getattr(modifier.properties.inputs, ids[name]), "value",
                             text=name, slider=name == "Imperfections" or name in preview.EFFECTS)
            if obj.get("fdm_baked_hidden"):
                box.operator("fdm_print.show")
            else:
                box.prop(settings, "fuse_bake")
                box.operator("fdm_print.bake")
            box.operator("fdm_print.remove")


CLASSES = (FDMSettings, FDM_OT_generate, FDM_OT_bake, FDM_OT_remove, FDM_OT_show, FDM_OT_repair, FDM_OT_lay_on_face, FDM_OT_auto_orient, FDM_PT_preview)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.fdm_print = PointerProperty(type=FDMSettings)


def unregister():
    if JOB is not None:
        JOB.cancel(bpy.context)
    del bpy.types.Object.fdm_print
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
