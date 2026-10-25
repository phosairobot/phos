from dataclasses import replace
from types import SimpleNamespace

import pytest

from robot.ui import AmbientOverlayState, BlinkPhase, EyeRenderer, FaceExpression, FaceState, TkEyeDisplay, VisualAccent
from robot.ui.display import ambient_overlay_layout
from robot.ui.eyes import upper_lid_points, valid_polygon_points


def render(state, timestamp=0.0):
    return EyeRenderer().render(state, timestamp=timestamp)


def test_neutral_state_renders_two_open_centered_eyes():
    frame = render(FaceState())

    assert frame.width == 800
    assert frame.height == 600
    assert frame.eyes[0].center_x < 400 < frame.eyes[1].center_x
    assert all(eye.radius_y > 100 for eye in frame.eyes)


def test_warm_overlay_has_two_small_centered_sweat_drops():
    frame = render(FaceState(ambient_overlay=AmbientOverlayState(temperature="warm")))
    layout = ambient_overlay_layout(frame)
    drops = layout.sweat

    assert len(drops) == 2
    assert all(radius_x < 20 and radius_y < 25 for _, _, radius_x, radius_y in drops)
    assert abs(sum(x for x, *_ in drops) / len(drops) - frame.width / 2) < frame.width * .01
    assert drops[0] != drops[1]
    assert layout.safe_top <= min(y - radius_y for _, y, _, radius_y in drops)
    assert max(y + radius_y for _, y, _, radius_y in drops) < min(eye.center_y - eye.radius_y for eye in frame.eyes)


def test_cold_and_haze_overlays_share_the_centered_forehead_anchor():
    cold_frame = render(FaceState(ambient_overlay=AmbientOverlayState(temperature="cold")))
    haze_frame = render(FaceState(ambient_overlay=AmbientOverlayState(air_quality="bad")))
    cold = ambient_overlay_layout(cold_frame)
    haze_layout = ambient_overlay_layout(haze_frame)
    haze = haze_layout.haze

    assert cold.snow[0] == cold.anchor_x == cold_frame.width / 2
    assert cold.snow[1] == cold.anchor_y
    assert cold.safe_top <= cold.snow[1] - cold.snow[2]
    assert cold.snow[1] + cold.snow[2] <= cold.safe_bottom
    assert len(haze) == 3
    assert all((x1 + x2) / 2 == haze_layout.anchor_x == haze_frame.width / 2 for x1, _, x2, _ in haze)
    assert all(haze_layout.safe_top <= y1 < y2 <= haze_layout.safe_bottom for _, y1, _, y2 in haze)
    assert max(y2 for _, _, _, y2 in haze) < min(eye.center_y - eye.radius_y for eye in haze_frame.eyes)


def test_warning_haze_uses_the_same_near_eye_safe_band_as_bad_haze():
    warning_frame = render(FaceState(ambient_overlay=AmbientOverlayState(air_quality="warning")))
    bad_frame = render(FaceState(ambient_overlay=AmbientOverlayState(air_quality="bad")))
    warning = ambient_overlay_layout(warning_frame)
    bad = ambient_overlay_layout(bad_frame)

    assert len(warning.haze) == 2
    assert len(bad.haze) == 3
    assert warning.anchor_x == bad.anchor_x == warning_frame.width / 2
    assert warning.safe_top == bad.safe_top
    assert warning.safe_bottom == bad.safe_bottom
    assert all(warning.safe_top <= y1 < y2 <= warning.safe_bottom for _, y1, _, y2 in warning.haze)


def test_combined_environmental_overlays_are_in_bounds_and_above_eye_geometry():
    frame = render(FaceState(ambient_overlay=AmbientOverlayState(temperature="warm", air_quality="warning")))
    layout = ambient_overlay_layout(frame)
    drops, haze = layout.sweat, layout.haze
    eye_top = min(eye.center_y - eye.radius_y for eye in frame.eyes)

    assert max(y + radius_y for _, y, _, radius_y in drops) < min(y1 for _, y1, _, _ in haze)
    assert max(y2 for _, _, _, y2 in haze) < eye_top
    assert layout.safe_bottom < eye_top
    for x, y, radius_x, radius_y in drops:
        assert radius_x <= x <= frame.width - radius_x
        assert radius_y <= y <= frame.height - radius_y
    for x1, y1, x2, y2 in haze:
        assert 0 <= x1 < x2 <= frame.width
        assert 0 <= y1 < y2 <= frame.height


def test_environmental_overlay_does_not_change_eye_geometry_or_iris():
    neutral = render(FaceState())
    overlay = render(FaceState(ambient_overlay=AmbientOverlayState(temperature="cold", air_quality="warning")))

    assert overlay.eyes == neutral.eyes
    assert overlay.iris_color == neutral.iris_color


def test_expression_transition_interpolates_eye_shape_and_gaze():
    renderer = EyeRenderer(transition_seconds=0.2)
    renderer.render(FaceState(), timestamp=0.0)
    partial = renderer.render(
        FaceState(expression=FaceExpression.HAPPY, reaction_strength=1.0, pupil_x=0.8), timestamp=0.1
    )
    completed = renderer.render(
        FaceState(expression=FaceExpression.HAPPY, reaction_strength=1.0, pupil_x=0.8), timestamp=0.3
    )

    assert partial.eyes[0].radius_y > completed.eyes[0].radius_y
    assert partial.eyes[0].pupil_x < completed.eyes[0].pupil_x


def test_pupil_coordinates_are_clamped_inside_eye_geometry():
    frame = render(FaceState(pupil_x=99, pupil_y=-99))
    for eye in frame.eyes:
        assert abs(eye.pupil_x - eye.center_x) + eye.pupil_radius < eye.radius_x
        assert abs(eye.pupil_y - eye.center_y) + eye.pupil_radius < eye.radius_y


def test_blink_phase_controls_aperture_without_interpolation_delay():
    renderer = EyeRenderer()
    renderer.render(FaceState(), timestamp=0.0)
    closing = renderer.render(FaceState(blink_phase=BlinkPhase.CLOSING, blink_progress=0.5), timestamp=0.01)
    closed = renderer.render(FaceState(blink_phase=BlinkPhase.CLOSED), timestamp=0.02)

    assert closing.eyes[0].radius_y < 100
    assert closed.eyes[0].closed


def test_reaction_strength_zero_keeps_neutral_geometry():
    neutral = render(FaceState())
    dormant_happy = render(FaceState(expression=FaceExpression.HAPPY, reaction_strength=0.0))

    assert neutral.eyes[0].radius_y == dormant_happy.eyes[0].radius_y


def test_semantic_accent_changes_eye_and_pupil_colors():
    neutral = render(FaceState())
    warm = render(FaceState(accent=VisualAccent.WARM, reaction_strength=1.0))
    alert = render(FaceState(accent=VisualAccent.ALERT, reaction_strength=1.0))

    assert neutral.eye_color == "#EAFBFF"
    assert warm.eye_color == "#28E0B0"
    assert warm.pupil_color == "#063B3D"
    assert alert.eye_color == "#FFC857"
    assert alert.eye_color != warm.eye_color


def test_accent_and_background_colors_interpolate_smoothly():
    renderer = EyeRenderer(transition_seconds=0.2)
    neutral = renderer.render(FaceState(), timestamp=0.0)
    partial = renderer.render(
        FaceState(background="#08101E", accent=VisualAccent.SLEEPY, reaction_strength=1.0), timestamp=0.1
    )
    completed = renderer.render(
        FaceState(background="#08101E", accent=VisualAccent.SLEEPY, reaction_strength=1.0), timestamp=0.3
    )

    assert neutral.eye_color != partial.eye_color != completed.eye_color
    assert neutral.background != partial.background != completed.background
    assert completed.eye_color == "#A78BFA"


def test_sleepy_expression_has_significantly_smaller_aperture():
    neutral = render(FaceState())
    sleepy = render(FaceState(expression=FaceExpression.SLEEPY, reaction_strength=1.0))

    assert sleepy.eyes[0].radius_y < neutral.eyes[0].radius_y * 0.5


def test_phase_one_expression_profiles_are_distinct_and_bounded_with_custom_iris():
    renderer = EyeRenderer(iris_color="#7a5cff")
    frames = {
        expression: renderer.render(FaceState(expression=expression, reaction_strength=1), timestamp=index)
        for index, expression in enumerate((FaceExpression.SAD, FaceExpression.ANGRY,
                                            FaceExpression.FOCUSED, FaceExpression.TIRED))
    }

    assert {expression.value for expression in frames} == {"sad", "angry", "focused", "tired"}
    assert all(frame.iris_color == "#7A5CFF" for frame in frames.values())
    assert frames[FaceExpression.SAD].eyes[0].pupil_y > render(FaceState()).eyes[0].pupil_y
    assert frames[FaceExpression.TIRED].eyes[0].radius_y < frames[FaceExpression.SAD].eyes[0].radius_y
    assert frames[FaceExpression.SAD].eyes[0].upper_lid_depth == .82
    assert frames[FaceExpression.SAD].eyes[0].upper_lid_depth > frames[FaceExpression.TIRED].eyes[0].upper_lid_depth
    assert frames[FaceExpression.SAD].eyes[0].upper_lid_slope < 0
    assert frames[FaceExpression.TIRED].eyes[0].upper_lid_slope == 0
    focused_eye = frames[FaceExpression.FOCUSED].eyes[0]
    angry_eye = frames[FaceExpression.ANGRY].eyes[0]
    assert focused_eye.upper_lid_slope == pytest.approx(.42)
    assert focused_eye.upper_lid_depth == pytest.approx(.68)
    assert focused_eye.upper_lid_curve == pytest.approx(.015)
    assert angry_eye.upper_lid_slope == pytest.approx(.50)
    assert angry_eye.upper_lid_depth == pytest.approx(.74)
    assert angry_eye.upper_lid_curve == pytest.approx(.01)
    # ANGRY uses the same inner-low direction as FOCUSED, but with stronger
    # tension and upper-eye coverage.
    assert angry_eye.upper_lid_slope > focused_eye.upper_lid_slope
    assert angry_eye.upper_lid_depth > focused_eye.upper_lid_depth
    assert focused_eye.radius_y > angry_eye.radius_y
    assert focused_eye.squint < angry_eye.squint
    for frame in frames.values():
        for eye in frame.eyes:
            assert 0 < eye.radius_x <= frame.width / 2 and 0 < eye.radius_y <= frame.height / 2
            assert 0 <= eye.pupil_x <= frame.width and 0 <= eye.pupil_y <= frame.height


def test_sad_and_tired_lid_caps_follow_the_eye_contour():
    frames = {
        expression: render(FaceState(expression=expression, reaction_strength=1))
        for expression in (FaceExpression.SAD, FaceExpression.TIRED)
    }
    caps = {expression: upper_lid_points(frame.eyes[0]) for expression, frame in frames.items()}

    assert caps[FaceExpression.SAD] != caps[FaceExpression.TIRED]
    for expression, points in caps.items():
        eye = frames[expression].eyes[0]
        assert len(points) > 3
        for x, y in points:
            normalized_distance = ((x - eye.center_x) / (eye.radius_x - 5)) ** 2 + ((y - eye.center_y) / (eye.radius_y - 5)) ** 2
            assert normalized_distance <= 1.000001


def test_sad_lids_are_soft_mirrored_low_outer_to_high_inner_geometry():
    frame = render(FaceState(expression=FaceExpression.SAD, reaction_strength=1))
    left, right = frame.eyes
    left_points, right_points = upper_lid_points(left), upper_lid_points(right)

    assert left.upper_lid_depth == right.upper_lid_depth == pytest.approx(.82)
    assert left.upper_lid_curve == right.upper_lid_curve == pytest.approx(-.025)
    # The first/last arc boundary points are outer→inner for the left eye and
    # inner→outer for the right eye.
    assert left_points[0][1] > left_points[12][1]
    assert right_points[0][1] < right_points[12][1]
    # The raised interior point proves the boundary is gently curved, not flat.
    left_chord_midpoint = (left_points[12][1] + left_points[-1][1]) / 2
    assert left_points[18][1] < left_chord_midpoint
    for eye, points in ((left, left_points), (right, right_points)):
        for x, y in points:
            distance = ((x - eye.center_x) / (eye.radius_x - 5)) ** 2 + ((y - eye.center_y) / (eye.radius_y - 5)) ** 2
            assert distance <= 1.000001


def test_phase_two_expression_profiles_are_distinct_symmetric_where_expected_and_bounded():
    frames = {
        expression: render(FaceState(expression=expression, reaction_strength=1))
        for expression in (FaceExpression.CONFUSED, FaceExpression.TIRED,
                           FaceExpression.SCARED, FaceExpression.SKEPTICAL)
    }
    neutral = render(FaceState())
    surprised = render(FaceState(expression=FaceExpression.SURPRISED, reaction_strength=1))

    confused, tired, scared, skeptical = (frames[expression] for expression in frames)
    assert confused.eyes[0].radius_y != confused.eyes[1].radius_y
    assert confused.eyes[0].upper_lid_depth != confused.eyes[1].upper_lid_depth
    assert tired.eyes[0].radius_y == tired.eyes[1].radius_y < render(FaceState(expression=FaceExpression.SAD, reaction_strength=1)).eyes[0].radius_y
    assert tired.eyes[0].upper_lid_depth == tired.eyes[1].upper_lid_depth > .7
    assert scared.eyes[0].radius_y == scared.eyes[1].radius_y > neutral.eyes[0].radius_y
    assert scared.eyes[0].iris_radius == scared.eyes[1].iris_radius < neutral.eyes[0].iris_radius
    assert scared.eyes[0].iris_radius < surprised.eyes[0].iris_radius
    assert skeptical.eyes[0].radius_y < skeptical.eyes[1].radius_y
    assert skeptical.eyes[0].upper_lid_depth > skeptical.eyes[1].upper_lid_depth
    assert confused.eyes != skeptical.eyes
    for frame in frames.values():
        for eye in frame.eyes:
            for x, y in upper_lid_points(eye):
                distance = ((x - eye.center_x) / (eye.radius_x - 5)) ** 2 + ((y - eye.center_y) / (eye.radius_y - 5)) ** 2
                assert distance <= 1.000001


def test_focused_lid_is_sloped_and_bounded_by_the_eye_contour():
    frame = render(FaceState(expression=FaceExpression.FOCUSED, reaction_strength=1))
    left, right = frame.eyes
    left_points = upper_lid_points(left)
    right_points = upper_lid_points(right)

    assert left.upper_lid_slope == .42 and right.upper_lid_slope == -.42
    assert left.upper_lid_curve == right.upper_lid_curve == .015
    assert len(left_points) == len(right_points) == 25
    # The left eye's inner endpoint (arc end) is lower than its outer start.
    assert left_points[12][1] > left_points[0][1]
    # The right eye mirrors it: its left endpoint is the inner one.
    assert right_points[0][1] > right_points[12][1]
    # The boundary has a deliberate, but near-straight, downward bow.
    midpoint_y = (left_points[12][1] + left_points[-1][1]) / 2
    assert 0 < left_points[18][1] - midpoint_y < left.radius_y * .02
    for eye, points in ((left, left_points), (right, right_points)):
        for x, y in points:
            normalized_distance = ((x - eye.center_x) / (eye.radius_x - 5)) ** 2 + ((y - eye.center_y) / (eye.radius_y - 5)) ** 2
            assert normalized_distance <= 1.000001


def test_angry_lid_is_inward_low_mirrored_and_bounded_by_the_eye_contour():
    frame = render(FaceState(expression=FaceExpression.ANGRY, reaction_strength=1))
    left, right = frame.eyes
    left_points = upper_lid_points(left)
    right_points = upper_lid_points(right)

    assert left.upper_lid_slope == .50 and right.upper_lid_slope == -.50
    assert left.upper_lid_curve == right.upper_lid_curve == .01
    assert len(left_points) == len(right_points) == 25
    # Left eye: the inner endpoint is on the right and sits lower.
    assert left_points[12][1] > left_points[0][1]
    # Right eye: the inner endpoint is on the left and sits lower.
    assert right_points[0][1] > right_points[12][1]
    for eye, points in ((left, left_points), (right, right_points)):
        for x, y in points:
            normalized_distance = ((x - eye.center_x) / (eye.radius_x - 5)) ** 2 + ((y - eye.center_y) / (eye.radius_y - 5)) ** 2
            assert normalized_distance <= 1.000001


def test_sad_gaze_has_subtle_inward_bias_and_preserves_tracking_input():
    baseline = render(FaceState(expression=FaceExpression.SAD, reaction_strength=1))
    tracked = render(FaceState(expression=FaceExpression.SAD, reaction_strength=1, pupil_x=.5))
    neutral = render(FaceState())

    for eye, expected in zip(baseline.eyes, (.08, -.08)):
        maximum = eye.radius_x * .52 - eye.iris_radius
        assert abs((eye.pupil_x - eye.center_x) / maximum - expected) < 1e-9
    for eye, expected in zip(tracked.eyes, (.58, .42)):
        maximum = eye.radius_x * .52 - eye.iris_radius
        assert abs((eye.pupil_x - eye.center_x) / maximum - expected) < 1e-9
    assert all(eye.pupil_x == eye.center_x for eye in neutral.eyes)


def test_tk_skips_an_insufficient_lid_polygon_without_crashing():
    frame = render(FaceState(expression=FaceExpression.SAD, reaction_strength=1))
    tiny_eye = replace(frame.eyes[0], radius_y=2.0)
    display = object.__new__(TkEyeDisplay)
    polygons = []
    display._canvas = SimpleNamespace(
        create_oval=lambda *args, **kwargs: None,
        create_polygon=lambda *args, **kwargs: polygons.append((args, kwargs)),
    )

    assert upper_lid_points(tiny_eye) == ()
    assert not valid_polygon_points(upper_lid_points(tiny_eye))
    display._draw_eye(tiny_eye, frame)
    assert polygons == []


def test_invalid_face_state_values_are_safely_normalized():
    state = FaceState(
        background="not-a-colour",
        eye_open=100,
        eye_asymmetry=100,
        squint=-2,
        pupil_x=4,
        pupil_y=-4,
        reaction_strength=4,
        accent="not-an-accent",
        blink_progress=-1,
    ).normalized()

    assert state.background == "#10243A"
    assert state.eye_open == 1.25
    assert state.eye_asymmetry == .5
    assert state.squint == 0.0
    assert state.pupil_x == 1.0
    assert state.pupil_y == -1.0
    assert state.reaction_strength == 1.0
    assert state.accent is VisualAccent.NEUTRAL
    assert state.blink_progress == 0.0


def test_configured_iris_theme_changes_stylized_iris_color():
    cyan = EyeRenderer(iris_color="cyan").render(FaceState(), timestamp=0)
    violet = EyeRenderer(iris_color="violet").render(FaceState(), timestamp=0)

    assert cyan.iris_color == "#28CEEB"
    assert violet.iris_color == "#A670F5"
    assert cyan.eyes[0].iris_radius > cyan.eyes[0].pupil_radius


def test_custom_iris_color_uses_the_same_renderer_path_as_presets():
    frame = EyeRenderer(iris_color="#7a5cff").render(FaceState(), timestamp=0)

    assert frame.iris_color == "#7A5CFF"


def test_semantic_iris_tint_interpolates_from_face_state():
    renderer = EyeRenderer(iris_color="blue", transition_seconds=.2)
    neutral = renderer.render(FaceState(), timestamp=0)
    partial = renderer.render(
        FaceState(accent=VisualAccent.ALERT, reaction_strength=1), timestamp=.1
    )
    settled = renderer.render(
        FaceState(accent=VisualAccent.ALERT, reaction_strength=1), timestamp=.3
    )

    assert neutral.iris_color != partial.iris_color != settled.iris_color
    assert settled.iris_color != "#A670F5"


def test_expression_states_remain_face_state_driven():
    renderer = EyeRenderer()
    neutral = renderer.render(FaceState(), timestamp=0)
    curious = renderer.render(
        FaceState(expression=FaceExpression.CURIOUS, reaction_strength=1), timestamp=1
    )
    surprised = renderer.render(
        FaceState(expression=FaceExpression.SURPRISED, reaction_strength=1), timestamp=2
    )

    assert curious.eyes[0].radius_y != curious.eyes[1].radius_y
    assert surprised.eyes[0].radius_y > neutral.eyes[0].radius_y


def test_explicit_eye_asymmetry_mirrors_eyes_and_overrides_expression_bias():
    left = render(FaceState(expression=FaceExpression.CURIOUS, reaction_strength=1, eye_open=1.12,
                            eye_asymmetry=.18))
    right = render(FaceState(expression=FaceExpression.CURIOUS, reaction_strength=1, eye_open=1.12,
                             eye_asymmetry=-.18))
    symmetric = render(FaceState(expression=FaceExpression.CURIOUS, reaction_strength=1, eye_open=1.23,
                                 eye_asymmetry=0))

    assert left.eyes[0].radius_y > left.eyes[1].radius_y
    assert right.eyes[1].radius_y > right.eyes[0].radius_y
    assert left.eyes[0].radius_y == right.eyes[1].radius_y
    assert left.eyes[1].radius_y == right.eyes[0].radius_y
    assert symmetric.eyes[0].radius_y == symmetric.eyes[1].radius_y


def test_explicit_eye_asymmetry_interpolates_each_eye_through_neutral():
    renderer = EyeRenderer(transition_seconds=.2)
    renderer.render(FaceState(eye_asymmetry=.18), timestamp=0)
    halfway = renderer.render(FaceState(eye_asymmetry=-.18), timestamp=.1)
    completed = renderer.render(FaceState(eye_asymmetry=-.18), timestamp=.2)

    assert abs(halfway.eyes[0].radius_y - halfway.eyes[1].radius_y) < abs(completed.eyes[0].radius_y - completed.eyes[1].radius_y)
    assert completed.eyes[1].radius_y > completed.eyes[0].radius_y


def test_tk_draws_separate_eye_body_iris_pupil_and_highlights():
    frame = EyeRenderer(iris_color="amber").render(FaceState(), timestamp=0)
    display = object.__new__(TkEyeDisplay)
    calls = []
    display._canvas = SimpleNamespace(
        create_oval=lambda *args, **kwargs: calls.append(kwargs),
        create_arc=lambda *args, **kwargs: calls.append(kwargs),
    )
    display._tk = SimpleNamespace(ARC="arc")

    display._draw_eye(frame.eyes[0], frame)

    fills = [call.get("fill") for call in calls]
    assert len(calls) == 8
    assert frame.iris_color in fills
    assert frame.pupil_color in fills
    assert "#071522" in fills
