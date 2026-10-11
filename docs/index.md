---
hide:
  - navigation
  - toc
classes:
  - phos-home
---

<div class="phos-hero" markdown>

<img class="phos-logo" src="assets/images/phos.png" alt="PHOS logo">

<div class="phos-hero-copy" markdown>

# PHOS

## An expressive AI-powered robot, built on Raspberry Pi.

PHOS brings animated eyes, vision, motion and environmental awareness together
in a modular physical robot platform designed for Raspberry Pi 3.

<p class="phos-version">Current source: <strong>{{ phos_version }}</strong> · target-hardware acceptance remains before tagging.</p>

<div class="phos-actions" markdown>

[Explore PHOS](features.md){ .md-button .md-button--primary }
[Read the docs](documentation.md){ .md-button }
[View on GitHub](https://github.com/phosairobot/phos){ .md-button }

</div>
</div>

<div class="phos-eyes" role="img" aria-label="Two stylised glowing PHOS eyes">
  <div class="phos-eye"><span class="phos-eye-iris"><span class="phos-eye-pupil"></span></span><span class="phos-eye-glint"></span></div>
  <div class="phos-eye phos-eye--right"><span class="phos-eye-iris"><span class="phos-eye-pupil"></span></span><span class="phos-eye-glint"></span></div>
</div>

</div>

<div class="phos-intro" markdown>

PHOS is an AI-powered physical robot—not a chatbot in a browser. Its architecture
keeps hardware, observation, behavior and rendering separate, so expressive
visual responses remain lightweight enough for a Raspberry Pi while the platform
can grow through verified milestones.

</div>

## Built for a physical world

<div class="phos-stat-row" markdown>

<div><strong>01</strong><span>Expressive display</span></div>
<div><strong>02</strong><span>Semantic behavior</span></div>
<div><strong>03</strong><span>Modular hardware</span></div>

</div>

## What PHOS does now

<div class="phos-grid" markdown>

<article class="phos-card" markdown>
<span class="phos-card-icon">◉</span><span class="phos-status">IMPLEMENTED</span>
### Expressive visual presence
Smooth animated eyes, gaze, blink timing, semantic accents and optional local camera preview create PHOS’s visible personality.

[Visual system →](visual-system.md)

</article>

<article class="phos-card" markdown>
<span class="phos-card-icon">⌁</span><span class="phos-status">IMPLEMENTED</span>
### Observes, then reacts
Camera, motion and environmental inputs become stable semantic observations before `BehaviorEngine` chooses visual intent.

[How PHOS works →](how-phos-works.md)

</article>

<article class="phos-card" markdown>
<span class="phos-card-icon">⌘</span><span class="phos-status">IMPLEMENTED</span>
### Configured with care
Web Admin edits the same active configuration source used at startup (normally canonical `phos.yaml`), with validation, status and carefully bounded reload/restart actions.

[Web Admin →](web-admin-overview.md)

</article>

</div>

## Built with AI coding agents—under human direction

PHOS is developed through an agent-driven engineering workflow. Human direction
sets product goals and architecture; AI coding agents assist with implementation,
refactoring, testing, documentation and release hardening. They work inside the
repository’s `AGENTS.md` rules, documented architecture boundaries and test suite.
The checked-out Git repository remains the source of truth.

<div class="phos-flow" aria-label="Human-directed agent workflow">
<span>Human direction</span><b>→</b><span>Architecture &amp; requirements</span><b>→</b><span>AI coding agents</span><b>→</b><span>Implementation &amp; tests</span><b>→</b><span>Repository evidence</span>
</div>

This is a human-governed engineering process; PHOS does not autonomously create
itself.

## Start exploring

<div class="phos-links" markdown>

- [Implemented features](features.md) — what is in the current source, and what still needs physical acceptance.
- [Getting started](installation.md) — the documented Raspberry Pi installation path.
- [Hardware & sensors](hardware-overview.md) — the physical platform and optional peripherals.
- [Roadmap](roadmap.md) — completed work, current release gates and explicitly deferred areas.

</div>
