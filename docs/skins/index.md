# Skins

A PHOS skin is a reusable appearance definition for the physical interface your
agents share. Choose iris colour and eye shape, state backgrounds, readability
protection, visual effects, and an optional semantic LED palette. A skin can
also select bounded idle blink and gaze timing through **Physical Personality**.

Skins make the same PHOS runtime recognisable in different settings or for
different agents. They do not change an agent's model, reasoning, permissions,
voice, or sensor interpretation.

## What a skin controls

| Part | Customisation | Boundary |
| --- | --- | --- |
| Eyes | Canonical iris colour and one of five eye shapes | Expressions, gaze intent, and blink events still come from the existing visual/behavior pipeline. |
| Backgrounds | Optional PNG/JPEG image for each of six robot states | An image is selected by state; a skin does not create or change that state. |
| Eye Safe Layer | Enablement and halo strength | Local contrast protection leaves the original background file unchanged. |
| State Effects | Enablement, intensity preset, palette, and reduced motion | The engine supplies the state-specific animation; packages do not contain animation code. |
| Semantic LEDs | Optional colours for eight themeable intents | Controller capability, arbitration, brightness, and animation timing remain runtime-owned; warning/error colours are protected. |
| Physical Personality | `analytical`, `playful`, or `calm` | Changes low-priority idle blink/gaze timing, not conversational personality or safety/state priorities. |

## Skins, expressions, states, and overlays

**Skin** describes the presentation. **`RobotState`** describes what PHOS is
currently doing. **`FaceState`** carries resolved expression, gaze, blink, and
visual intent to `EyeRenderer`.

A skin does not define new expressions or replace the animated eyes with an
image. The existing expression profiles and robot-state priorities remain in
force. Camera tracking, attention, and motion reactions can override idle gaze.
Environmental markers, transient semantic notices, and protected Admin/recovery
overlays remain separate from the skin package.

**Global Appearance** uses the saved global appearance. **Follow Active Agent**
can temporarily select an eligible active agent's assigned skin. This is visual
presentation arbitration, not a change of agent permissions or an agent
orchestration framework. See [installing and activating](installing.md).

## Choose a path

- [Built-in Skins](built-in.md): compare the three built-ins and shipped Aurora catalog entry.
- [Skin Architecture](architecture.md): discovery, rendering, activation, and fallbacks.
- [Skin Structure](structure.md): packages, YAML catalogs, and managed storage.
- [Configuration Reference](configuration.md): fields, defaults, accepted values, and examples.
- [Creating a Custom Skin](../appearance/creating-skins.md): build and validate a complete package.
- [Installing and Activating Skins](installing.md): local files, Web Admin, runtime switching, and integration boundaries.
- [Troubleshooting](troubleshooting.md): validation errors and current implementation limits.

Skins personalise PHOS's physical communication interface. Start with the
[visual system](../visual-system.md) for expression semantics or
[Web Administration](../web-administration.md#appearance) for the surrounding UI.
