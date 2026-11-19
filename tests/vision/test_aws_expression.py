import asyncio
import json
import logging
import sys
from types import SimpleNamespace

import pytest

from robot.runtime import RuntimeConfig, build_runtime
from robot.ui import MemoryEyeDisplay
from robot.vision.aws_expression import AWSExpressionProvider, CloudExpressionConfig, map_response
from robot.vision.provider import ExpressionObservation, FaceRegion
from robot.vision.smoother import ExpressionSmoother
from robot.vision.pipeline import VisionPipeline, VISION_EXPRESSION_STABLE, VISION_FACE_POSITION
from robot.core import EventBus


class Crop:
    shape = (80, 80, 3)
    size = 19200
    value = 0.0

    def copy(self):
        return self


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


def response(label="HAPPY", confidence=99):
    return {"FaceDetails": [{"Confidence": 99, "Emotions": [
        {"Type": label, "Confidence": confidence}, {"Type": "CALM", "Confidence": 1}]}]}


@pytest.fixture
def fake_cv(monkeypatch):
    calls = []
    def encode(extension, crop, options):
        calls.append(crop)
        return True, SimpleNamespace(tobytes=lambda: b"selected-crop-jpeg")
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(
        IMWRITE_JPEG_QUALITY=1, imencode=encode))
    return calls


def provider(clock, *, client=None, **settings):
    result = AWSExpressionProvider(CloudExpressionConfig(**settings), clock=clock, client=client)
    result._fingerprint = lambda crop: (crop.value,) * 256
    return result


async def complete(p):
    assert p._task is not None
    await p._task


@pytest.mark.parametrize("raw,expected", [("HAPPY", "happy"), ("SURPRISED", "surprised"),
                                         ("ANGRY", "unknown"), ("CONFUSED", "unknown")])
def test_mapping(raw, expected):
    observation = map_response(response(raw), .9)
    assert observation.label == expected
    assert set(dict(observation.probabilities)) == {"happy", "surprised", "neutral", "unknown"}
    assert sum(dict(observation.probabilities).values()) == pytest.approx(1)


def test_mapping_does_not_amplify_weak_or_ambiguous_confidence():
    assert map_response(response(confidence=20), .9).label == "unknown"
    result = response()
    result["FaceDetails"][0]["Emotions"][1]["Confidence"] = 99
    assert map_response(result, .9).confidence == .5
    for invalid in ({"FaceDetails": []}, {"FaceDetails": response()["FaceDetails"] * 2},
                    {"FaceDetails": [{"Confidence": 10}]}):
        assert map_response(invalid, .9) is None
    assert map_response(response(confidence=float("nan")), .9) is None


def test_policy_cache_change_ttl_session_and_crop_only(fake_cv):
    async def exercise():
        clock, calls = Clock(), []
        client = SimpleNamespace(detect_faces=lambda **kw: calls.append(kw) or response())
        p = provider(clock, client=client, max_requests_per_session=2)
        crop = Crop()
        assert await p.classify(None) is None
        assert await p.classify(crop) is None  # stable gate
        assert p.requests == 0
        clock.value = 1
        assert await p.classify(crop) is None
        await complete(p)
        first = await p.classify(crop)
        assert first.sampled_at == 1
        clock.value = 30
        crop.value = 1
        assert await p.classify(crop) is first  # cooldown
        assert p.requests == 1
        clock.value = 31
        assert await p.classify(crop) is first  # changed input requests refresh
        await complete(p)
        second = await p.classify(crop)
        assert second.sampled_at == 31
        clock.value = 120
        assert await p.classify(crop) is second
        clock.value = 121
        assert await p.classify(crop) is None  # TTL + session cap
        assert p.requests == 2
        assert calls == [{"Image": {"Bytes": b"selected-crop-jpeg"}, "Attributes": ["EMOTIONS"]}] * 2
        assert fake_cv == [crop, crop]
        await p.close()
    asyncio.run(exercise())


def test_unchanged_refresh_and_maximum_rate(fake_cv):
    async def exercise():
        clock = Clock()
        p = provider(clock, client=SimpleNamespace(detect_faces=lambda **kw: response()),
                     cooldown_seconds=1, max_requests_per_minute=1)
        crop = Crop()
        await p.classify(crop)
        clock.value = 1
        await p.classify(crop)
        await complete(p)
        clock.value = 31
        await p.classify(crop)
        assert p.requests == 1
        clock.value = 61
        await p.classify(crop)
        assert p.requests == 2
        await p.close()
    asyncio.run(exercise())


def test_identical_crop_is_not_resent_at_cooldown(fake_cv):
    async def exercise():
        clock = Clock()
        p = provider(clock, client=SimpleNamespace(detect_faces=lambda **kw: response()))
        await p.classify(Crop())
        clock.value = 1
        await p.classify(Crop())
        await complete(p)
        clock.value = 31
        assert await p.classify(Crop()) is not None
        assert p.requests == 1
        await p.close()
    asyncio.run(exercise())


def test_failure_backoff_clears_cache_and_redacts_exception(fake_cv, caplog):
    async def exercise():
        clock = Clock()
        def fail(**kwargs):
            raise RuntimeError("SECRET must never be logged")
        p = provider(clock, client=SimpleNamespace(detect_faces=fail), retry_max_seconds=120)
        await p.classify(Crop())
        clock.value = 1
        await p.classify(Crop())
        await complete(p)
        assert await p.classify(Crop()) is None
        clock.value = 60
        await p.classify(Crop())
        assert p.requests == 1
        clock.value = 61
        await p.classify(Crop())
        await complete(p)
        assert p._next_request == 181
        await p.close()
    with caplog.at_level(logging.WARNING):
        asyncio.run(exercise())
    assert "SECRET" not in caplog.text
    assert "RuntimeError" in caplog.text


def test_cached_samples_do_not_confirm_expression():
    smoother = ExpressionSmoother(maximum_gap_seconds=90)
    first = ExpressionObservation("happy", .99, (("happy", .99), ("unknown", .01)), 1)
    for timestamp in (1, 2, 3, 4, 30):
        assert smoother.observe(first, timestamp=timestamp) is None
    assert smoother.temporal_state["count"] == 1
    second = ExpressionObservation("happy", .99, first.probabilities, 61)
    assert smoother.observe(second) is None
    third = ExpressionObservation("happy", .99, first.probabilities, 121)
    assert smoother.observe(third).label == "happy"
    assert smoother.observe(third).observed_for_ms == 120000
    assert smoother.temporal_state["count"] == 3
    assert smoother.observe(None) is None


def test_selection_and_tracking_continue_during_request_and_discard_lost_face():
    async def exercise():
        clock = Clock()
        p = provider(clock)
        release = asyncio.Event()
        async def request(crop, sampled_at, generation):
            await release.wait()
            if generation == p._generation:
                p._cache = ExpressionObservation("happy", .99, (("happy", .99), ("unknown", .01)), sampled_at)
                p._expires = sampled_at + 90
        p._request = request
        class Frame(Crop):
            shape = (480, 640, 3)
            def __getitem__(self, key):
                return Crop()
        class Camera:
            async def capture_frame(self):
                return Frame()
        class Detector:
            faces = [FaceRegion(100, 100, 80, 80)]
            async def detect(self, frame):
                return self.faces
        detector = Detector()
        bus, positions, expressions = EventBus(), [], []
        bus.subscribe(VISION_FACE_POSITION, lambda event: positions.append(event))
        bus.subscribe(VISION_EXPRESSION_STABLE, lambda event: expressions.append(event.data))
        pipeline = VisionPipeline(Camera(), detector, p, ExpressionSmoother(), events=bus)
        for index in range(12):
            clock.value = index / 2
            await asyncio.wait_for(pipeline.process_once(timestamp=clock.value), .1)
        assert len(positions) == 12 and p.requests == 1
        assert all(item["visual_expression"]["label"] == "unknown" for item in expressions)
        detector.faces = []
        clock.value = 6
        await pipeline.process_once(timestamp=6)
        release.set()
        await complete(p)
        assert p._cache is None
        detector.faces = [FaceRegion(100, 100, 80, 80)]
        await pipeline.process_once(timestamp=6.5)
        assert p.requests == 1
        await p.close()
    asyncio.run(exercise())


@pytest.mark.parametrize("settings", [dict(cooldown_seconds=0), dict(cache_ttl_seconds=float("nan")),
    dict(max_requests_per_minute=-1), dict(max_requests_per_session=1.5), dict(change_threshold=2),
    dict(minimum_face_confidence=-1), dict(retry_max_seconds=1), dict(stable_seconds=0),
    dict(refresh_seconds=90), dict(region=""), dict(read_timeout_seconds=float("inf"))])
def test_invalid_cloud_config(settings):
    with pytest.raises(ValueError):
        CloudExpressionConfig(**settings)


def test_provider_selection_and_json_config(tmp_path):
    path = tmp_path / "settings.json"
    document = RuntimeConfig().to_dict()
    document["expression"].update(provider="aws", enabled=True)
    document["expression"]["aws"].update(region="eu-west-1", cooldown_seconds=45)
    path.write_text(json.dumps(document))
    config = RuntimeConfig.from_file(path)
    runtime = build_runtime(config=config, eye_display=MemoryEyeDisplay())
    assert isinstance(runtime._vision_pipeline._expression_provider, AWSExpressionProvider)
    assert config.cloud_expression.cooldown_seconds == 45
    assert config.cloud_expression.region == "eu-west-1"
    assert RuntimeConfig().expression_provider == "local"
    for invalid in ('{"expression_provider":"other"}', '{"aws_secret_access_key":"placeholder"}',
                    '{"cloud_expression":{"access_key":"placeholder"}}'):
        path.write_text(invalid)
        with pytest.raises((ValueError, TypeError)):
            RuntimeConfig.from_file(path)


def test_sdk_uses_external_credentials_emotions_only_and_no_hidden_retries(fake_cv, monkeypatch):
    captured = {}
    def client(service, **kwargs):
        captured.update(service=service, **kwargs)
        return SimpleNamespace(detect_faces=lambda **kw: response())
    monkeypatch.setitem(sys.modules, "boto3", SimpleNamespace(client=client))
    monkeypatch.setitem(sys.modules, "botocore.config", SimpleNamespace(Config=lambda **kw: kw))
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    p = AWSExpressionProvider(CloudExpressionConfig())
    assert p._request_sync(Crop()).label == "happy"
    assert captured == {"service": "rekognition", "region_name": "eu-west-1", "config": {
        "connect_timeout": 3, "read_timeout": 5,
        "retries": {"total_max_attempts": 1, "mode": "standard"}}}


def test_cli_overrides_file_without_losing_cloud_policy(tmp_path, monkeypatch):
    from robot import main
    path = tmp_path / "config.json"
    document = RuntimeConfig().to_dict()
    document["expression"].update(provider="aws", enabled=True)
    document["expression"]["aws"]["cooldown_seconds"] = 45
    path.write_text(json.dumps(document))
    seen = []
    async def run(*, config, lifecycle=None, web_server=None):
        seen.append((config, lifecycle, web_server))
    monkeypatch.setattr(main, "async_main", run)
    monkeypatch.setattr(main.logging, "basicConfig", lambda **kwargs: None)
    monkeypatch.setattr(main.logging, "FileHandler", lambda *args, **kwargs: None)
    monkeypatch.setattr(sys, "argv", ["phos", "--config", str(path), "--aws-region", "eu-west-2"])
    main.main()
    assert seen[0][0].cloud_expression.region == "eu-west-2"
    assert seen[0][0].cloud_expression.cooldown_seconds == 45
    assert seen[0][0].expression_provider == "aws"


def test_cloud_semantics_reach_behavior_without_vendor_data(fake_cv):
    from robot.core import BehaviorEngine
    from robot.ui import FaceExpression

    async def exercise():
        clock, bus = Clock(), EventBus()
        p = provider(clock, client=SimpleNamespace(detect_faces=lambda **kw: response()))
        class Frame(Crop):
            shape = (480, 640, 3)
            def __getitem__(self, key):
                return Crop()
        class Camera:
            async def capture_frame(self):
                return Frame()
        class Detector:
            async def detect(self, frame):
                return [FaceRegion(100, 100, 80, 80)]
        engine = BehaviorEngine(bus, clock=clock)
        pipeline = VisionPipeline(Camera(), Detector(), p, ExpressionSmoother(maximum_gap_seconds=90), events=bus)
        payloads = []
        bus.subscribe(VISION_EXPRESSION_STABLE, lambda event: payloads.append(event.data))
        await engine.start()
        try:
            # Simulate continuous detections across three independent cloud samples.
            for index in range(247):
                clock.value = index / 2
                await pipeline.process_once(timestamp=clock.value)
                if p._task is not None:
                    await p._task
            assert p.requests == 3
            assert engine.face_state.expression is FaceExpression.HAPPY
            assert payloads[-1]["visual_expression"]["label"] == "happy"
            assert set(payloads[-1]["visual_expression"]) == {"label", "confidence", "observed_for_ms"}
            assert "Emotions" not in repr(payloads) and "HAPPY" not in repr(payloads)
        finally:
            await p.close()
            await engine.stop()
    asyncio.run(exercise())
