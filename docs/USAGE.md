# Using FDMfier

- **Simulated Print Size** sets the longest dimension used to calculate slicing
  detail in millimeters. The finished preview is scaled back to the source's
  scene size. World Z is the build direction.
- In Edit Mode, select a flat face or coplanar faces on the outside of the
  source mesh and click **Lay on Selected Face** to put them at world Z = 0.
  It uses the edit mesh before modifiers. **Auto Orient** is available in
  Object and Edit Mode. It scores candidate orientations on the evaluated
  mesh by overhang area, bed contact, and build height. Both actions rotate
  and place the source object. Regenerate an existing preview after reorienting.
- Set layer height, extrusion width, wall lines, and separate top and bottom
  layer counts. Surface patterns include alternating lines, parallel lines,
  concentric, Hilbert Curve, Archimedean Chords, and Octagram Spiral.
  Sparse infill also offers Grid, Triangles, Concentric, Honeycomb, Gyroid,
  Cubic, Cross 3D, and Adaptive Cubic.
- Gyroid, Cubic, and Cross 3D change with slice height. Adaptive Cubic uses
  smaller cells near the model’s surfaces and larger cells inside. Its density
  sets the finest cell spacing. Cross 3D uses FDMfier’s own fractal construction,
  so its paths can differ from Cura’s. Infill at 100% uses solid alternating lines.
- The vertex estimate uses source bounds, slice settings, and bead resolution.
  Complex contours and support routing can make the actual count differ.
  **Repair / Remesh Copy** welds vertices and fills holes on a copy. If that
  fails to close the mesh, it tries voxel remeshing. Generate from the repaired
  copy. The original and its live preview are hidden.
- **Brim** adds attached tracks around the outside of the first layer. Set
  **Brim Width** in simulated print millimeters. With both enabled, the skirt
  starts beyond the brim.
- **Generate** builds a continuous Joined Profiles mesh with rounded layers,
  patterned skins, and air spaces between sparse infill. It is ready to render
  with translucent materials. Change print settings and click **Regenerate**
  to rebuild it. The status bar shows progress while Blender works. Press **Esc** to cancel.
- **Visible Layers** works in the sidebar and the Geometry Nodes modifier.
  Partial reveals show a closed cross-section with the infill and air gaps
  visible. The slider uses the generated mesh in viewport and render.
- **Imperfections** adds mild surface variation, stringing, and sag along
  unsupported edges. Select a vertex group to limit the effect and regenerate
  after editing the group or switching its invert toggle.
- Generate uses the source's active material. Regenerate keeps the preview's
  material. Standard and Tree supports use a separate material slot.
- **Remove Preview** is next to Regenerate and brings the source back.
- When supports are enabled, **Overhang Angle** measures the minimum angle
  from vertical. Horizontal undersides are 90°. Tree branches share trunks
  that taper gradually toward the bed and widen as they carry more tips.
- Regenerate earlier previews to use Joined Profiles and its reveal slider.

This is a visual FDM replica for closed mesh solids, including meshes with holes
through the solid and disconnected shells. Features thinner than the extrusion
width can disappear. Supports are sampled and can start on the build plate or a lower shelf.
Enable **Build Plate Only** to restrict Standard and Tree supports to the bed.
Increase **Support Spacing** if support generation reaches its limit.
A value of 0 uses automatic spacing. Obstructed overhangs may receive partial coverage. The add-on does not produce
printer toolpaths or G-code. For dense previews, increase layer height or
extrusion width if generation reaches a geometry budget.

## Build and verify

From the workspace root, using Blender 5.2 or later as `blender`:

```sh
blender -b --command extension build --source-dir ./fdmfier --output-dir . --split-platforms
export BLENDER_USER_RESOURCES="$(mktemp -d)"
blender -b --command extension install-file -r user_default -e ./FDMfier-1.1.1-macos_arm64.zip
blender -b --python-exit-code 1 --python tests/smoke.py
blender -b --python-exit-code 1 --python tests/infill.py
blender -b --python-exit-code 1 --python tests/solid.py
```

The install command shows the Apple Silicon ZIP as an example. Choose the ZIP
for the system you're testing. The Linux x64 package includes CPython 3.13
and 3.14 wheels for pyclipper 1.4.0. The macOS and Windows packages include
CPython 3.13 wheels. Blender selects the wheel for its own Python version.
Third-party license notices are included inside the wheels.

Use a local 3DBenchy STL to check the 250-layer Gyroid reveal and the lower
hull across all infill patterns.

```sh
blender -b --python-exit-code 1 --python tests/benchy.py -- /path/to/3DBenchy.stl
blender -b --python-exit-code 1 --python tests/benchy.py -- /path/to/3DBenchy.stl --all-patterns
```
