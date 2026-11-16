# FDMfier

**Developer** Nikita Goryainov - nik.grv
**Version** 1.0.9
**Category** Modeling
**Compatibility** Requires Blender 5.2 LTS or later. Tested with Blender 5.2 on Windows x64 and Blender 5.3 on Apple Silicon. Linux x64 and Intel Macs are still untested. Intel Macs require a custom Blender build with Python 3.13.
**License** GPL-3.0-or-later

## Short description

Give your Blender models the look of an FDM print, with visible layer lines, patterned infill, and a little stringing if you want it.

## Overview

Start with a solid mesh and choose how you want the print to look. Set the layer height, walls, and infill, pick a pattern for the top and bottom, and add standard or tree supports if the model calls for them. You can add a skirt or brim and limit supports to the build plate.

Reveal the print a layer at a time to see what’s inside. Adjust seams, stringing, layer drift, and overhang droop live, then bake the visible layers to an editable mesh. For a translucent material, **Fuse for Translucency** joins overlapping beads while keeping the layer ridges, top pattern, and gaps in sparse infill. The sidebar also has tools to repair a mesh and orient it for the preview.

FDMfier is made for Blender renders. Use a dedicated slicer to prepare a print and export G-code.

Open the 3D Viewport sidebar with **N**, select **FDMfier**, and click **Generate** with a solid mesh selected.

## Submission

Build with `blender --command extension build --source-dir ./fdmfier --split-platforms`. Upload the Windows x64, Linux x64, Apple Silicon, and Intel Mac ZIPs individually under version 1.0.9 of the same Blender Extensions listing. Each ZIP includes its platform’s `pyclipper` wheel and license notices. The extension runs offline. The package ID is `FDMfier`.

Suggested image caption: Fused print with a concentric top pattern, rounded layer ridges, and a first-layer brim.
