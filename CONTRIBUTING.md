# Contributing to PHOS

PHOS welcomes issue reports, documentation improvements, hardware evidence, and
code contributions. Please read [AGENTS.md](AGENTS.md), the
[architecture](docs/architecture.md), and the [accepted decisions](docs/decisions.md)
before proposing a change.

## Issues and pull requests

- Use the [GitHub issue tracker](https://github.com/phosairobot/phos/issues) for
  reproducible defects and bounded feature proposals.
- Include the PHOS version, Raspberry Pi/OS details, non-secret configuration,
  reproduction steps, relevant sanitized logs, and the observed result.
- Do not publish credentials, administrator passwords, bearer tokens, cloud
  credentials, raw camera frames, or audio recordings.
- Keep pull requests focused. Explain the user-visible effect, tests run, and
  any hardware validation still required.

## Code and testing expectations

Keep hardware-specific details behind existing interfaces, preserve the
repository architecture, and keep ordinary tests runnable without physical
hardware. Add or update deterministic tests for behavior changes, run the
relevant test suite, and check the final diff for unrelated changes. Physical
Raspberry Pi acceptance is separate from automated test success.

## Contribution licensing

Submit only material that you are authorized to license. Unless explicitly
agreed otherwise in writing, contributions submitted for inclusion in PHOS are
provided under the [Apache License, Version 2.0](LICENSE). This policy does not
require copyright assignment.
