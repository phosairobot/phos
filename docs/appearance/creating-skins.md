# Creating a Custom Skin

Build a portable `.phos-skin` package using the implemented version-3 manifest.
This tutorial starts with the real Minimal built-in, creates an asset-free
**Copper pulse** variant, validates it through PHOS, and installs it with Web
Admin. It needs no camera, sensors, or LED hardware to display its eye styling.

For field-by-field rules, use the [configuration reference](../skins/configuration.md).
The [structure guide](../skins/structure.md) describes package paths and limits.

## 1. Create a working directory

Run the following commands from the repository root with PHOS installed in its
existing `.venv`. The directory is an authoring workspace, not a runtime plugin
folder:

```bash
mkdir -p my-skin
```

## 2. Start from a real built-in

Write Minimal's actual exported manifest shape into your workspace. This uses
PHOS's serializer; it does not change the running robot or its configuration.

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
import yaml
from robot.ui.skins import built_in_skins, skin_to_manifest

skin = next(item for item in built_in_skins() if item.id == "minimal")
manifest = skin_to_manifest(skin, skin.appearance)
Path("my-skin/skin.yaml").write_text(
    yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
)
print("Wrote my-skin/skin.yaml")
PY
```

Alternatively, export Minimal from **Appearance → Skins** and extract its
`skin.yaml`. An exported archive has the manifest at its root, without a parent
folder and without image assets for this built-in.

## 3. Define metadata and visual properties

Replace the workspace manifest with the complete example below. It selects an
amber iris, an expressive eye contour, restrained amber State Effects, optional
semantic LED colours, and calm idle timing.

## Complete manifest

```yaml
schema_version: 3
name: Copper pulse
description: Warm irises and restrained effects, with calm idle timing.
appearance:
  eyes:
    iris_color: "#F4AB3D"
    shape: expressive
  backgrounds:
    enabled: false
    idle: null
    listening: null
    thinking: null
    speaking: null
    sleeping: null
    error: null
  eye_safe_layer:
    enabled: true
    mode: subtle
  state_effects:
    enabled: true
    preset: subtle
    palette: amber
    reduce_motion: true
led_palette:
  enabled: true
  colors:
    idle: "#F4AB3D"
    thinking: "#B38CFF"
    success: "#41CD7D"
physical_personality:
  preset: calm
```

`led_palette` is optional and has no physical effect without available
controller-backed LEDs. Warning/error colours remain protected. Physical
Personality is applied through effective-skin reconciliation, not through the
live Preview button.

### Optional background assets

The complete example needs no assets. To add one:

1. Create `my-skin/assets/` and copy your own PNG/JPEG image there, for example
   `idle.png`.
2. Set `appearance.backgrounds.enabled: true` and
   `appearance.backgrounds.idle: assets/idle.png`.
3. Keep unassigned states `null`. For another state, use a separate filename,
   even when the image contents are identical.
4. Prepare 800×600 images for the reference display. Check the
   [package/image limits](../skins/structure.md#package-and-image-limits).

Put only referenced image files in `assets/`. Do not include the PHOS logo as
an eye replacement; backgrounds are composed beneath the animated eyes.

## 4. Keep expressions and animations within supported controls

A skin chooses an eye shape and an effect preset/palette; it does not provide
new expression profiles or animation scripts. Test expressions using the
existing [visual controls](../visual-system.md#eye-expressions). State Effects
follow the actual robot state, and Physical Personality changes bounded idle
blink/gaze timing. `reduce_motion` applies to State Effects, not all eye motion.

Do not add arbitrary `expressions`, `animations`, `geometry`, or timing fields:
they are not manifest extensions and will be rejected.

## 5. Validate the manifest

This invokes the same manifest validator used during package import without
writing any PHOS state:

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
import yaml
from robot.ui.skins import skin_from_manifest

manifest = yaml.safe_load(Path("my-skin/skin.yaml").read_text(encoding="utf-8"))
name, description, appearance, led_palette, personality = skin_from_manifest(manifest)
print(f"Valid manifest: {name}")
PY
```

## 6. Build and validate the archive

This command includes the manifest and immediate asset files, then performs a
real package import into a temporary directory. Temporary validation does not
activate the skin or edit your runtime configuration.

```bash
PYTHONPATH=src .venv/bin/python - <<'PY'
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile
from robot.ui.skins import SkinRepository

workspace = Path("my-skin")
package = Path("copper-pulse.phos-skin")
with ZipFile(package, "w", compression=ZIP_DEFLATED) as archive:
    archive.write(workspace / "skin.yaml", "skin.yaml")
    assets = workspace / "assets"
    if assets.is_dir():
        for asset in sorted(assets.iterdir()):
            if not asset.is_file() or asset.is_symlink():
                raise ValueError("assets/ must contain only regular image files")
            archive.write(asset, f"assets/{asset.name}")

with TemporaryDirectory() as temporary:
    repository = SkinRepository(Path(temporary) / "phos.yaml")
    installed = repository.import_package(package.read_bytes())
    assert repository.get(installed.id).name == "Copper pulse"
    print(f"Validated package: {package}")
PY
```

Keep the example name unchanged for the assertion, or update that assertion
when authoring your own named skin. Manifest validation checks schema and
parameters; package validation additionally checks paths, entries, sizes, asset
references, and supported image signatures. Decoded-image limits require
OpenCV/NumPy, as explained in [Skin Structure](../skins/structure.md).

### Other archive tools

For an image-backed package, the equivalent macOS/Linux commands are separate
operations:

```bash
cd my-skin
```

```bash
COPYFILE_DISABLE=1 zip -X -r ../copper-pulse.phos-skin skin.yaml assets
```

For an asset-free package, omit `assets`:

```bash
zip -X ../copper-pulse.phos-skin skin.yaml
```

On Windows, select `skin.yaml` and `assets/` themselves, not their parent folder,
then create a ZIP archive and give it the `.phos-skin` extension. Exclude
`__MACOSX`, `.DS_Store`, AppleDouble `._*` files, and unrelated files.

## 7. Install and activate

1. Open **Appearance → Skins** in authenticated Web Admin.
2. Import `copper-pulse.phos-skin`. It receives a new local custom ID.
3. Choose **Global Appearance** if you want to assess this global selection
   without automatic active-agent changes.
4. Use **Preview** for a temporary live visual check, then **Apply** to save it.
5. Export the installed skin to keep a portable copy.

Apply uses the shared configuration/lifecycle boundary. Import alone does not
change the appearance. See [Installing and Activating](../skins/installing.md)
for local catalogs, agent assignments, and restoring PHOS Default.

## 8. Test the result

- Verify the amber iris and expressive contour on the actual face display.
  Static SVG Preview currently uses the classic shape.
- Exercise supported expressions; the skin must leave their semantic behavior
  and local state priorities intact.
- Observe idle blink/gaze after **Apply** to assess the `calm` preset.
- With supported hardware, inspect **Active LED Palette** and request only
  permitted signals; no LED hardware is needed for the eye appearance.
- If using backgrounds, inspect each assigned state and confirm the eyes remain
  legible. Check centre-cropping on the target display.
- Restart PHOS through your normal managed/foreground launch and confirm the
  saved global skin still resolves.

For errors or a difference between preview and the live display, use
[Troubleshooting](../skins/troubleshooting.md).
