---
hide:
  - navigation
  - toc
classes:
  - phos-home
---

# Installation

<div class="phos-page-intro" markdown>

<span class="phos-kicker">GETTING STARTED</span>

PHOS runs on Raspberry Pi OS **with a graphical desktop**, Python 3.11+ and the
800×600 display. Raspberry Pi OS Lite alone is not sufficient because the eye
renderer needs an active graphical display.

</div>

## Recommended path

1. Clone PHOS and run `./scripts/install-phos.sh` as documented in the canonical [Raspberry Pi installation procedure](installation.md).
2. Test the camera and eye demo before enabling optional Vision features.
3. Edit the complete canonical `config/phos.yaml`; its defaults safely start eyes only.
4. Validate configuration, then start PHOS with the documented foreground command.
5. Use the documented user systemd service for production startup and browser-initiated PHOS restart.

Web Admin is disabled by default. Enable it in the canonical `web` section only
on a trusted LAN, then complete bootstrap password rotation. Optional expression,
environmental, IMU and LED paths each have dependencies and physical checks in
the full guide.

No installation commands are repeated here, so the [canonical installation
reference](installation.md) remains the single source of truth.
