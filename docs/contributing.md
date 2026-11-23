# Contributing and project status

PHOS welcomes issue reports, documentation improvements, hardware evidence, and
code contributions. The repository currently has **no CONTRIBUTING guide and no
repository license file**. This is an important adoption and legal gap: do not
assume the code is licensed for reuse or redistribution until maintainers publish
an explicit license.

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
interfaces, retain hardware-free tests, and do not treat physical acceptance as
complete without recorded evidence.

## Project maturity

The repository distinguishes implemented source paths from target-hardware
acceptance. The [roadmap](roadmap.md) records planned and unverified work; the
[release records](releases.md) record milestone scope. Optional hardware and
external integrations should be described with their documented acceptance
status, not as universal product guarantees.

## Maintainer decisions still needed

- Select and publish a license.
- Publish a contribution process and preferred discussion channel, if desired.
- Supply authentic reference-build photos, a demonstration video, and any
  approved enclosure files before they are presented as project assets.
