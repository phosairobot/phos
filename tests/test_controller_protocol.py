import pytest

from robot.hardware.controller_protocol import (ControllerMessage, JsonLinesDecoder, ProtocolError,
                                                 decode_frame, validate_led_animation)


def test_json_lines_decodes_partial_and_multiple_frames():
    frame = ControllerMessage("heartbeat", {}).document()
    encoded = (str(frame).replace("'", '"')).encode()
    decoder = JsonLinesDecoder()
    assert decoder.feed(encoded[:8]) == []
    assert [item.type for item in decoder.feed(encoded[8:] + b"\n" + encoded + b"\n")] == ["heartbeat", "heartbeat"]


@pytest.mark.parametrize("frame", [b"{", b'{"protocol":"phos-hw","version":2,"type":"ping","payload":{}}', b'{"protocol":"phos-hw","version":1,"type":"bad","payload":{}}'])
def test_invalid_frames_are_bounded(frame):
    with pytest.raises(ProtocolError): decode_frame(frame)


def test_led_animation_validation_includes_breathe_and_rejects_bad_parameters():
    validate_led_animation({"animation": "breathe", "color": "#6F5CFF", "brightness": .65, "period_ms": 3200, "min_intensity": .08, "max_intensity": 1, "duration_ms": 0})
    with pytest.raises(ProtocolError): validate_led_animation({"animation": "breathe", "brightness": 2})
