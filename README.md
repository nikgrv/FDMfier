<div align="center">

# FDMfier

FDM print previews for Blender, with visible layers, patterned infill, and adjustable imperfections.

[![Latest release](https://img.shields.io/github/v/release/nikgrv/FDMfier?label=release&color=2563eb)](https://github.com/nikgrv/FDMfier/releases/latest)
[![Blender 5.2 or later](https://img.shields.io/badge/Blender-5.2%2B-e87d0d?logo=blender&logoColor=white)](#download)
[![GitHub downloads](https://img.shields.io/github/downloads/nikgrv/FDMfier/total?label=GitHub%20downloads&color=475569)](https://github.com/nikgrv/FDMfier/releases)
[![GPL v3 or later](https://img.shields.io/badge/license-GPL--3.0--or--later-475569)](LICENSE)

**[Download the latest release](https://github.com/nikgrv/FDMfier/releases/latest)** · **[Blender Extensions](https://extensions.blender.org/add-ons/fdmfier/)** · **[Usage guide](docs/USAGE.md)** · **[Changelog](CHANGELOG.md)**

</div>

![Blue Benchy rendered with FDMfier, showing rounded layer lines and a patterned top surface](docs/images/benchy_blue_100.png)

FDMfier makes a closed mesh look like it came off an FDM printer. Set the layer
height, walls, and infill, then add supports or a little stringing. Generate
creates a render-ready preview at the source model’s size and position.

[Read the 1.1.1 release notes](docs/releases/1.1.1.md).

## Download

Requires **Blender 5.2 LTS or later**. Choose the unified ZIP or a platform ZIP
from the latest release. [GitHub Releases](https://github.com/nikgrv/FDMfier/releases/latest)

| Platform | ZIP suffix | Blender’s Python version in 1.1.1 |
| --- | --- | --- |
| Apple Silicon | `macos_arm64.zip` | 3.13 |
| Windows x64 | `windows_x64.zip` | 3.13 |
| Linux x64 | `linux_x64.zip` | 3.13 or 3.14 |
| Intel Mac | `macos_x64.zip` | 3.13, via a compatible custom Blender build |

1. Open **Edit → Preferences → Add-ons**, open the menu, and choose **Install from Disk**.
2. Select the downloaded ZIP and enable **FDMfier** if needed.

## Quick start

1. In Object Mode, select a closed mesh and open the 3D Viewport sidebar with
   **N**. Choose **FDMfier**.
2. Set the layer height, wall count, and infill, then click **Generate**. The
   result is ready to render in place, and the source object is hidden.
3. Adjust **Visible Layers** or **Imperfections** live. To change slicing
   settings or update a preview made before 1.1.1, click **Regenerate**.
4. Assign materials to the preview in **Material Properties**. **Remove
   Preview** restores the source.

[Full usage guide](docs/USAGE.md)

## Infill and surface patterns

Choose each surface pattern independently, then regenerate the preview.

![FDMfier’s Gyroid, Cubic, Cross 3D, Adaptive Cubic, Hilbert Curve, Archimedean Chords, and Octagram Spiral rendered in Blender](docs/images/patterns_1_0_10.png)

| Infill | Top and bottom surfaces |
| --- | --- |
| Lines · Parallel Lines · Grid · Triangles · Concentric · Honeycomb | Alternating Lines · Parallel Lines · Concentric |
| **Gyroid · Cubic · Cross 3D · Adaptive Cubic** | **Hilbert Curve · Archimedean Chords · Octagram Spiral** |

Adaptive Cubic makes smaller cells near model surfaces and larger cells inside.
Its density sets the finest cell spacing. Cross 3D uses FDMfier’s own fractal
construction, so its paths can differ from Cura’s.

## Adjust the print

| Control | What you can change |
| --- | --- |
| Layers and walls | Layer height, extrusion width, wall count, and separate top and bottom layer counts |
| Imperfections | Surface variation, stringing, and droop, with an optional vertex group mask and invert control |
| Supports | Standard or Tree, an overhang angle threshold, and Build Plate Only |
| Bed adhesion | Skirt loops and offset, or brim width |
| Live preview | Visible Layers and Imperfections in viewport and render |
| Preparation | Lay on Selected Face, Auto Orient, a vertex estimate, and Repair / Remesh Copy |

Slicing changes take effect on **Regenerate**. Live Geometry Nodes controls
update immediately. Default settings use **0.30 mm layers**, **two wall lines**,
**15% Grid infill**, and **four top and bottom layers**.

## Materials and translucency

<table>
<tr>
<td width="50%"><img src="docs/images/benchy_transparent_100.png" alt="Translucent Benchy with visible FDM layers" width="100%"></td>
<td width="50%"><img src="docs/images/benchy_blue_100_imp.png" alt="Blue Benchy with stringing and other print imperfections" width="100%"></td>
</tr>
<tr>
<td>Translucent material</td>
<td>Adjustable print imperfections</td>
</tr>
</table>

Generation connects neighboring extrusions into a continuous shell with
rounded layer ridges. The selected wall count sets the shell thickness, while
spaces between sparse infill tracks remain hollow. **Generate** uses the
source’s active material. **Regenerate** keeps the materials assigned to
the preview, including the separate support material slot.

FDMfier creates visual replicas for renders. Use a dedicated slicer to prepare
printer toolpaths and G-code. The input should be a closed mesh solid. See the
usage guide for thin features, support coverage, and geometry limits.

## Roadmap

- [ ] Improve slicing and mesh generation performance while retaining layer detail and translucent rendering quality.
- [ ] Test the current release on Windows, Linux, and Intel Mac builds.
- [ ] Add easier controls for print animations.
- [ ] Expand infill and surface pattern options.
- [x] Support Blender 5.2 LTS.
- [x] Confirm an earlier release works on Windows x64 with Blender 5.2.
- [x] Add Gyroid, Cubic, Cross 3D, Adaptive Cubic, and more surface patterns.

[Usage and build instructions](docs/USAGE.md) · [Release history](CHANGELOG.md) · [Report an issue](https://github.com/nikgrv/FDMfier/issues)

Made by **Nikita Goryainov — nik.grv**. Licensed under **GPL-3.0-or-later**.
