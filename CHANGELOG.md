# Changelog

Full release notes and installable packages are available on [GitHub Releases](https://github.com/nikgrv/FDMfier/releases).

## 1.1.1

- Rebuilt the preview solver to generate a render-ready mesh in one step, replacing the bake and fusion workflow.
- Reduced generation time on a 250-layer Gyroid Benchy by 41.8% compared with an earlier 1.1.1 development build. The meshes matched exactly.
- Improved thin lower walls, profile joins, and layer reveal for dense prints.
- Preserved preview materials on regeneration and gave both support styles a separate material slot.
- Moved generation progress to Blender’s status bar, with a sidebar fallback.
- Bundled Linux x64 wheels for Python 3.13 and 3.14. Thanks to [@wonderrozz-coder](https://github.com/wonderrozz-coder) for diagnosing and testing the fix. [Issue #1](https://github.com/nikgrv/FDMfier/issues/1)

[Read the 1.1.1 release notes](docs/releases/1.1.1.md).

## 1.0.10

- Added Gyroid, Cubic, Cross 3D, and Adaptive Cubic infill.
- Added Hilbert Curve, Archimedean Chords, and Octagram Spiral as independent top and bottom surface choices.
- Updated the vertex estimate for the new curved and surface paths.
- Checked hole preservation, solid surface coverage, changing infill density, and closed geometry with both fusion methods.
- Refreshed the repository page with clearer downloads, pattern examples, release badges, and a usage guide.

Adaptive Cubic’s density sets the finest cell spacing, with larger cells farther from the source surface. Cross 3D uses FDMfier’s own fractal construction and can differ from Cura’s paths.

Tested with Blender 5.3 on Apple Silicon. Windows, Linux, and Intel Mac runtime testing is pending for this release. The minimum Blender version remains 5.2 LTS. Intel Macs require a custom Blender build with Python 3.13.

## 1.0.9

- Added separate platform ZIPs built with Blender’s `--split-platforms` command, alongside the unified GitHub download.
- Moved preview generation to small steps on Blender’s main thread and removed Python threading and queue usage.
- Added a baking status message before the synchronous fused bake.
- Removed `restore_fusion_method` from startup and file load. Original Boolean remains the default.

## 1.0.8

- Corrected the extension manifest to use schema `1.0.0`.
- Corrected the supported platform identifiers, including Linux x64.
- Set the default layer height to 0.30 mm in the revised packages.

## 1.0.7

- Added Blender 5.2 LTS support, tested by the maintainer on Windows x64.
- Offered Original Boolean and Four-Layer Boolean fusion methods, with Original Boolean as the default.

## 1.0.1

- Added bundled dependencies for additional platforms, including Windows, Linux, and custom Intel Mac builds.

## V1 · 1.0.0

- Introduced layered FDM previews with configurable walls, infill, surface patterns, supports, skirts, and brims.
- Added live layer reveal, seams, and adjustable imperfections.
- Added orientation tools, repair copies, editable mesh baking, and Fuse for Translucency.
- Tested the initial release on Apple Silicon.
