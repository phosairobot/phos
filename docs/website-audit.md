# Website and documentation audit

**Audit basis:** checked-out repository, MkDocs configuration, documentation,
tracked assets, GitHub Actions workflows, configuration, MCP adapter, and tests
reviewed during the 1.9.0 documentation redesign. This page records the documentation redesign findings.

## Current inventory and disposition

The following is the page-by-page disposition for every Markdown source page
published by the MkDocs configuration. A move below means navigation placement;
the source URL remains unchanged, so no redirect is needed.

| Page | Disposition | Reason |
| --- | --- | --- |
| `index.md` | UPDATE | Reframe the entry page around physical interface, build, and agent paths. |
| `discover.md`, `features.md`, `how-phos-works.md`, `use-cases.md` | KEEP / MOVE | Retain as Explore content; revise messaging where version or personality framing is inaccurate. |
| `vision-expressions.md`, `environmental-awareness.md`, `motion-awareness.md` | MOVE | Keep specialist capability material under Explore. |
| `installation-overview.md`, `installation.md`, `hardware-overview.md`, `hardware.md` | KEEP / UPDATE | Retain the canonical build/install/wiring detail; add a concise maker entry path. |
| `build-phos.md` | CREATE | New evidence-bounded maker pathway; no invented BOM, price, or enclosure data. |
| `mcp-overview.md`, `mcp.md`, `ai.md`, `remote-api.md` | KEEP / UPDATE | Separate the readable agent path from the exact MCP/REST reference. |
| `developer-quickstart.md` | CREATE | New developer entry path using the verified Streamable HTTP boundary and bridge pattern. |
| `architecture.md`, `decisions.md`, `development.md`, `documentation.md` | KEEP | Engineering and contributor references; configuration retains one canonical reference. |
| `vision.md`, `vision-model-evaluation.md`, `visual-system.md` | MOVE | Retain under Reference; do not duplicate their implementation detail. |
| `voice.md`, `voice-commands.md`, `tts.md` | MOVE | Retain under Reference; voice claims remain conditional on configuration/hardware. |
| `web-administration.md`, `web-admin-overview.md`, `hardware-controller.md`, `api.md`, `configuration-migration.md` | MOVE | Retain operational and compatibility references. |
| `appearance/creating-skins.md` | MOVE | Retain as optional customization, not primary product positioning. |
| `current-state.md`, `roadmap.md`, `releases.md`, `release-1.0.0.md`, `release-1.1.0.md`, `release-1.2.0.md`, `release-1.3.0.md`, `release-1.4.0.md`, `release-1.7.0.md`, `release-1.8.0.md`, `release-1.9.0.md` | KEEP / UPDATE | Preserve technical history and publish the observed version discrepancy. |
| `contributing.md` | CREATE | Surface issue reporting, maturity, and contribution-process decisions. |
| `website-audit.md` | CREATE | Preserve the evidence, gap analysis, and URL/disposition decisions. |
| Existing source URLs | KEEP | No source file moves were made; redirects are not required. |

## Evidence-based gap analysis

| Requirement | Existing page or implementation | Current status | Missing information | Recommended change | Priority |
| --- | --- | --- | --- | --- | --- |
| Clear maker/developer entry paths | Homepage, installation and MCP docs | IMPLEMENTED | None in documentation structure | Keep Build and Developers as primary routes. | High |
| Current release version | Git tag, `robot.__version__`, release test | PARTIALLY IMPLEMENTED | Tag is `1.9.0`; package/test are `1.8.0`. | Resolve source/tag metadata before publication. | High |
| Canonical YAML configuration | `RuntimeConfig`, `config/phos.yaml`, migration guide | IMPLEMENTED | None identified | Retain YAML-only wording and historical JSON context only. | High |
| Required/optional hardware | Hardware/install docs and providers | PARTIALLY IMPLEMENTED | Pi 4/5, BOM alternatives, prices, enclosure specs are unverified. | State only the Pi 3/display baseline; obtain physical evidence before expanding. | High |
| MCP tool discovery | MCP server and MCP reference | IMPLEMENTED | Client-specific setup is external to this repository. | Keep the 17-tool/5-Resource reference canonical. | High |
| Authentic product media | `docs/assets/images/phos.png` | NOT IMPLEMENTED | No verified reference-build photo/video is tracked. | Obtain approved media with provenance and alt text. | High |
| License | Repository root | IMPLEMENTED | Apache License 2.0, NOTICE, and a dependency audit are present. | Confirm the copyright holder and complete release-specific third-party review before publication. | High |
| Contribution workflow / discussion venue | GitHub issues and root `CONTRIBUTING.md` | PARTIALLY IMPLEMENTED | No documented Discussion venue. | Add a preferred discussion channel only after maintainer decision. | Medium |
| GitHub Pages deployment | `.github/workflows/deploy-pages.yml` | IMPLEMENTED | Deployment uses a write token and third-party action; no issue for this content change. | Retain GitHub Pages-compatible MkDocs build. | Medium |
| Social metadata / share image | MkDocs URL/description and logo | PARTIALLY IMPLEMENTED | No approved social-preview photo/metadata asset. | Add explicit social image once authentic media exists. | Medium |
| Simulator without hardware | Tests use fakes/mocks | UNKNOWN / NOT VERIFIED | No end-user simulator is documented or verified. | Do not promise one. | Medium |

## Navigation changes

The previous **Discover PHOS** and broad **Technical Documentation** menus mixed
audience goals with deep reference pages. The revised navigation uses **Explore**,
**Build PHOS**, **Developers**, **Reference**, and **Community**. Technical pages
remain at their original URLs and are not removed.

## Consistency findings

- The checked-out commit is tagged 1.9.0, while the Python package literal and
  release test report 1.8.0; the modified OpenAPI contract also reports 1.9.0.
  This is a release-management discrepancy outside this documentation-only
  change and must be resolved before publication.
- YAML is the sole supported runtime configuration format. JSON remains used for
  API/MCP/controller payloads and may appear in historical material.
- The MCP adapter exposes 17 named tools and five Resources in the checked-out
  implementation. External clients vary in Streamable HTTP and subscription
  support, so the site documents a compatible bridge pattern rather than
  promising support for every client.
- The project license is Apache-2.0. The root `NOTICE` retains a
  `[COPYRIGHT HOLDER]` placeholder pending maintainer confirmation; this and
  the release-specific third-party audit remain publication prerequisites.

## Validation approach

The documentation build is run with `mkdocs build --strict`. Link resolution and
navigation are exercised by that build; physical hardware, external MCP clients,
and social-preview rendering require the corresponding hardware/accounts and
are reported separately rather than claimed as validated.
