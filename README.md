# FDMfier for Blender

By Nikita Goryainov - nik.grv. Licensed under GPL-3.0-or-later.

Turn a closed mesh into an FDM-style replica with visible extrusion beads, infill,
a seam, and optional supports, skirt, and brim. The add-on slices the evaluated
mesh, including modifiers. A Geometry Nodes modifier turns its paths into
renderable geometry.

## Benchy renders

![Blue Benchy with visible layer lines](docs/images/benchy_blue_100.png)
*Blue filament with visible layer lines.*

![Blue Benchy with simulated print imperfections and stringing](docs/images/benchy_blue_100_imp.png)
*Blue filament with simulated imperfections.*

![Transparent Benchy with visible extrusion layers](docs/images/benchy_transparent_100.png)
*Transparent material with visible extrusion layers.*

## Install

1. In Blender 5.3 on Apple Silicon, open **Edit → Preferences → Add-ons**.
2. Open the menu in the top right, select **Install from Disk**, and choose
   `FDMfier-1.0.0.zip`. Enable **FDMfier** if needed.
   If you installed a development build, disable it before enabling V1. Blender
   treats the new package ID as a separate extension.
3. Select a solid mesh in Object Mode. Open the 3D Viewport sidebar with **N**
   and choose **FDMfier → Generate**.

The add-on places the preview over the source at the same scene scale and
hides the source. Repair and baking also keep the result in place.
Use **View → Frame Selected** for a closer look.

## Use

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
  layer counts. Choose alternating, parallel, or concentric surface paths.
  Sparse infill also offers grid, triangles, concentric, and honeycomb.
- The vertex estimate uses source bounds, slice settings, and bead resolution.
  Complex contours and support routing can make the actual count differ.
  **Repair / Remesh Copy** welds vertices and fills holes on a copy. If that
  fails to close the mesh, it tries voxel remeshing. Generate from the repaired
  copy. The original and its live preview are hidden.
- **Brim** adds attached tracks around the outside of the first layer. Set
  **Brim Width** in simulated print millimeters. With both enabled, the skirt
  starts beyond the brim.
- Change slicing settings or **Imperfection Mask** and click
  **Regenerate**. Paint a source vertex group to restrict imperfections to
  selected areas. For droop, the painted weight is applied only where an
  exterior edge also lacks support from the preceding model layer. Stringing
  uses the lower weight at its two endpoints. The invert toggle reverses the
  selected weights. A blank group applies imperfections everywhere. Regenerate
  after editing weights. Regenerate existing previews to apply perimeter
  cleanup and the updated bead profile.
- **Visible Layers**, **Seam Placement**, and the live Geometry Nodes controls
  update in viewport and render. **Imperfections** is the overall strength.
  Width Variation, Layer Drift, Stringing, and Overhang Droop have independent
  strength sliders. The sidebar shows generation progress. Press **Esc** to cancel before the final mesh is created.
- Infill defaults to **15%**. Fused baking joins adjacent wall tracks into a
  shell while retaining the air spaces in sparse infill.
- **Bake to Mesh** creates an editable mesh at the current reveal height.
  Enable **Fuse for Translucency** before baking to join beads while preserving
  rounded layer ridges, top grooves, and infill cavities. Fused baking keeps
  the evaluated bead surfaces and joins them with internal bonds. Assign a material
  to the baked mesh in Blender. Baking hides the source and live preview.
  **Show Live Preview** brings the preview back.
- When supports are enabled, **Overhang Angle** measures the minimum angle
  from vertical. Horizontal undersides are 90°. Tree branches share trunks
  that taper gradually toward the bed and widen as they carry more tips.
- Regenerate previews made with earlier releases to use the new scale and
  imperfection controls.

This is a visual FDM replica for closed mesh solids, including meshes with holes
through the solid and disconnected shells. Features thinner than the extrusion
width can disappear. Supports are sampled and can start on the build plate or a lower shelf.
Enable **Build Plate Only** to restrict Standard and Tree supports to the bed.
Increase **Support Spacing** if support generation reaches its limit.
A value of 0 uses automatic spacing. Obstructed overhangs may receive partial coverage. The add-on does not produce
printer toolpaths or G-code. For dense previews, increase layer height or
extrusion width if generation reaches a geometry budget.

## Build and verify

From the workspace root, using the Blender 5.3 executable as `blender`:

```sh
blender -b --command extension build --source-dir ./fdm_print_preview --output-dir .
export BLENDER_USER_RESOURCES="$(mktemp -d)"
blender -b --command extension install-file -r user_default -e ./FDMfier-1.0.0.zip
blender -b --python-exit-code 1 --python tests/smoke.py
```

The package bundles the macOS CPython 3.13 wheel for
[pyclipper 1.4.0](https://pypi.org/project/pyclipper/1.4.0/). Third-party license
notices are included inside the wheel.

## V1 release

See the [release notes and platform testing checklist](RELEASE_NOTES.md).
