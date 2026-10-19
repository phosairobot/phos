# Creating a PHOS Skin

A PHOS Skin is a portable visual package. It can set eye colour, a background
for each robot state, the Eye Safe Layer, and State Effects. It does not change
voice commands, robot behaviour, sensors, or temporary runtime state.

This guide is for designers, makers, and contributors. You need only a text
editor for YAML, image files, and a way to create a ZIP archive.

## Package format

A Skin is a ZIP-compatible archive with the recommended extension
`.phos-skin`. The manifest, `skin.yaml`, must be at the root of the archive.
`assets/` is optional; include it only when the manifest references images.

```text
my-skin.phos-skin
├── skin.yaml
└── assets/
    ├── idle.png
    ├── listening.png
    ├── thinking.png
    ├── speaking.png
    ├── sleeping.png
    └── error.png
```

The archive must contain `skin.yaml`, not `my-skin/skin.yaml`. Asset paths are
relative to the archive and use `assets/<filename>`.

Only PNG, JPG, and JPEG background images are supported. Put only assets that
are referenced by `skin.yaml` in the archive.

## Complete Skin example

This is the canonical schema-version-2 manifest for a Skin with one image for
each state. The filenames must exactly match the files in `assets/`.

```yaml
schema_version: 2
name: Northern lights
description: Cool aurora backgrounds with a violet iris.
appearance:
  eyes:
    iris_color: violet
  backgrounds:
    enabled: true
    idle: assets/idle.png
    listening: assets/listening.png
    thinking: assets/thinking.png
    speaking: assets/speaking.png
    sleeping: assets/sleeping.png
    error: assets/error.png
  eye_safe_layer:
    enabled: true
    mode: balanced
  state_effects:
    enabled: true
    preset: balanced
    palette: aqua
    reduce_motion: false
```

`schema_version` must be `2`. `name` and `description` must both be strings;
an empty description is allowed.

## Background states

The `backgrounds` block always contains `enabled` and all six state keys:

| State | Used while PHOS is… |
| --- | --- |
| `idle` | waiting normally |
| `listening` | listening |
| `thinking` | processing |
| `speaking` | speaking |
| `sleeping` | sleeping |
| `error` | showing an error state |

Set `enabled` to `true` to use configured backgrounds. Each state can use a
different asset, or `null` when no image is assigned for that state. For
example:

```yaml
backgrounds:
  enabled: true
  idle: assets/idle.png
  listening: assets/listening.png
  thinking: null
  speaking: null
  sleeping: null
  error: null
```

Every non-`null` asset value must be a matching `assets/<safe-filename>.png`,
`.jpg`, or `.jpeg` file in the package. The same asset may be used by more than
one state.

## Eyes, Eye Safe Layer, and State Effects

### Eyes

`eyes.iris_color` selects the iris colour. Supported values are:
`cyan`, `blue`, `green`, `turquoise`, `amber`, `violet`, and `white`.

### Eye Safe Layer

The Eye Safe Layer adds a contrast-friendly halo behind the eyes so they stay
readable over a detailed or bright background. It does not alter your image
file.

```yaml
eye_safe_layer:
  enabled: true
  mode: balanced
```

Supported modes are `subtle`, `balanced`, and `expressive`. `balanced` is the
default and is a good starting point for photographic backgrounds.

### State Effects

State Effects add the selected visual treatment to the appearance. All four
fields below are required even when effects are disabled:

```yaml
state_effects:
  enabled: true
  preset: balanced
  palette: phos
  reduce_motion: false
```

- `enabled` is `true` or `false`.
- `preset` is `subtle`, `balanced`, or `expressive`.
- `palette` is `phos`, `cyan`, `blue`, `violet`, `aqua`, `amber`, `white`, or
  `green`.
- `reduce_motion` is `true` or `false`.

## Minimal Skin: no image assets

Use this complete manifest when you only want to change the eyes, Eye Safe
Layer, or State Effects. It needs no `assets/` directory.

```yaml
schema_version: 2
name: Quiet cyan
description: A background-free, low-motion appearance.
appearance:
  eyes:
    iris_color: cyan
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
    enabled: false
    preset: subtle
    palette: phos
    reduce_motion: true
```

## Image requirements

PHOS validates these hard limits when importing package images:

- PNG, JPG, and JPEG only.
- Each image must be no larger than 4 MiB.
- Each decoded image must be at most 1920×1080 pixels.
- The entire archive must be no larger than 32 MiB, contain at most 16 entries,
  and expand to at most 32 MiB.

For the best result on the standard PHOS display, prepare backgrounds at
800×600 pixels (4:3). This is a recommendation, not the maximum accepted
size. An image with a different aspect ratio is still subject to the hard
limits above, but may need cropping or scaling to look right.

## Create the archive

### macOS and Linux

Arrange the files in a directory named, for example, `my-skin`:

```text
my-skin/
├── skin.yaml
└── assets/
    └── idle.png
```

From inside that directory, create a clean archive:

```bash
cd my-skin
COPYFILE_DISABLE=1 zip -X -r ../my-skin.phos-skin skin.yaml assets
```

This command adds the contents of `my-skin`, rather than the `my-skin`
directory itself. `-X` omits extra file attributes and `COPYFILE_DISABLE=1`
prevents macOS copy metadata from being added. Do not include `__MACOSX`,
`.DS_Store`, or AppleDouble files whose names start with `._`.

For the minimal example, omit `assets` from the command:

```bash
cd my-skin
zip -X ../my-skin.phos-skin skin.yaml
```

### Windows

In File Explorer, select `skin.yaml` and the `assets` folder themselves (not
their parent folder), then use **Send to → Compressed (zipped) folder**. Rename
or copy the resulting `.zip` file so it ends in `.phos-skin` if needed. Open
the archive once to confirm that `skin.yaml` appears at its top level.

## Before importing

- [ ] `skin.yaml` is at the archive root.
- [ ] `schema_version` is `2`.
- [ ] The manifest has exactly `eyes`, `backgrounds`, `eye_safe_layer`, and
      `state_effects` under `appearance`.
- [ ] All six background-state keys are present, and every referenced asset
      exists with exactly the same filename and extension.
- [ ] The archive has no duplicate files, extra top-level files, nested asset
      directories, `__MACOSX`, `.DS_Store`, or `._*` files.
- [ ] Assets are PNG/JPG/JPEG files directly inside `assets/`.
- [ ] No asset path is absolute, uses backslashes, or contains `..`.
- [ ] The package and images meet the limits in [Image requirements](#image-requirements).

## Import, preview, apply, and export

In Web Admin, open **Appearance → Skins → Import Skin Package**. PHOS accepts
`.phos-skin` (recommended) and `.zip` (compatibility) archives. `skin.yaml` is
only the manifest inside a package; YAML files are not a public import format.

**Preview** displays the Skin without changing the live PHOS appearance.
**Apply** updates the live appearance. If a custom Skin is in use, PHOS will not
let it be deleted while its own background assets are still referenced; apply a
different Skin first.

Use **Export** to create a `.phos-skin` package. When exporting a custom Skin,
PHOS bundles its referenced backgrounds under `assets/`, making the package
portable to another PHOS installation.

## Troubleshooting

| Message or symptom | What to check |
| --- | --- |
| `Skin package has unsafe archive entries` | Recreate the archive with `skin.yaml` at its root. Remove `__MACOSX`, `.DS_Store`, `._*` files, nested directories, absolute paths, backslashes, and `..` paths. |
| `Skin appearance must contain ...` | Use the exact `appearance` structure in this guide. All four blocks are required. |
| `Skin state_effects is invalid` | Include `enabled`, `preset`, `palette`, and `reduce_motion`, using only the values listed above. |
| A referenced asset is missing or invalid | Check spelling and case, including `.jpg` versus `.png`; place the image directly in `assets/`. |
| Package or image is too large | Keep each image at or below 4 MiB, each decoded image within 1920×1080, and the archive within 32 MiB. |

For Web Admin setup and the related appearance controls, see the
[Web Administration manual](../web-administration.md#appearance).
