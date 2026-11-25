# Skin Structure

PHOS has two authoring formats: a portable **skin package** and a local **YAML
catalog**. Their schema versions and asset paths are different. Neither format
contains Python, JavaScript, fonts, shaders, or executable plugins.

## Portable package

`.phos-skin` is a ZIP-compatible archive; Web Admin also accepts `.zip`.
`skin.yaml` must be at the root. `assets/` is optional and may contain only
referenced PNG/JPG/JPEG files directly inside it.

```text
copper-pulse.phos-skin
├── skin.yaml             # Required UTF-8 YAML manifest, schema version 3
└── assets/               # Optional; omit for a background-free skin
    ├── idle.png          # Referenced as assets/idle.png
    └── listening.jpg     # Referenced as assets/listening.jpg
```

This is the package layout used by the existing exporter/importer. There is no
required directory per built-in skin: the built-ins are data definitions in
`src/robot/ui/skins.py`. Exporting the real `minimal` built-in produces this
complete archive, with no assets:

```text
minimal.phos-skin
└── skin.yaml             # Minimal's full appearance, exported as schema version 3
```

### Manifest and filenames

- Required metadata: `schema_version`, `name`, `description`, and `appearance`.
- Required appearance blocks: `eyes`, `backgrounds`, `eye_safe_layer`, and
  `state_effects`. Unknown blocks and parameters are rejected.
- Optional version-3 blocks: `led_palette` and `physical_personality`.
- No package ID: import always creates a new local custom ID.
- Asset names start with an ASCII letter or digit, followed by letters, digits,
  dots, underscores, spaces, or hyphens, and end in `.png`, `.jpg`, or `.jpeg`.
- No nested asset directories, absolute paths, backslashes, `..` segments,
  symlinks, duplicate ZIP entries, or unreferenced files.
- The exact `assets/` directory entry may be present or omitted. Other directory
  entries, including a parent skin folder or `__MACOSX/`, are rejected.

The manifest validator accepts legacy version `2` without the version-3 optional
blocks. New exports use version `3`. The internal version-1 YAML import helper
is not the public Web Admin upload format.

### Package and image limits

| Limit | Value |
| --- | --- |
| Archive size | At most 32 MiB |
| Total declared unpacked size | At most 32 MiB |
| Entry count, including manifest/directory entries | At most 16 |
| Individual entry, including manifest | At most 4 MiB |
| Individual background image | At most 4 MiB |
| Decoded image dimensions when OpenCV/NumPy are available | Width ≤1920 and height ≤1080 |

The importer checks file signatures and PNG/JPEG extension consistency. It
performs decoded-image checks when OpenCV and NumPy are available; without them
that decoding/dimension check is skipped. Prepare and validate images on the
normal Pi installation with OpenCV. For the reference display, 800×600 images
avoid aspect-ratio cropping.

The current importer writes each state's background separately with exclusive
file creation. Use a **distinct filename for each assigned state**, even when
images have identical contents. Reusing one filename across states currently
fails import; it is not a supported packaging shortcut.

## The shipped catalog

The real repository layout is:

```text
config/
├── phos.yaml             # Canonical runtime configuration; appearance.config_file: skins.yaml
└── skins.yaml            # Catalog schema 1; contains the Aurora definition
src/robot/ui/
└── skins.py              # PHOS Default, Minimal, Ambient, repository and validators
```

The complete `config/skins.yaml` is:

```yaml
schema_version: 1
skins:
  - id: aurora
    display_name: Aurora
    inherits: phos-default
    physical_personality:
      preset: calm
    appearance:
      eyes:
        iris_color: "#B38CFF"
```

Catalog entries use stable lowercase IDs and may inherit another catalog entry
or a built-in. `inherits: null` uses `phos-default`. Appearance dictionaries
merge recursively; lists/scalars replace their parent values. Catalog LED
palettes can inherit and override individual colours. Inheritance cycles,
unknown parents, depth beyond eight, and IDs conflicting with other sources
are rejected.

Catalog backgrounds use managed configuration-relative paths such as
`backgrounds/idle.png`, not package-relative `assets/idle.png`. Referenced catalog
images must already exist beneath the configuration directory's `backgrounds/`.

## Managed installation storage

Import/create operations write relative to the **active configuration file**,
not necessarily the repository's `config/` directory:

```text
<active-configuration-directory>/
├── phos.yaml
├── skins.yaml                       # Optional configured catalog
├── skins.json                       # Managed custom metadata; written atomically
└── backgrounds/
    └── skins/
        └── custom-<uuid-hex>/        # Generated local ID, not an author-chosen name
            ├── idle.png
            └── listening.jpg
```

Use package import/export rather than manually editing `skins.json`. Import
rewrites `assets/...` paths into this namespace; export rewrites them back and
bundles referenced images. Store no credentials, transcripts, or runtime state
in skin metadata.
