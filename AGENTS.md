# PHOS — Repository Agent Instructions

## Identity and target
PHOS is a modular expressive robot targeting Raspberry Pi 3.

Current documented hardware baseline:
- Raspberry Pi 3
- 5-inch 800x600 display
- microphone
- Raspberry Pi Camera and face tracking are documented as operational; exact hardware details remain in `docs/hardware.md`

The existing Python package namespace is `robot`. Do not rename it to `phos` without an explicit approved migration.

## Source of truth
The checked-out repository is authoritative. Conversation history, old prompts and external skill copies are not authoritative when they conflict with repository files.

Before changing code:
1. inspect `git status` and the current diff;
2. read this file and every nested `AGENTS.md` that applies to the target path;
3. read only the relevant files in `docs/`, especially `docs/decisions.md`;
4. inspect existing interfaces, implementation and tests;
5. identify the smallest coherent change for the current milestone.

More specific nested `AGENTS.md` guidance overrides general guidance for its directory.

## Working agreement
- Architecture/design defines WHAT and WHY.
- Coding agents such as Codex implement HOW and CODE.
- Git and the checked-out repository record the implementation state.
- Extend existing abstractions instead of recreating them under new names.
- Preserve accepted architecture unless an explicit change is approved and documented.
- Do not rename/remove public interfaces without explicit instruction.
- Do not perform unrelated refactors or dependency churn.
- Keep hardware/vendor details behind interfaces, adapters or providers.
- Keep ordinary tests runnable without physical Raspberry Pi hardware.
- Consider Raspberry Pi 3 CPU, RAM and storage limits before adding dependencies or background work.

If documentation, tests and implementation materially disagree, report the inconsistency instead of silently redesigning PHOS.

## Runtime configuration

New non-secret PHOS runtime configuration must be added to the canonical
`RuntimeConfig` model in `src/robot/config.py` and `config/phos.yaml`.
Do not introduce new standalone CLI configuration unless explicitly required.
Use the reusable loading/validation/persistence boundary for future configuration
interfaces; keep secrets external. The web administration adapter must reuse
this model and validation; future configurable subsystems extend the same
configuration/editor architecture rather than adding separate mechanisms.
Administrator credentials and all other secrets stay outside runtime JSON.
See `docs/development.md` for the schema and migration policy. Do not duplicate schema/defaults in AGENTS files.

## Architecture boundaries
- `core/`: lifecycle, state, events, orchestration and behavior decisions.
- `hardware/`: physical-device adapters and GPIO-facing implementation.
- `voice/`: microphone, STT, TTS and audio concerns.
- `ai/`: agent, provider-neutral model interfaces, tools, memory and personality.
- `ui/`: display, visual state and eye rendering.
- `vision/`: camera and computer vision.

Additional boundaries:
- AI/business logic must not directly manipulate GPIO.
- All LLM access goes through `LLMProvider`; vendor SDKs stay under AI provider adapters.
- TTS synthesis goes through `TTSProvider`; synthesis and playback are separate responsibilities.
- Vision produces observations/events; it must not call the LLM or renderer directly.
- `BehaviorEngine` decides PHOS behavior/visual intent from robot state and observations.
- `FaceState` is the UI-neutral visual state consumed by the renderer.
- `EyeRenderer` renders `FaceState`; it must not interpret Vision objects.
- Home Assistant is an integration/tool layer, not PHOS's reasoning core.
- Future Web/API/MCP/Voice control surfaces must reuse PHOS application services;
  they must not access hardware, drivers or subsystem internals directly. Extend
  the shared services for approved capabilities instead of duplicating control logic.
- Reload/restart must use the shared lifecycle service and validate canonical
  configuration; adapters must never execute arbitrary OS commands. Process
  replacement belongs to the deployment supervisor, not a self-spawning web handler.

## Milestone discipline
PHOS is developed as vertical, runnable milestones. Finish, run and verify one capability before deliberately expanding the next major subsystem.

The implemented 1.0.0 scope is frozen; post-1.0 capabilities are explicitly deferred
in `docs/roadmap.md`. Hardware regression remains separate from implementation.
The progression for separately approved future milestones remains:
1. verify the animated eyes/demo on the real 800x600 Raspberry Pi display;
2. add/verify face-position tracking so the already-working eyes can look toward a detected face;
3. complete and verify facial-expression observation plus BehaviorEngine reactions with a selected ONNX model;
4. integrate microphone -> STT -> provider-neutral LLM -> TTS -> playback;
5. add Home Assistant and further hardware/integrations incrementally.

Some later-stage code may already exist from earlier implementation work. Do not delete it merely because it is ahead of the current milestone, but do not expand it automatically either. The current task and repository state determine what should change.

Do not start the next milestone automatically after completing the requested one.

## Documentation source of truth
- Architecture: `docs/architecture.md`
- Accepted decisions: `docs/decisions.md`
- Hardware: `docs/hardware.md`
- Current implementation handoff: `docs/current-state.md`
- Progress/milestones: `docs/roadmap.md`
- AI: `docs/ai.md`
- Vision: `docs/vision.md`
- TTS: `docs/tts.md`
- Development/testing: `docs/development.md`

Avoid duplicating detailed facts across documents or AGENTS files when a single source of truth is sufficient.

## Verification and completion
After implementation:
1. run relevant tests;
2. run the full suite when practical;
3. execute or describe the concrete runnable verification path for the requested milestone;
4. inspect `git diff` and `git status`;
5. verify no unrelated files changed;
6. update documentation only when externally visible behavior, implementation status or an approved architectural decision changed;
7. report changed files, verification evidence, remaining TODOs and architectural conflicts.

Do not commit, push, deploy or modify external systems unless explicitly requested.
