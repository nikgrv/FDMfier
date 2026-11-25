# SPDX-FileCopyrightText: 2026 Nikita Goryainov - nik.grv
# SPDX-License-Identifier: GPL-3.0-or-later

"""FDMfier Blender extension."""

from math import ceil
from time import perf_counter
from types import SimpleNamespace

import bmesh
import bpy
import numpy as np
from bpy.app.handlers import persistent
from bpy.props import BoolProperty, EnumProperty, FloatProperty, IntProperty, PointerProperty
from bpy.types import Operator, Panel, PropertyGroup
from mathutils import Matrix, Vector

from . import loft, orientation, preview, slicer, solid

SETTING_NAMES = (
    "size", "layer_height", "width", "walls", "skins", "density",
    "pattern", "supports", "skirt", "skirt_loops", "skirt_offset", "brim", "brim_width",
    "bottom_layers", "top_pattern", "bottom_pattern", "support_angle",
    "build_plate_only", "support_spacing", "weight_group", "invert_weight_group",
)
LIVE = ("Visible Layers", "Imperfections")
SKIN_PATTERNS = (
    ("LINES", "Alternating Lines", "Change hatch direction each layer"),
    ("PARALLEL", "Parallel Lines", "Keep one hatch direction across layers"),
    ("CONCENTRIC", "Concentric", "Follow successive insets of the surface outline"),
    ("HILBERT", "Hilbert Curve", "A continuous square maze across the surface"),
    ("ARCHIMEDEAN", "Archimedean Chords", "A circular spiral clipped to the surface"),
    ("OCTAGRAM", "Octagram Spiral", "An eight-pointed star spiral clipped to the surface"),
)
JOB = None


def redraw_preview_progress(context):
    for window in context.window_manager.windows:
        for area in getattr(window, "global_areas", ()):
            if area.type == "STATUSBAR":
                area.tag_redraw()
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.tag_redraw()


def draw_generation_progress(self, context):
    if JOB is not None and JOB.scene == context.scene:
        factor, _ = JOB.progress
        row = self.layout.row()
        row.scale_x = 2
        row.progress(factor=factor, type="BAR", text=f"FDMfier {factor:.0%}")


class FDMSettings(PropertyGroup):
    size: FloatProperty(name="Simulated Print Size (mm)", description="Longest dimension used for slicing detail. The preview matches the source size in the scene", default=100, min=1, max=1000)
    layer_height: FloatProperty(name="Layer Height (mm)", default=0.30, min=0.03, max=5, precision=3)
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
    density: FloatProperty(name="Infill (%)", default=15, min=0, max=100, precision=1)
    pattern: EnumProperty(name="Infill Pattern", items=(
        ("LINES", "Lines", "Alternate line directions between layers"),
        ("GRID", "Grid", "Crossed line families"),
        ("TRIANGLES", "Triangles", "Three line families 60 degrees apart"),
        ("PARALLEL", "Parallel Lines", "Use the same line direction on every layer"),
        ("CONCENTRIC", "Concentric", "Follow the outline in successive loops"),
        ("HONEYCOMB", "Honeycomb", "A hexagonal lattice"),
        ("GYROID", "Gyroid", "Curved paths through a gyroid surface that evolves with height"),
        ("CUBIC", "Cubic", "A three-dimensional lattice of tilted cubes"),
        ("CROSS_3D", "Cross 3D", "A fractal cross pattern that changes across layers"),
        ("ADAPTIVE_CUBIC", "Adaptive Cubic", "Smaller cubic cells near model surfaces and larger cells inside"),
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
                 and modifier.node_group and (modifier.node_group.get("fdm_joined_reveal")
                 or modifier.node_group.get("fdm_schema") in {1, 2, 3, 4, 5, 6, 7, 8})), None)


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
        if settings.invert_weight_group:
            welded_weights = [1 - weight for weight in welded_weights]
        settings.weight_surface = ([Vector(vertex) for vertex in vertices], faces, welded_weights)
    return vertices, faces, scale, origin


def remove_object(obj):
    data = obj.data
    bpy.data.objects.remove(obj, do_unlink=True)
    if not data.users:
        bpy.data.meshes.remove(data)


def hide_previous_bakes(view_layer, source):
    for obj in view_layer.objects:
        if (obj.get("fdm_baked") or obj.get("fdm_fused")) and obj.fdm_print.source == source:
            obj.hide_set(True)
            obj.hide_render = True


def set_preview_materials(obj, source, previous, support_start):
    body = source.active_material
    support = None
    support_selected = False
    if previous is not None:
        had_support = bool(previous.get("fdm_support_slot")) and len(previous.material_slots) > 1
        support_selected = had_support and previous.active_material_index == 1
        body = previous.material_slots[0].material if support_selected else previous.active_material
        if had_support:
            support = previous.material_slots[1].material
    if body is not None or support_start is not None:
        obj.data.materials.append(body)
    if support_start is not None:
        obj.data.materials.append(support)
        obj["fdm_support_slot"] = True
        indices = np.zeros(len(obj.data.polygons), dtype=np.int32)
        indices[support_start:] = 1
        obj.data.polygons.foreach_set("material_index", indices)
        obj.active_material_index = int(support_selected)


def publish(context, source, settings, result, mm_per_unit, location):
    layers, narrow = result[-2:]
    previous = existing_preview(context.scene, source)
    visible = layers
    imperfections = 0.0
    if previous is not None:
        modifier = node_modifier(previous)
        if modifier:
            ids = preview.sockets(modifier.node_group)
            if "Visible Layers" in ids:
                prior = getattr(modifier.properties.inputs, ids["Visible Layers"]).value
                if prior < previous["fdm_layers"]:
                    visible = max(0, min(layers, prior))
            if "Imperfections" in ids:
                imperfections = getattr(modifier.properties.inputs, ids["Imperfections"]).value
    mesh = bpy.data.meshes.new("FDM joined profiles")
    try:
        result[0].write(mesh)
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    try:
        obj = bpy.data.objects.new(f"{source.name} • FDM Preview", mesh)
    except Exception:
        bpy.data.meshes.remove(mesh)
        raise
    try:
        obj.location = location
        obj["fdm_preview"] = True
        obj["fdm_layers"] = layers
        set_preview_materials(obj, source, previous, result[0].support_start)
        obj.fdm_print.source = source
        for name in SETTING_NAMES:
            setattr(obj.fdm_print, name, getattr(settings, name))
        modifier = obj.modifiers.new("FDMfier", "NODES")
        modifier.node_group = loft.reveal_nodes(layers)
        sockets = preview.sockets(modifier.node_group)
        for name, value in (("Visible Layers", visible), ("Imperfections", imperfections),
                            ("Extrusion Width", settings.width / mm_per_unit),
                            ("Layer Height", settings.layer_height / mm_per_unit)):
            getattr(modifier.properties.inputs, sockets[name]).value = value
        cleanup = obj.modifiers.new("Remove detached infill", "NODES")
        cleanup.node_group = solid.cleanup_nodes()
        socket = preview.sockets(cleanup.node_group)["Minimum Void"]
        getattr(cleanup.properties.inputs, socket).value = (
            0.025 * settings.width ** 2 * settings.layer_height / mm_per_unit ** 3)
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
            self.report({"WARNING"}, f"Generated {layers} layers. Some sections were narrower than the extrusion width.")
        else:
            self.report({"INFO"}, f"Generated {layers} print layers.")
        return {"FINISHED"}

    def execute(self, context):
        try:
            self.prepare(context)
            steps = self.generation_steps()
            try:
                while True:
                    next(steps)
            except StopIteration as completed:
                return self.finish(context, completed.value)
        except (ValueError, RuntimeError, ReferenceError) as error:
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}

    def generation_steps(self):
        return loft.generate_steps(self.vertices, self.faces, self.settings, self.mm_per_unit)

    def invoke(self, context, event):
        global JOB
        self.source = active_source(context)
        self.scene = context.scene
        self.progress = (0.0, "Preparing mesh")
        self.steps = None
        self.result = None
        self.timer = context.window_manager.event_timer_add(0.02, window=context.window)
        context.window_manager.modal_handler_add(self)
        JOB = self
        redraw_preview_progress(context)
        return {"RUNNING_MODAL"}

    def modal(self, context, event):
        if JOB is not self:
            return {"CANCELLED"}
        if event.type == "ESC" and event.value == "PRESS":
            self.cancel(context)
            self.report({"INFO"}, "Print preview generation cancelled.")
            return {"CANCELLED"}
        if event.type != "TIMER":
            return {"PASS_THROUGH"}
        try:
            if self.result is not None:
                status = self.finish(context, self.result)
                self.cancel(context)
                return status
            if self.steps is None:
                self.prepare_snapshot(context)
                self.steps = self.generation_steps()
                return {"RUNNING_MODAL"}

            previous = self.progress
            deadline = perf_counter() + 0.015
            try:
                while True:
                    self.progress = next(self.steps)
                    if perf_counter() >= deadline:
                        break
            except StopIteration as complete:
                self.result = complete.value
                # Let Blender redraw before creating and swapping the mesh.
                self.progress = (0.99, "Creating Blender preview")
            if self.progress != previous:
                redraw_preview_progress(context)
        except Exception as error:
            self.cancel(context)
            self.report({"ERROR"}, str(error))
            return {"CANCELLED"}
        return {"RUNNING_MODAL"}

    def cancel(self, context):
        global JOB
        if JOB is self:
            if self.steps is not None:
                self.steps.close()
            self.steps = self.result = None
            self.vertices = self.faces = self.settings = None
            context.window_manager.event_timer_remove(self.timer)
            JOB = None
            redraw_preview_progress(context)


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
            if not obj.material_slots and source.active_material is not None:
                obj.data.materials.append(source.active_material)
            if obj.material_slots:
                obj.active_material_index = min(source.active_material_index, len(obj.material_slots) - 1)
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
            previous.hide_set(True)
            previous.hide_render = True
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
    if settings.pattern == "GYROID":
        fill_points += (layers - full_layers) * x * y * density / width ** 2 * 2
    if settings.pattern == "CROSS_3D":
        fill_points += (layers - full_layers) * x * y * (density / width) ** 2 * 2
    for depth, pattern in ((settings.skins, settings.top_pattern),
                           (settings.bottom_layers, settings.bottom_pattern)):
        if pattern in {"HILBERT", "ARCHIMEDEAN"}:
            fill_points += min(layers, depth) * x * y / width ** 2 * (2 if pattern == "HILBERT" else 0.35)
    support_points = layers * x * y / max(width * 6, 2, settings.support_spacing) ** 2 * 3 if settings.supports != "OFF" else 0
    skirt_points = 4 * settings.skirt_loops if settings.skirt else 0
    brim_points = 4 * ceil(settings.brim_width / width) if settings.brim else 0
    return layers, round(loft.PROFILE_SAMPLES * (wall_points + fill_points + support_points + skirt_points + brim_points))


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
            if not context.screen.show_statusbar or not hasattr(context.window, "global_areas"):
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
            row = layout.row(align=True)
            row.operator("fdm_print.generate", text="Regenerate" if obj else "Generate")
            if obj:
                row.operator("fdm_print.remove")
        if obj:
            box = layout.box()
            box.label(text=f"{obj['fdm_layers']} generated layers")
            modifier = node_modifier(obj)
            if modifier:
                ids = preview.sockets(modifier.node_group)
                for name in (name for name in LIVE if name in ids):
                    box.prop(getattr(modifier.properties.inputs, ids[name]), "value",
                             text=name, slider=True)



CLASSES = (FDMSettings, FDM_OT_generate, FDM_OT_remove, FDM_OT_repair, FDM_OT_lay_on_face, FDM_OT_auto_orient, FDM_PT_preview)


@persistent
def cancel_generation_on_load(_):
    if JOB is not None:
        JOB.cancel(bpy.context)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Object.fdm_print = PointerProperty(type=FDMSettings)
    bpy.app.handlers.load_pre.append(cancel_generation_on_load)
    bpy.types.STATUSBAR_HT_header.append(draw_generation_progress)


def unregister():
    bpy.types.STATUSBAR_HT_header.remove(draw_generation_progress)
    bpy.app.handlers.load_pre.remove(cancel_generation_on_load)
    if JOB is not None:
        JOB.cancel(bpy.context)
    del bpy.types.Object.fdm_print
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
