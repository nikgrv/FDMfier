# FDMfier

**Developer** Nikita Goryainov - nik.grv
**Version** 1.0.10
**Category** Modeling
**Compatibility** Requires Blender 5.2 LTS or later. Version 1.0.10 was tested with Blender 5.3 on Apple Silicon. An earlier release was tested with Blender 5.2 on Windows x64. Runtime testing of 1.0.10 on Windows, Linux, and Intel Mac builds is pending. Intel Macs require a custom Blender build with Python 3.13.
**License** GPL-3.0-or-later

## Short description

Give your Blender models the look of an FDM print, with visible layer lines, patterned infill, and a little stringing if you want it.

## Overview

Start with a solid mesh and choose how you want the print to look. Set the layer height, walls, and infill, pick a pattern for the top and bottom, and add standard or tree supports if the model calls for them. You can add a skirt or brim and limit supports to the build plate.

Reveal the print a layer at a time to see what’s inside. Adjust seams, stringing, layer drift, and overhang droop live, then bake the visible layers to an editable mesh. For a translucent material, **Fuse for Translucency** joins overlapping beads while keeping the layer ridges, top pattern, and gaps in sparse infill. The sidebar also has tools to repair a mesh and orient it for the preview.

Version 1.0.10 adds Gyroid, Cubic, Cross 3D, and Adaptive Cubic infill, plus Hilbert Curve, Archimedean Chords, and Octagram Spiral for the top and bottom surfaces. Adaptive Cubic uses smaller cells near model surfaces and larger cells inside. Cross 3D uses FDMfier’s own fractal construction.

FDMfier is made for Blender renders. Use a dedicated slicer to prepare a print and export G-code.

Open the 3D Viewport sidebar with **N**, select **FDMfier**, and click **Generate** with a solid mesh selected.

## Submission

Build with `blender --command extension build --source-dir ./fdmfier --split-platforms`. Upload the Windows x64, Linux x64, Apple Silicon, and Intel Mac ZIPs individually under version 1.0.10 of the same Blender Extensions listing. Each ZIP includes its platform’s `pyclipper` wheel and license notices. The extension runs offline. The package ID is `FDMfier`.

Suggested image caption: Fused print with a concentric top pattern, rounded layer ridges, and a first-layer brim.
