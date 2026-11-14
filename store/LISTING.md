# FDMfier

**Developer** Nikita Goryainov - nik.grv
**Version** 1.0.1
**Category** Modeling
**Compatibility** Tested with Blender 5.3 on Apple Silicon. Windows x64, Linux x64/ARM64, and Intel Mac builds are untested. The Intel Mac package requires a custom Blender 5.3 build with Python 3.13. Blender 4.5 LTS was the last official Intel Mac release.
**License** GPL-3.0-or-later

## Short description

Turn solid meshes into editable FDM print previews with visible layer lines, patterned infill, supports, and adjustable print imperfections.

## Overview

FDMfier slices solid meshes into geometry you can inspect, edit, and render in Blender. Set the layer height, wall thickness, infill density, and top and bottom patterns. Add standard or tree supports, a skirt, or a brim. Restrict supports to the build plate when needed. Reveal the print layer by layer to inspect the interior, or use the live controls for seams, stringing, layer drift, and overhang droop.

The sidebar also includes mesh repair and print orientation tools. Bake the visible layers to an editable mesh and enable **Fuse for Translucency** to join overlapping paths while retaining exterior layer ridges, top grooves, and sparse infill cavities. FDMfier creates visual previews for Blender. Printer toolpaths and G-code require a separate slicer.

Open the 3D Viewport sidebar with **N**, select **FDMfier**, and click **Generate** with a solid mesh selected.

## Submission

Use the validated `FDMfier-1.0.1.zip` and the included preview render for the [Blender Extensions submission](https://extensions.blender.org/about/). The ZIP contains the extension's Python source, documentation, GPL notice, and bundled pyclipper wheel with its license notices. The extension runs offline. The package ID is `FDMfier`.

Suggested image caption: Fused print with a concentric top pattern, rounded layer ridges, and a first-layer brim.
