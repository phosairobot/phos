# Contributing and project status

PHOS welcomes issue reports, documentation improvements, hardware evidence, and
code contributions. The repository contribution process is in the root
[CONTRIBUTING.md](https://github.com/phosairobot/phos/blob/main/CONTRIBUTING.md),
and PHOS is licensed under the
[Apache License, Version 2.0](https://github.com/phosairobot/phos/blob/main/LICENSE).

## Report or discuss work

- Use the [GitHub issue tracker](https://github.com/phosairobot/phos/issues) to
  report reproducible defects or propose a bounded improvement.
- Include PHOS version, Raspberry Pi/OS details, configuration with secrets
  removed, exact steps, logs with credentials removed, and the observed result.
- Do not post bearer credentials, Web Admin passwords, cloud credentials, raw
  camera frames, or audio in an issue.

## Before opening a change

Read the [architecture](architecture.md), [accepted decisions](decisions.md),
and repository `AGENTS.md`. Keep hardware-specific details behind existing
interfaces, retain hardware-free tests, and record hardware test results with
wiring and configuration details.

## Project maturity

The [roadmap](roadmap.md) records planned and unverified work; the
[release records](releases.md) record milestone scope. Describe optional hardware
and external integrations with their configuration
requirements and test results.

## Build on PHOS

- **External agent experiences:** demonstrate how an agent reads physical context,
  chooses an action, and uses expressions, thinking visuals, or configured speech.
- **MCP client integrations:** show discovery, permission failures, resource reads,
  `subscriptions/listen`, reconnection, and explicit decisions before output calls.
- **Hardware controllers:** implement the existing `phos-hw` v1 boundary on new
  hardware, with capability discovery and the accepted observation mappings.
  Document a board as supported only with implementation and hardware evidence.
- **Sensors and providers:** extend existing adapters and shared semantic services;
  explain units, freshness, failure behavior, and controller provenance.
- **Physical communication and accessibility:** improve expression clarity,
  reduced-motion experiences, documentation, or interaction examples using the
  existing visual and application-service boundaries.
- **Community demonstrations:** share reproducible configurations, hardware
  reports, or videos that show what an integration actually does.

Start with [use cases](use-cases.md), [MCP integration](mcp-overview.md),
the [controller protocol](hardware-controller.md), and
[skin authoring](appearance/creating-skins.md). Propose changes to public contracts
or architecture through the contribution process before implementing them.
