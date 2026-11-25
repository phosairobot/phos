# Skin Configuration Reference

Portable manifests, local catalogs, and runtime selection are separate schemas.
A package must supply its required fields; the baseline column below describes
**PHOS Default**, not values the importer inserts for missing required fields.
Only eye shape and the explicitly optional blocks have omission behavior.

## Portable manifest metadata

| Parameter | Type | Required | Default / accepted values | Example |
| --- | --- | --- | --- | --- |
| `schema_version` | Integer | Yes | No default; `3` for new packages; legacy `2` without version-3 optional blocks is accepted | `3` |
| `name` | String | Yes | No default; use a nonempty name, at most 80 characters, for persistent custom records | `Copper pulse` |
| `description` | String | Yes | No default; empty allowed; at most 240 characters for persistent custom records | `Warm irises and restrained effects.` |
| `appearance` | Mapping | Yes | Exactly the four blocks below | See the [complete tutorial](../appearance/creating-skins.md#complete-manifest). |
| `led_palette` | Mapping or null | No | Omitted/null: no skin palette | See [semantic LEDs](#semantic-led-palette). |
| `physical_personality` | Mapping or null | No | Omitted/null: no skin preset; reconciliation falls back to `analytical` | `{preset: calm}` |

Metadata lengths are checked when managed custom records are loaded; use the
limits above even though initial package-manifest validation checks only their
string types. Packages do not accept `id`, `inherits`, or `display_name`.

## Appearance fields

Every package includes `eyes`, `backgrounds`, `eye_safe_layer`, and
`state_effects` beneath `appearance`, even when a feature is disabled.

### Eyes

| Parameter | Type | Required | PHOS Default / omission | Accepted values | Example |
| --- | --- | --- | --- | --- | --- |
| `appearance.eyes.iris_color` | String | Yes | `#28CEEB`; no omission default | `#RRGGBB` or the named presets below; normalized to uppercase hex | `"#F4AB3D"` |
| `appearance.eyes.shape` | String | No | `classic_oval` | `classic_oval`, `round`, `expressive`, `angular`, `compact` | `expressive` |

| Iris preset | Canonical colour |
| --- | --- |
| `cyan` | `#28CEEB` |
| `blue` | `#4985FF` |
| `green` | `#41CD7D` |
| `turquoise` | `#23C8AF` |
| `amber` | `#F4AB3D` |
| `violet` | `#A670F5` |
| `white` | `#DAECF6` |

Quote hex colours in YAML so `#` is not parsed as a comment. Iris colours do not
accept alpha, gradients, texture paths, or animation definitions. Semantic
eye-body accents remain controlled by the existing expression/state pipeline;
the selected iris is not a replacement colour for every pixel.

| Shape | Renderer treatment |
| --- | --- |
| `classic_oval` | Existing oval rendering profile. |
| `round` | Circular open-eye geometry. |
| `expressive` | Custom contour with lifted outer corners. |
| `angular` | Winged contour with a tapered inner point. |
| `compact` | Reduced vertical radius and compact contour. |

Packages cannot set pixel positions, eye dimensions, custom meshes, expression
profiles, or arbitrary blink/gaze curves. These five shapes are the supported
geometry choices. Validate custom shapes on the live display; the static SVG
preview does not currently pass the selected shape to its renderer.

### State backgrounds

| Parameter | Type | Required | PHOS Default | Accepted values | Example |
| --- | --- | --- | --- | --- | --- |
| `appearance.backgrounds.enabled` | Boolean | Yes | `false` | `true`, `false` | `true` |
| `appearance.backgrounds.idle` | String or null | Yes | `null` | Referenced package image or null | `assets/idle.png` |
| `appearance.backgrounds.listening` | String or null | Yes | `null` | Referenced package image or null | `assets/listening.jpg` |
| `appearance.backgrounds.thinking` | String or null | Yes | `null` | Referenced package image or null | `assets/thinking.png` |
| `appearance.backgrounds.speaking` | String or null | Yes | `null` | Referenced package image or null | `assets/speaking.png` |
| `appearance.backgrounds.sleeping` | String or null | Yes | `null` | Referenced package image or null | `assets/sleeping.png` |
| `appearance.backgrounds.error` | String or null | Yes | `null` | Referenced package image or null | `assets/error.png` |

Disabled backgrounds retain their assignments in the skin. Any non-null package
reference still needs its asset in the archive. Use distinct filenames per
assigned state because of the current importer limitation. See
[structure and image limits](structure.md#package-and-image-limits).

### Eye Safe Layer

| Parameter | Type | Required | PHOS Default | Accepted values | Example |
| --- | --- | --- | --- | --- | --- |
| `appearance.eye_safe_layer.enabled` | Boolean | Yes | `true` | `true`, `false` | `true` |
| `appearance.eye_safe_layer.mode` | String | Yes | `balanced` | `subtle`, `balanced`, `expressive` | `subtle` |

The layer analyses usable background images and adds bounded local contrast
protection. With no background or a sufficiently dark, low-variation image, it
can draw no halo even when enabled. It does not modify the image file.

### State Effects

| Parameter | Type | Required | PHOS Default | Accepted values | Example |
| --- | --- | --- | --- | --- | --- |
| `appearance.state_effects.enabled` | Boolean | Yes | `false` | `true`, `false` | `true` |
| `appearance.state_effects.preset` | String | Yes | `balanced` | `subtle`, `balanced`, `expressive` | `subtle` |
| `appearance.state_effects.palette` | String | Yes | `phos` | `phos`, `cyan`, `blue`, `violet`, `aqua`, `amber`, `white`, `green` | `amber` |
| `appearance.state_effects.reduce_motion` | Boolean | Yes | `false` | `true`, `false` | `true` |

All four fields remain required when disabled. The engine selects ambient,
inward, circulating, outward, breathing, or restrained-error treatments by
robot state. Presets scale their visual strength. Reduced motion limits State
Effects speed, motion, and pulsing; it does not disable eye blinking, camera
tracking, or every motion-related behavior.

## Semantic LED palette

This optional version-3 block sits beside `appearance`, not inside it:

```yaml
led_palette:
  enabled: true
  colors:
    idle: "#F4AB3D"
    thinking: "#B38CFF"
    success: "#41CD7D"
```

| Parameter | Type | Required | Default / accepted values | Example |
| --- | --- | --- | --- | --- |
| `led_palette.enabled` | Boolean | When the mapping is supplied | `true` or `false`; no inserted default | `true` |
| `led_palette.colors` | Mapping | When the mapping is supplied | May be empty or partial; disabled palettes must use `{}` | `{idle: "#F4AB3D"}` |
| `led_palette.colors.idle` | Hex string | No | Omitted: runtime fallback | `"#F4AB3D"` |
| `led_palette.colors.listening` | Hex string | No | Omitted: runtime fallback | `"#22D3EE"` |
| `led_palette.colors.thinking` | Hex string | No | Omitted: runtime fallback | `"#B38CFF"` |
| `led_palette.colors.speaking` | Hex string | No | Omitted: runtime fallback | `"#60A5FA"` |
| `led_palette.colors.sleeping` | Hex string | No | Omitted: runtime fallback | `"#1E3A8A"` |
| `led_palette.colors.success` | Hex string | No | Omitted: runtime fallback | `"#41CD7D"` |
| `led_palette.colors.attention` | Hex string | No | Omitted: runtime fallback | `"#F4AB3D"` |
| `led_palette.colors.ambient` | Hex string | No | Omitted: runtime fallback | `"#F59E0B"` |

LED colours require exactly `#RRGGBB`; iris preset names are not accepted here.
Warning/error keys are rejected. Skins cannot change LED brightness, animation,
priority, duration, hardware pins, or controller capability. A disabled palette
is `{enabled: false, colors: {}}`; it disables the skin override, not the LEDs.

## Physical Personality

```yaml
physical_personality:
  preset: calm
```

| Parameter | Type | Required | Default / accepted values | Example |
| --- | --- | --- | --- | --- |
| `physical_personality.preset` | String | When the mapping is supplied | `analytical`, `playful`, `calm`; absent block falls back to `analytical` | `calm` |

| Preset | Idle timing policy |
| --- | --- |
| `analytical` | Blink interval 4.8–7.2 s; gaze rest interval 4.0–7.0 s. |
| `playful` | Blink interval 2.4–4.2 s; gaze rest interval 1.5–3.2 s; larger set of idle gaze offsets and more frequent double blinks. |
| `calm` | Blink interval 5.5–8.0 s; gaze rest interval 4.8–8.0 s; restrained idle offsets. |

These are policy ranges, not guaranteed measured frame timings. The preset also
selects double-blink pause/chance and gaze-hold ranges internally; skins cannot
supply custom timing numbers. Attention, tracking, IMU reactions, and system
states retain authority. Physical Personality belongs to the skin, not an
agent's reasoning persona.

## Local YAML catalog

See the [actual Aurora file](structure.md#the-shipped-catalog) for a complete example.

| Parameter | Type | Required | Default / accepted values | Example |
| --- | --- | --- | --- | --- |
| `schema_version` | Integer | Yes | Exactly `1` for catalogs | `1` |
| `skins` | List | Yes | List of entries; empty allowed | `[]` |
| `skins[].id` | String | Yes | Lowercase `[a-z][a-z0-9-]{0,63}`; unique across all sources | `copper-pulse` |
| `skins[].display_name` | String | Yes | Nonempty after trimming, at most 80 characters | `Copper pulse` |
| `skins[].inherits` | String or null | Yes | Built-in/catalog ID; null selects `phos-default` | `minimal` |
| `skins[].appearance` | Mapping | Yes | Partial recursive overrides; `{}` retains the parent appearance | `{eyes: {iris_color: "#F4AB3D"}}` |
| `skins[].led_palette` | Mapping or null | No | Omitted: inherit; null: clear; disabled: stop inheritance; enabled partial: merge colours | `{enabled: true, colors: {idle: "#F4AB3D"}}` |
| `skins[].physical_personality` | Mapping or null | No | Omitted: inherit; null: clear; mapping uses a supported preset | `{preset: calm}` |

Catalog backgrounds use `backgrounds/<filename>` or managed custom-skin paths,
not `assets/`. Unknown catalog fields, including package-style `description`,
are rejected. Catalog definitions do not use the portable package schema.

## Runtime selection and per-agent policy

These fields belong to the existing full `config/phos.yaml` model, not
`skin.yaml`. Merge changes into the active configuration; do not replace the
entire runtime document with a skin manifest.

| Parameter | Type | Default / accepted values | Example |
| --- | --- | --- | --- |
| `appearance.config_file` | Path or null | Shipped `skins.yaml`; older configurations default to null. Catalog must be a `.yaml`/`.yml` file beneath the active configuration directory. | `skins.yaml` |
| `appearance.mode` | String | `global`; accepts `global`, `follow_agent` | `follow_agent` |
| `appearance.global_skin_id` | String or null | `null`; nonempty ID when supplied | `phos-default` |
| `appearance.anonymous_mcp_use_default_agent_skin` | Boolean | `false` | `false` |
| `appearance.presentation_owner.agent_id` | String or null | `null`; retained legacy owner metadata; does not override automatic mode arbitration | `null` |
| `agents.entries[].skin.enabled` | Boolean | Both assignment fields required when `skin` exists; no assignment by default | `true` |
| `agents.entries[].skin.id` | String or null | Required with `skin`; nonempty known ID when enabled | `minimal` |
| `agents.entries[].appearance.participate` | Boolean | `true` when policy omitted | `true` |
| `agents.entries[].appearance.priority` | Integer | `50`; range 0–100 | `60` |
| `agents.entries[].appearance.activity_timeout_seconds` | Integer | `30`; range 5–3600 | `45` |

The appearance policy mapping, when supplied, requires all three fields.
Anonymous MCP fallback uses the default agent's eligible skin at fixed priority
−1 with a 30-second lease, below authenticated candidates. It does not create a
registered anonymous agent or grant that default agent's permissions.

Apply supported runtime settings through the existing lifecycle or Web Admin
workflow described in [Installing and Activating](installing.md).
