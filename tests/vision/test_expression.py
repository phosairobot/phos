from pathlib import Path
import logging

from robot.vision import OpenCVExpressionProvider


class FakeImage:
    shape = (80, 80, 3)


class FakeScores:
    def flatten(self):
        return [0.0, 3.0, 0.0]


class FakeNetwork:
    def __init__(self):
        self.input_blob = None

    def setInput(self, blob):
        self.input_blob = blob

    def forward(self):
        return FakeScores()


class FakeDnn:
    def __init__(self):
        self.blob_arguments = None

    def blobFromImage(self, image, **kwargs):
        self.blob_arguments = (image, kwargs)
        return "blob"


class FakeCv2:
    COLOR_RGB2GRAY = "rgb-to-gray"

    def __init__(self):
        self.dnn = FakeDnn()
        self.convert_arguments = None

    def cvtColor(self, image, conversion):
        self.convert_arguments = (image, conversion)
        return "grayscale-crop"


def test_expression_provider_converts_rgb_face_crop_to_grayscale_when_configured():
    provider = OpenCVExpressionProvider(
        Path("unused.onnx"),
        ("neutral", "happiness", "surprise"),
        input_size=(64, 64),
        scale=1.0,
        swap_rb=True,
        grayscale=True,
    )
    fake_cv2 = FakeCv2()
    fake_network = FakeNetwork()
    provider._cv2 = fake_cv2
    provider._network = fake_network

    image = FakeImage()
    observation = provider._classify_sync(image)

    assert fake_cv2.convert_arguments == (image, fake_cv2.COLOR_RGB2GRAY)
    assert fake_cv2.dnn.blob_arguments == (
        "grayscale-crop",
        {
            "scalefactor": 1.0,
            "size": (64, 64),
            "mean": (0.0, 0.0, 0.0),
            "swapRB": False,
            "crop": False,
        },
    )
    assert fake_network.input_blob == "blob"
    assert observation.label == "happiness"
    probabilities = dict(observation.probabilities)
    assert set(probabilities) == {"neutral", "happiness", "surprise"}
    assert probabilities["happiness"] == observation.confidence
    assert abs(sum(probabilities.values()) - 1.0) < 1e-9


def test_expression_diagnostics_report_scores_and_probabilities_without_saving_images(caplog):
    provider = OpenCVExpressionProvider(
        Path("unused.onnx"),
        ("neutral", "happiness", "surprise"),
        diagnostics=True,
    )
    provider._cv2 = FakeCv2()
    provider._network = FakeNetwork()

    with caplog.at_level(logging.DEBUG, logger="robot.vision.expression"):
        provider._classify_sync(FakeImage())

    assert len(caplog.messages) == 1
    assert "raw_scores=[0.0, 3.0, 0.0]" in caplog.messages[0]
    assert "probabilities=" in caplog.messages[0]
    assert "top=happiness" in caplog.messages[0]
    assert "inference_ms=" in caplog.messages[0]


def test_mobilefacenet_preprocessing_matches_reference_rgb_normalization():
    import pytest
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")

    class Network(FakeNetwork):
        def forward(self):
            return np.array([[0, 0, 0, 6, 0, 0, 0]], dtype=np.float32)

    provider = OpenCVExpressionProvider.from_preset(Path("unused.onnx"), "mobilefacenet")
    provider._cv2 = cv2
    provider._network = Network()
    crop = np.zeros((80, 80, 3), dtype=np.uint8)
    crop[:] = [0, 127, 255]  # BGR bytes from the current Pi RGB888 capture.
    observation = provider._classify_sync(crop)

    blob = provider._network.input_blob
    assert blob.shape == (1, 3, 112, 112)
    np.testing.assert_allclose(blob[0, :, 0, 0], [1, 127 / 127.5 - 1, -1], atol=1e-6)
    assert observation.label == "happy"
    assert observation.confidence > 0.98
    assert provider.last_inference_ms >= 0
