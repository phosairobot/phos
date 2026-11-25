# Installing and Activating Skins

Skin management uses the existing PHOS application and lifecycle services. The
local runtime is required for live appearance; package import alone does not
start it or change the display.

## Web Administration

Open **Appearance → Skins** after signing in.

| Operation | Result |
| --- | --- |
| Import Skin Package | Accepts `.phos-skin` or compatible `.zip`; validates and installs a new custom ID. Standalone YAML is not an upload format. |
| Preview | Temporarily applies eye/background/Eye Safe/State Effect settings live; does not save configuration or apply LED/personality settings. |
| Apply | Saves the skin's appearance and global ID, then requests runtime reload/reconciliation. |
| Export | Downloads a version-3 `.phos-skin` archive with referenced background files. |
| Delete Skin | Deletes a managed custom definition and owned assets, subject to reference checks; built-ins and catalog entries are not deleted through this action. |
| Save mode | Persists Global Appearance or Follow Active Agent and requests reconciliation. |

The current card UI offers Preview, Apply, Export, and eligible custom-skin
deletion. There is no card editor for YAML, renaming, LED palette editing, or
Physical Personality editing. Edit an exported package and import it again.
Repeated imports create distinct IDs rather than updating a previous skin.

**Preview has no countdown or automatic revert.** Apply a different appearance,
trigger normal reconciliation, or restart to replace it. After Apply, verify
runtime status; saving and the currently effective agent appearance are distinct.

## Manual local catalog

For a stable, source-controlled ID, use the supported catalog rather than
copying arbitrary skin folders into the runtime.

1. Place a catalog such as the repository's `config/skins.yaml` beneath the
   active configuration directory. Keep the three required entry fields plus
   `appearance` and any supported optional blocks.
2. Put referenced background files under that directory's `backgrounds/`.
3. Set `appearance.config_file` in the existing full runtime configuration.
4. Select the catalog skin in Web Admin and use **Apply**. For manual selection,
   merge the following appearance section into the full configuration and
   restart through the normal deployment workflow.

```yaml
appearance:
  config_file: skins.yaml
  mode: global
  global_skin_id: aurora
  anonymous_mcp_use_default_agent_skin: false
  presentation_owner:
    agent_id: null
```

This example selects the real shipped Aurora definition. Preserve unrelated
runtime fields and agent records. Canonical model validation checks the runtime
configuration; `SkinRepository.list()` checks the catalog itself. Changing a
catalog file is not a filesystem-watched hot reload; request an explicit apply,
reconciliation, or restart after edits.

The current Python wheel packages `config/phos.yaml`, but does not bundle
`config/skins.yaml`. Source checkouts include it. When using an installed
package without that catalog, copy the repository catalog alongside your active
configuration, or set `appearance.config_file: null` to use only built-ins and
managed custom skins. Do not leave a configured catalog path pointing at a
missing file.

Portable-package manual installation is supported through the internal
`SkinRepository.import_package(bytes)` boundary, as demonstrated in the
[tutorial's temporary validation](../appearance/creating-skins.md#6-build-and-validate-the-archive).
There is no dedicated skin-management CLI. Do not hand-create custom IDs or
write `skins.json` as an installation shortcut.

## Runtime switching and per-agent appearance

**Global Appearance** uses the persisted global skin, falling back to saved
display settings when there is no usable global definition.

For **Follow Active Agent**:

1. In **Settings → Agents**, assign a known skin to the desired registered agent.
2. Enable its appearance participation and choose priority/timeout.
3. In **Appearance → Skins**, select **Follow Active Agent** and save the mode.
4. Use the agent's normal MCP credential and perform accepted semantic work.
   The activity tracker renews that agent's appearance lease.
5. Inspect current activation and **Active LED Palette**. On expiry, PHOS selects
   another eligible active agent or the global fallback.

Defaults are participation enabled, priority 50, and a 30-second activity
window. Higher priority wins; existing winners are retained on a tie where
possible. The complete ranges and optional anonymous default-agent fallback are
in the [configuration reference](configuration.md#runtime-selection-and-per-agent-policy).
A configured legacy presentation owner does not override this mode authority.

These appearance leases are distinct from `phos_set_state`'s bounded thinking
visual lease. Assigning a skin does not change permissions or give an agent
exclusive access to the display. The global configuration is not overwritten by
a temporary selected-agent skin.

## REST, MCP, and internal application boundaries

| Surface | Implemented appearance access |
| --- | --- |
| Authenticated Web Admin `/appearance/skins` | Package import, temporary preview, apply, export, deletion, and mode controls through application services. |
| Web Admin `GET /appearance/skins/<skin_id>/export` | Download a portable package; requires normal Admin access. |
| Web Admin `GET /appearance/preview.svg` and `/appearance/preview` | Static SVG and its preview page; not a live skin-activation API. Current shape/catalog limitations are documented under troubleshooting. |
| REST `/api/v1/config` | Existing authenticated full-configuration read/update boundary; not a skin package/catalog CRUD API. |
| REST `/api/v1/backgrounds/assets`, `/api/v1/backgrounds` | Existing managed background listing/configuration; not skin package import/export. |
| MCP | No skin-list/import/apply tool or skin resource. Accepted agent activity can indirectly select an administrator-assigned skin in `follow_agent` mode. |
| Python application services | Internal skin list/create/import/export/preview/apply/delete operations; these are not automatically public REST/MCP methods. |

MCP expression, speech, signal, and thinking-state tools continue to use their
existing permissions. They do not accept a skin manifest or choose arbitrary
skin IDs. Follow the existing [Remote API](../remote-api.md) and
[MCP reference](../mcp.md) for those contracts.

## Restore PHOS Default

1. Select **Global Appearance** and save the mode if automatic selection is active.
2. Find **PHOS Default** in Available Skins and choose **Apply**.
3. Verify cyan irises, classic oval eyes, no assigned backgrounds, and disabled
   State Effects. Normal robot state and expressions still affect appearance.

This resets the appearance baseline; it does not reset sensors, credentials,
agent records, or runtime behavior configuration. `global_skin_id: null` alone
means “use saved global display values,” not “reset to PHOS Default.”

Before deleting a custom skin, remove or replace agent assignments, including
retained disabled assignments, and apply another appearance if its owned
backgrounds are still in use. Export a backup before deletion.
