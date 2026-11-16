# FDMfier 1.0.9

- GitHub offers a unified ZIP and four platform ZIPs. Each platform ZIP contains its matching `pyclipper` wheel.
- Preview generation now runs in small steps on Blender’s main thread and still shows progress. The add-on no longer uses Python threading or queues.
- Fused baking shows **Baking fused mesh…** before Blender pauses. Both fusion methods use the same geometry as 1.0.8.
- Removed the legacy fusion-setting reset from startup and file load. Original Boolean remains the default.

## Downloads

Install `FDMfier-1.0.9.zip` or the package matching your platform through **Edit → Preferences → Add-ons → Install from Disk**.

| Platform | ZIP |
| --- | --- |
| Apple Silicon | `FDMfier-1.0.9-macos_arm64.zip` |
| Intel Mac | `FDMfier-1.0.9-macos_x64.zip` |
| Windows x64 | `FDMfier-1.0.9-windows_x64.zip` |
| Linux x64 | `FDMfier-1.0.9-linux_x64.zip` |

This release was tested in Blender 5.3 on Apple Silicon. The other platform packages were built and validated. Runtime testing on those platforms is pending for 1.0.9. The minimum version is Blender 5.2 LTS. Intel Mac requires a custom Blender build with Python 3.13.
