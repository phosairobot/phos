# Voice Commands

PHOS can resolve a small, local set of deterministic voice commands. These
commands are matched by the configured deterministic resolver; they do not need
an LLM to determine their intent. A phrase outside this exact catalog remains
unresolved and does not trigger an action.

To execute a command from a microphone transcript, enable `voice.enabled`,
`interaction.enabled`, and `interaction.execution_enabled`. The last option is
off by default: with it disabled, PHOS may record a resolved intent but does not
perform the command.

## How voice commands work

Start a finite listening session, speak one supported phrase, then let the
session finish. PHOS transcribes the utterance, resolves it locally, validates
the canonical action, and invokes the application-service boundary. Commands
cannot directly access hardware or the renderer.

Matching ignores surrounding whitespace, letter case, and terminal `.`, `!`, or
`?`; the remaining words must match a documented phrase exactly. There is no
wake word or continuous listening mode.

`interaction.default_language` is used when STT does not provide a language.
`interaction.supported_languages` limits matching to its configured languages.
The supplied configuration supports `en` and `it`; locale tags such as `en-US`
and `it-IT` use their English or Italian command catalog respectively. A
language outside that configured list remains unresolved.

## Italian commands

### Expressions

| Action | Example accepted phrases |
| --- | --- |
| Neutral expression | `sii neutro`, `torna normale`, `espressione neutra` |
| Happy expression | `diventa felice`, `sii allegro`, `mostra una faccia felice` |
| Curious expression | `sii curioso`, `fai una faccia curiosa`, `mostrati curiosa` |
| Surprised expression | `sii sorpreso`, `fammi sorpresa`, `faccia sorpresa` |
| Sad expression | `sii triste`, `espressione triste` |
| Angry expression | `sii arrabbiato`, `faccia arrabbiata` |
| Focused expression | `sii concentrato`, `espressione concentrata` |
| Tired expression | `sii stanco`, `mostrati stanca` |
| Confused expression | `imposta espressione confusa`, `sii confuso` |
| Scared expression | `imposta espressione spaventata`, `sii spaventato` |
| Skeptical expression | `imposta espressione scettica`, `sii scettico` |

The expression aliases are: `felice`/`allegro`, `curioso`/`curiosa`,
`sorpreso`/`sorpresa`, `triste`, `arrabbiato`/`arrabbiata`,
`concentrato`/`concentrata`, `stanco`/`stanca`, `confuso`/`confusa`,
`spaventato`/`spaventata`, `scettico`/`scettica`, and
`neutro`/`neutra`/`normale`. Each is accepted by the same expression forms: the
alias on its own, `diventa …`, `sii …` or `si …`, `mostrati …`, `faccia …`,
`fai una faccia …`, `mostra una faccia …`, `fammi …`, `torna …`,
`espressione …`, or `imposta espressione …`.

### Robot state

| Action | Example accepted phrases |
| --- | --- |
| Sleep | `vai a dormire`, `mettiti a dormire`, `dormi`, `entra in modalità sonno` |
| Return to idle | `torna attivo`, `svegliati`, `svegliati phos` |

Only `sleeping` and `idle` are voice-settable. Listening, thinking, speaking,
and error are internal/transient lifecycle states and are not voice commands.

### Sensors

| Action | Example accepted phrases |
| --- | --- |
| Temperature | `qual è la temperatura`, `dimmi la temperatura`, `dammi la temperatura` |
| Pressure | `leggi la pressione`, `mostrami la pressione atmosferica`, `controlla la pressione` |
| Air quality | `com'è la qualità dell'aria`, `qual è la qualità dell'aria`, `controlla la qualità dell'aria` |

For each sensor alias, the shared forms are `qual è la …`, `dimmi la …`,
`dammi la …`, `leggi la …`, `mostrami la …`, and `controlla la …`. Pressure
also accepts `pressione atmosferica`; air quality additionally accepts the
special form `com'è la qualità dell'aria`.

## English commands

### Expressions

| Action | Example accepted phrases |
| --- | --- |
| Neutral expression | `be neutral`, `make a normal face`, `reset expression` |
| Happy expression | `be happy`, `look happy`, `show a happy face` |
| Curious expression | `curious`, `make a curious face`, `face curious` |
| Surprised expression | `be surprised`, `show a surprised face` |
| Sad expression | `be sad`, `set expression sad` |
| Angry expression | `look angry`, `show a angry face` |
| Focused expression | `be focused`, `set expression focused` |
| Tired expression | `be tired`, `show a tired face` |
| Confused expression | `set expression confused`, `be confused` |
| Scared expression | `set expression scared`, `look scared` |
| Skeptical expression | `set expression skeptical`, `be skeptical` |

The aliases are `happy`, `curious`, `surprised`, `sad`, `angry`, `focused`,
`tired`, `confused`, `scared`, and `skeptical`; neutral also accepts `neutral`
and `normal`. Each alias can be spoken on its own or with `be …`, `look …`,
`face …`, `make a … face`, `show a … face`, or `set expression …`. `normal
face` and `reset expression` are extra neutral-only forms.

### Robot state

| Action | Example accepted phrases |
| --- | --- |
| Sleep | `sleep`, `go to sleep`, `go to sleep mode`, `enter sleep mode` |
| Return to idle | `go idle`, `enter idle mode`, `wake`, `wake up` |

Only `sleeping` and `idle` are voice-settable. Listening, thinking, speaking,
and error are not voice-settable.

### Sensors

| Action | Example accepted phrases |
| --- | --- |
| Temperature | `what is the temperature`, `tell me the temperature`, `read the temperature` |
| Pressure | `what's the pressure`, `read the atmospheric pressure`, `show me the pressure` |
| Air quality | `tell me the air quality`, `show me the air quality`, `how is the air quality` |

For every sensor alias, the shared forms are `what is the …`, `what's the …`,
`tell me the …`, `read the …`, and `show me the …`. Pressure also accepts
`atmospheric pressure`; air quality additionally accepts `how is the air
quality`.

## Results and limitations

Expressions and state commands have no spoken response. Sensor queries produce
a localized deterministic response when the requested snapshot is available.
PHOS reports temperature in degrees Celsius, pressure in hectopascals, and air
quality as eCO2 ppm plus TVOC ppb. To speak those sensor results, also enable
`interaction.response_tts_enabled` and configure TTS; otherwise the response
is available as interaction data but remains silent.

The temperature/pressure sensor must be available for those queries. Air-quality
queries require an available CCS811 reading. If the relevant sensor is
unavailable, PHOS returns a bounded unavailable result instead of inventing a
value.

## Troubleshooting

- Start listening before speaking, then wait for the current finite session to
  finish before starting another one.
- Check that voice capture, interaction resolution, and interaction execution
  are all enabled; resolution alone does not authorize execution.
- Use one of the exact documented forms. Similar wording, unsupported states,
  and unsupported languages remain unresolved.
- For sensor commands, check the corresponding runtime sensor status if PHOS
  reports that the reading is unavailable.

See [Voice capture and STT](voice.md) for microphone and STT setup.
