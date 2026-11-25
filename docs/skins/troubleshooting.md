# Skin Troubleshooting

Start with the exact error, the active configuration directory, and whether the
operation was Import, live Preview, Apply, or static SVG Preview. These paths
have different validation and persistence behavior. Keep an exported package
before editing managed files.

## Discovery and catalog errors

| Symptom | Cause / action |
| --- | --- |
| A folder is not listed as a skin | PHOS discovers built-ins, the configured YAML catalog, and managed `skins.json`; it does not scan arbitrary skin directories. Import a package or add a catalog entry. |
| Configured catalog is missing | Resolve `appearance.config_file` relative to the active configuration file; copy the catalog there, or use null to disable that source. The current wheel does not include the repository catalog. |
| Catalog has unsupported fields | Use catalog schema 1 and required `id`, `display_name`, `inherits`, `appearance`; package fields are a different schema. |
| Duplicate/conflicting ID | Choose a unique lowercase catalog ID. Imported IDs are generated; do not replace built-in IDs or edit custom IDs by hand. |
| Unknown parent, cycle, or depth error | Inherit from an available built-in/catalog entry; remove cycles and keep inheritance within the eight-depth bound. |
| Catalog background unavailable | Place the referenced file beneath the active configuration directory's `backgrounds/`; catalog loading rejects missing references. |
| Entire custom list unavailable | Repair unreadable/malformed `skins.json` from a known backup; it is not silently skipped. Prefer package import/export over manual edits. |

## Import validation

| Symptom | Cause / action |
| --- | --- |
| Unsupported manifest | Use package schema 3, or a legacy schema-2 package without version-3 optional blocks. New packages should use 3; catalog schema 1 is not a portable package manifest. |
| `Skin appearance must contain ...` | Supply exactly `eyes`, `backgrounds`, `eye_safe_layer`, and `state_effects`. Disabled features still require their complete fields. |
| Unsupported parameter | Remove custom geometry, animation code, expression definitions, URLs, or unknown fields; use the [reference](configuration.md). |
| Unsafe archive entries | Put `skin.yaml` at root; remove parent folders, nested assets, backslashes, traversal, symlinks, `__MACOSX`, `.DS_Store`, and `._*` files. |
| Duplicate/unreferenced entries | Include each file once and only images referenced by the manifest. Omit `assets/` entirely for an asset-free skin. |
| Missing or invalid image | Check exact filename/case, extension and PNG/JPEG signature; image bytes must match the extension. |
| Import fails when states share an image | The current importer writes each state's file exclusively. Give each assigned state a distinct filename, even if the bytes are identical. |
| Size/dimension error | Observe the [package limits](structure.md#package-and-image-limits). Validate decoded dimensions on a PHOS installation with OpenCV/NumPy; this check is skipped when those modules are absent. |
| Name imports but later metadata loading fails | Use a nonempty name of at most 80 characters and description of at most 240 characters. Initial package-manifest checks are less strict than persistent custom-record checks. |

## Colours, dimensions, and rendering

- **Hex colour becomes null in YAML:** quote `"#RRGGBB"`; unquoted `#` begins a
  comment. Iris presets are accepted and normalised; LED palettes require hex.
- **Unexpected eye-body colours:** skin iris colour is separate from semantic
  accents and expression geometry. Test with a neutral state/expression first.
- **Background is cropped:** the renderer scales proportionally to fill and
  centre-crops. Prepare 800×600 images for the reference display.
- **No Eye Safe halo:** there must be a usable background with enough brightness
  or variation to need protection. A dark or absent background can produce none.
- **State Effects look static or restrained:** check enablement, preset, palette,
  reduced motion, and actual robot state. Static SVG Preview is one frame.
- **Changed file still looks old:** display background preparation is keyed by
  path and dimensions. Use a new asset filename or restart to invalidate an
  existing prepared image after replacing its bytes in place.
- **LEDs do not change:** confirm the resolved skin, its enabled palette, the
  semantic intent, and an active LED-capable controller. Live Preview does not
  apply LED palettes; absent/disabled entries use runtime fallback colours.
- **Idle timing does not change:** Physical Personality is skin-owned and applied
  during effective-skin reconciliation. Live Preview applies visual blocks only;
  tracking, attention, motion reactions, and system state override idle intent.

## Preview and saved-appearance differences

The static `appearance_preview.render_preview` renderer currently constructs its
eye renderer without the configured shape, so non-classic shapes can look
classic in SVG Preview. The preview service also constructs a repository without
the optional YAML catalog; selecting a catalog-only ID in that static preview
path can fail even though normal skin listing/live activation resolves it.
Use the live face display for those checks.

The current `appearance_snapshot` helper records `classic_oval` rather than
reading the selected shape. Internal “create from current appearance” and
appearance-matching paths can therefore omit a non-classic shape. Author/export
explicit skin definitions instead of relying on that capture path for shape.

Internal repository `rename` and `update_led_palette` operations currently
reconstruct a custom definition without retaining `physical_personality`.
They are not editors exposed by the current skin-card UI. Keep the authored
package as the source for a skin's personality when using internal helpers.

## Activation and deletion

| Symptom | Cause / action |
| --- | --- |
| Runtime appearance service not ready | Wait until the running display/services are ready, then retry. Importing a package does not start the runtime. |
| Saved skin differs from visible skin | Check Global Appearance versus Follow Active Agent and the selected agent's current activity lease. A higher-priority eligible active agent may be visible. |
| Preview remains after leaving the page | Live Preview has no expiry timer. Apply the intended skin, reconcile normal appearance, or restart. |
| Missing custom backgrounds | Explicit Preview/Apply reports and drops missing state assignments; automatic selected-agent appearance falls back to global instead. Restore the files or export/reimport a complete package. |
| Apply failed | The application attempts to restore the previous saved configuration. Some live work may already have occurred; inspect active status and retry or use the normal restart workflow. |
| Cannot delete skin | Built-ins/catalog entries are not deletable through custom-skin deletion. Remove agent references and active owned-background references for a custom skin first. |
| Reimport does not update an assigned skin | Imports always receive a new ID. Reassign agents or select the new global skin explicitly. |
| Warning/error LED colours ignore the theme | Those semantic colours are protected and are not configurable skin keys. |

These limits describe the current implementation. Skin packages do not promise
compatibility with future schemas, additional image formats, custom animation
engines, or arbitrary third-party renderer plugins.
