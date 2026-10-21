---
hide:
  - navigation
  - toc
classes:
  - phos-home
---

# Visual system

<div class="phos-page-intro" markdown>

<span class="phos-kicker">THE PHOS FACE</span>

PHOS’s face is its primary output: a lightweight animated eye renderer on the
800×600 display, optionally complemented by a WS2812B LED ring and environmental
overlays.

</div>

## A persistent baseline, with temporary reactions

The selected persistent visual source is one of **manual**, **environment** or
**PHOS state**. It establishes the baseline iris/accent intent. Higher-priority
robot states and short IMU events such as tilt, shake and impact temporarily
override that intent. They never overwrite saved base configuration; when the
effect ends, PHOS restores the resolved persistent state.

## Eye expressions

The manually selectable expressions are neutral, happy, curious, surprised,
sad, angry, focused, tired, confused, scared and skeptical. They are semantic
`FaceExpression` values, independent of `RobotState`; selecting one persists
until explicitly replaced. Their canonical profiles control shared eye geometry,
so live Tk rendering and Appearance Preview remain aligned. All profiles use the
configured iris color, including custom `#RRGGBB` colors. HAPPY's blue and
SURPRISED's yellow eye-body accents are intentional exceptions rather than a
generic emotion-color mapping: eye geometry carries the expression. FURIOUS and
expression intensity are not implemented.

## One intent, three outputs

- **Eyes:** `EyeRenderer` applies `FaceState` expression, gaze, blink, semantic
  accent and the named base iris theme.
- **LED ring:** the optional low-rate controller consumes the same
  provider-neutral semantic intent, including environmental colors and motion
  fills, without accessing vision or sensor objects.
- **Overlays:** independently confirmed environmental markers—sweat, snow/ice
  and haze—decorate a safe band without changing eye geometry or pupils.

The detailed contracts are maintained in the [architecture reference](architecture.md#visual-reaction-semantics) and [1.1.0 release record](release-1.1.0.md).
