from pathlib import Path

from robot.vision.detector import OpenCVFaceDetector, _default_cascade_path


class OpenCVWithoutData:
    pass


class OpenCVWithData:
    class data:
        haarcascades = "/custom/haarcascades"


def test_default_cascade_path_uses_raspberry_pi_os_location_when_cv2_data_is_unavailable():
    assert _default_cascade_path(OpenCVWithoutData()) == Path(
        "/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"
    )


def test_default_cascade_path_uses_cv2_bundled_location_when_available():
    assert _default_cascade_path(OpenCVWithData()) == Path(
        "/custom/haarcascades/haarcascade_frontalface_default.xml"
    )


def test_face_detector_converts_internal_bgr_frames_to_grayscale():
    class Cascade:
        def detectMultiScale(self, *_args, **_kwargs):
            return []

    class Cv2:
        COLOR_BGR2GRAY = "bgr-to-gray"

        def __init__(self):
            self.arguments = None

        def cvtColor(self, frame, conversion):
            self.arguments = (frame, conversion)
            return "gray"

    frame = object()
    cv2 = Cv2()
    detector = OpenCVFaceDetector()
    detector._cv2 = cv2
    detector._cascade = Cascade()
    assert detector._detect_sync(frame) == ()
    assert cv2.arguments == (frame, cv2.COLOR_BGR2GRAY)
