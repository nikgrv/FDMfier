"""Check the continuous print mode with the extension enabled in Blender."""

import importlib
from collections import defaultdict

import bmesh
import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree

package = next(name for name in bpy.context.preferences.addons.keys() if name.endswith(".FDMfier"))
addon = importlib.import_module(package)


def check_indexed_polygon():
    # Include horizontal rim edges, long edges crossing several bands, and
    # oblique sections whose vertices land on a query row.
    outlines = (
        [(x, -12) for x in range(-20, 21)] + [(x, 12) for x in range(20, -21, -1)],
        [(x, x % 7 - 12) for x in range(-30, 31)] + [(x, x % 5 + 4) for x in range(30, -31, -1)],
    )
    for ring in outlines:
        index = addon.loft.IndexedRing(ring, addon.slicer.bounds([ring]))
        for x in range(-31, 32):
            for y in range(-13, 14):
                point = x, y
                assert index.locate(point) == addon.slicer.clip.PointInPolygon(point, ring), point


check_indexed_polygon()


def crossings(tree, origin, direction):
    point, axis = Vector(origin), Vector(direction)
    hits = []
    while (hit := tree.ray_cast(point, axis)[0]) is not None:
        hits.append(hit)
        point = hit + axis * 0.00001
    return hits


def check(density, skins):
    source = bpy.context.object
    settings = source.fdm_print
    settings.size = 8
    settings.density = density
    settings.pattern = "GYROID"
    settings.skins = settings.bottom_layers = skins
    settings.top_pattern = "ARCHIMEDEAN"
    settings.bottom_pattern = "HILBERT"
    assert bpy.ops.fdm_print.generate() == {"FINISHED"}
    live = bpy.context.object
    assert addon.node_modifier(live).node_group.get("fdm_joined_reveal")
    mesh = live.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        assert all(edge.is_manifold and edge.is_contiguous for edge in bm.edges)
        assert bm.calc_volume(signed=True) > 0
    finally:
        bm.free()

    # A watertight cavity is a separate negative-volume boundary. A
    # detached infill lattice would add another positive-volume boundary.
    parents = list(range(len(mesh.vertices)))

    def root(index):
        while index != parents[index]:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    for edge in mesh.edges:
        a, b = edge.vertices
        parents[root(a)] = root(b)
    volumes = defaultdict(float)
    mesh.calc_loop_triangles()
    for tri in mesh.loop_triangles:
        a, b, c = (mesh.vertices[index].co for index in tri.vertices)
        volumes[root(tri.vertices[0])] += a.dot(b.cross(c)) / 6
    assert sum(volume > 0 for volume in volumes.values()) == 1
    assert sum(volume < 0 for volume in volumes.values()) == bool(density)

    tree = BVHTree.FromPolygons([v.co for v in mesh.vertices], [tuple(f.vertices) for f in mesh.polygons])
    if density:
        # Both solid skins are present, with air spaces between infill.
        rays = [crossings(tree, (x * 0.087 + 0.008, 0.111, -0.1), (0, 0, 1)) for x in range(-8, 9)]
        assert all(len(ray) >= 2 and len(ray) % 2 == 0 for ray in rays)
        assert any(len(ray) > 2 for ray in rays)
        top_heights = [ray[-1].z for ray in rays]
        assert 0.003 < max(top_heights) - min(top_heights) < 0.06
    else:
        hits = crossings(tree, (-2, 0.111, 0.45), (1, 0, 0))
        assert len(hits) == 4
        assert 0.70 < (hits[1].x - hits[0].x) * 4 < 1.02

    assert bpy.ops.fdm_print.remove() == {"FINISHED"}


check(15, 2)
bpy.ops.mesh.primitive_cylinder_add(vertices=64, radius=1, depth=1)
check(0, 0)
print("FDM continuous solid tests passed")

# Additional wall lines add thickness with the same four material/air
# crossings through the two sides of a hollow print.
bpy.ops.mesh.primitive_cube_add()
source = bpy.context.object
settings = source.fdm_print
settings.size = 10
settings.density = 0
settings.skins = settings.bottom_layers = 0
thicknesses = []
for wall_count in (1, 2, 3):
    settings.walls = wall_count
    assert bpy.ops.fdm_print.generate() == {"FINISHED"}
    live = bpy.context.object
    tree = BVHTree.FromObject(live, bpy.context.evaluated_depsgraph_get())
    hits = crossings(tree, (-2, 0.111, 0.51), (1, 0, 0))
    assert len(hits) == 4
    thicknesses.append(hits[1].x - hits[0].x)
    assert bpy.ops.fdm_print.remove() == {"FINISHED"}
assert thicknesses[0] < thicknesses[1] < thicknesses[2]

# A closed, sub-extrusion-width footing should neither vanish below a
# wider body nor be discarded as a detached infill island.
footing_vertices = [
    (x_sign * half_width, y, z)
    for z, half_width in ((0, 0.022), (0.15, 0.022), (0.20, 0.17), (0.54, 0.54))
    for x_sign, y in ((-1, -1), (1, -1), (1, 1), (-1, 1))
]
footing_faces = [(3, 2, 1, 0), (12, 13, 14, 15)]
for base in (0, 4, 8):
    for corner in range(4):
        a, b = base + corner, base + (corner + 1) % 4
        footing_faces.append((a, b, b + 4, a + 4))
mesh = bpy.data.meshes.new("Thin flared footing")
mesh.from_pydata(footing_vertices, [], footing_faces)
source = bpy.data.objects.new(mesh.name, mesh)
bpy.context.collection.objects.link(source)
bpy.context.view_layer.objects.active = source
source.select_set(True)
settings = source.fdm_print
settings.size = 14
for pattern in (item.identifier for item in addon.FDMSettings.bl_rna.properties["pattern"].enum_items):
    settings.pattern = pattern
    assert bpy.ops.fdm_print.generate() == {"FINISHED"}
    live = bpy.context.object
    modifier = addon.node_modifier(live)
    sockets = addon.preview.sockets(modifier.node_group)
    visible = getattr(modifier.properties.inputs, sockets["Visible Layers"])
    for shown in (live["fdm_layers"], 5):
        visible.value = shown
        tree = BVHTree.FromObject(live, bpy.context.evaluated_depsgraph_get())
        for z in (0.02, 0.06, 0.10, 0.14):
            hits = crossings(tree, (-2, 0, z), (1, 0, 0))
            assert len(hits) == 2, (pattern, shown, z)
            assert 0.038 < hits[1].x - hits[0].x < 0.046, (pattern, shown, z)
    assert bpy.ops.fdm_print.remove() == {"FINISHED"}
print("FDM thin footing test passed")

# Joined Profiles should preserve both parts across a wide empty span.
bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 0))
first = bpy.context.object
first.scale = (2, 2, 4)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
bpy.ops.mesh.primitive_cube_add(size=2, location=(96, 96, 0))
source = bpy.context.object
source.scale = (2, 2, 4)
bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
first.select_set(True)
bpy.ops.object.join()
settings = source.fdm_print
settings.size = 100
settings.pattern = "GYROID"
assert bpy.ops.fdm_print.generate() == {"FINISHED"}
mesh = bpy.context.object.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
bm = bmesh.new()
try:
    bm.from_mesh(mesh)
    assert all(edge.is_manifold and edge.is_contiguous for edge in bm.edges)
finally:
    bm.free()
tree = BVHTree.FromPolygons([v.co for v in mesh.vertices], [tuple(f.vertices) for f in mesh.polygons])
assert not crossings(tree, (0, 0, 20), (0, 0, -1))
print("FDM sparse sampling tests passed")

# Detached infill and tiny cavities disappear. An anchored separate part
# and an enclosed air cavity of deliberate size are preserved.
unit = [(x, y, z) for z in (0, 1) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))]
shell = [(3, 2, 1, 0), (4, 5, 6, 7), (0, 1, 5, 4),
         (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
points, faces, anchored = [], [], []
for x, size, inward, keep in ((0, 1, False, True), (2, 1, False, True), (4, 0.2, False, False),
                              (0.2, 0.6, True, False), (2.2, 0.01, True, False)):
    start = len(points)
    points.extend((x + px * size, 0.2 + py * size, 0.2 + pz * size) for px, py, pz in unit)
    faces.extend(tuple(start + i for i in (face[::-1] if inward else face)) for face in shell)
    anchored.extend([keep] * 8)
mesh = bpy.data.meshes.new("Floating infill and air pockets")
mesh.from_pydata(points, [], faces)
attribute = mesh.attributes.new("fdm_solid_anchor", "BOOLEAN", "POINT")
attribute.data.foreach_set("value", anchored)
obj = bpy.data.objects.new(mesh.name, mesh)
bpy.context.collection.objects.link(obj)
modifier = obj.modifiers.new("Remove detached infill", "NODES")
modifier.node_group = addon.solid.cleanup_nodes()
socket = addon.preview.sockets(modifier.node_group)["Minimum Void"]
getattr(modifier.properties.inputs, socket).value = 0.0001
clean = obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).data
assert len(clean.polygons) == 18
assert max(v.co.x for v in clean.vertices) == 3
print("FDM floating infill cleanup tests passed")
