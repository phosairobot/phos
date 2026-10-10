import asyncio
import logging
from types import SimpleNamespace

from flask import Flask
import pytest

from robot.core import Event
from robot.core.attention import ATTENTION_CHANGED, AttentionKind, AttentionState
from robot.core.presence import PERSON_ENTERED, PRESENCE_CHANGED, PresenceKind, PresenceState
from robot.vision.provider import ObservedExpression
from robot.vision.pipeline import OBSERVED_EXPRESSION_CHANGED
from robot.core.runtime import RobotCore
from robot.core.behavior_engine import BehaviorEngine
from robot.services import PhosApplicationService, RemoteApplicationService
from robot.services import ApplicationError
from robot.web.api import create_api


class Runtime:
    def __init__(self):
        self.core = RobotCore()
        self._behavior_engine = BehaviorEngine(self.core.events)

    def sensor_status(self):
        return {"environmental": {"status": "available", "available": True},
                "ccs811": {"status": "warming_up"}}

    def apply_base_visual_source(self, config):
        self._behavior_engine.configure_base_visual_source(config.base_visual_source)


class _VoiceFuture:
    def __init__(self, result=None, error=None):
        self._result, self._error = result, error

    def result(self, timeout):
        if self._error:
            raise self._error
        return self._result


def test_voice_start_failure_logs_traceback_and_keeps_stable_503(monkeypatch, caplog):
    runtime = Runtime()
    runtime._loop = object()

    async def start_listening():
        return {"state": "listening"}

    runtime.start_listening = start_listening

    def failed_submit(coroutine, loop):
        coroutine.close()
        return _VoiceFuture(error=RuntimeError("microphone exploded"))

    monkeypatch.setattr("robot.services.application.asyncio.run_coroutine_threadsafe", failed_submit)
    app = Flask(__name__)
    app.register_blueprint(create_api(PhosApplicationService(runtime)))

    with caplog.at_level(logging.ERROR):
        response = app.test_client().post("/api/v1/voice/listen")

    assert response.status_code == 503
    assert response.json == {"error": {"code": "voice_unavailable",
                                        "message": "Voice session could not be started or stopped.",
                                        "details": {"reason": "RuntimeError"}}}
    assert "VOICE SESSION: failed operation=start exception_type=RuntimeError exception_message=microphone exploded" in caplog.text
    assert "Traceback" in caplog.text
    assert "microphone exploded" not in response.get_data(as_text=True)


def test_voice_start_success_still_returns_session_status(monkeypatch):
    runtime = Runtime()
    runtime._loop = object()

    async def start_listening():
        return {"state": "listening", "listening": True}

    runtime.start_listening = start_listening

    def successful_submit(coroutine, loop):
        coroutine.close()
        return _VoiceFuture(result={"state": "listening", "listening": True})

    monkeypatch.setattr("robot.services.application.asyncio.run_coroutine_threadsafe", successful_submit)
    app = Flask(__name__)
    app.register_blueprint(create_api(PhosApplicationService(runtime)))

    response = app.test_client().post("/api/v1/voice/listen")

    assert response.status_code == 200
    assert response.json == {"state": "listening", "listening": True}


def test_speak_api_validates_and_forwards_trimmed_text():
    class Service:
        def __init__(self): self.calls = []
        def speak(self, text): self.calls.append(text)

    service = Service()
    app = Flask(__name__)
    app.register_blueprint(create_api(service))
    client = app.test_client()

    response = client.post("/api/v1/speak", json={"text": "  Hello from PHOS.  "})

    assert response.status_code == 202
    assert response.json == {"status": "accepted"}
    assert service.calls == ["Hello from PHOS."]
    for payload in ({}, {"text": "  "}, {"text": 2}, {"text": "x" * 501}):
        rejected = client.post("/api/v1/speak", json=payload)
        assert rejected.status_code == 400
        assert rejected.json["error"]["code"] == "invalid_speech"
    assert client.post("/api/v1/speak", data="not-json", content_type="application/json").status_code == 400


def test_speak_api_preserves_stable_busy_unavailable_and_internal_errors():
    class Service:
        def __init__(self, error): self.error = error
        def speak(self, _text): raise self.error

    for error, status, code in (
        (ApplicationError("tts_busy", "PHOS is already speaking.", status=409), 409, "tts_busy"),
        (ApplicationError("tts_unavailable", "Text-to-speech is unavailable.", status=503), 503, "tts_unavailable"),
        (RuntimeError("/private/model-path"), 500, "internal_error"),
    ):
        app = Flask(__name__)
        app.register_blueprint(create_api(Service(error)))
        response = app.test_client().post("/api/v1/speak", json={"text": "hello"})
        assert response.status_code == status
        assert response.json["error"]["code"] == code
        assert "/private/model-path" not in response.get_data(as_text=True)


def test_application_speak_maps_runtime_busy_and_unavailable_errors(monkeypatch):
    from robot.voice import TTSBusyError, TTSConfigurationError, TTSProviderError
    runtime = Runtime()
    runtime._loop = object()

    async def accept_speech(_text):
        return None
    runtime.accept_speech = accept_speech

    for error, status, code in ((TTSBusyError(), 409, "tts_busy"),
                                (TTSConfigurationError(), 503, "tts_unavailable"),
                                (TTSProviderError("provider_plan_required"), 503, "tts_unavailable")):
        def submit(coroutine, _loop, error=error):
            coroutine.close()
            return _VoiceFuture(error=error)
        monkeypatch.setattr("robot.services.application.asyncio.run_coroutine_threadsafe", submit)
        with pytest.raises(ApplicationError) as raised:
            PhosApplicationService(runtime).speak(" hello ")
        assert raised.value.status == status and raised.value.code == code


def test_remote_speak_uses_lifecycle_ipc_and_main_dispatch_reaches_application_service():
    class Lifecycle:
        def __init__(self): self.calls = []
        def execute(self, operation, payload=None):
            self.calls.append((operation, payload))
            return {"ok": True, "result": None}
    lifecycle = Lifecycle()
    RemoteApplicationService(lifecycle).speak("Hello")
    assert lifecycle.calls == [("application.speak", {"text": "Hello"})]

    from robot.lifecycle import LifecycleService
    received = []
    service = object.__new__(LifecycleService)
    service._application_service = SimpleNamespace(speak=lambda text: received.append(text))
    assert service._execute_application({"operation": "application.speak", "payload": {"text": "Hello"}}) == {"ok": True, "result": None}
    assert received == ["Hello"]


def test_versioned_status_and_stable_error_document():
    app = Flask(__name__)
    app.register_blueprint(create_api(PhosApplicationService(Runtime())))
    client = app.test_client()
    snapshot = client.get("/api/v1/status")
    assert snapshot.status_code == 200
    # A dashboard can initialize from one semantic status document; sensor
    # adapters remain behind the application service.
    assert {"robot", "visual", "environment", "motion", "sensors", "health", "overlay", "presence", "attention"} <= set(snapshot.json)
    capabilities = client.get("/api/v1/capabilities").json
    assert capabilities["commands"]["set_visual_source"]["allowed_values"] == ["manual", "environment", "state"]
    assert capabilities["commands"]["set_robot_state"] == {"endpoint": "/api/v1/state", "method": "POST",
                                                               "field": "state", "allowed_values": ["idle", "listening", "thinking", "speaking", "sleeping"],
                                                               "transitions": {"idle": ["listening", "sleeping"], "listening": ["idle", "thinking", "sleeping"],
                                                                               "thinking": ["idle", "speaking"], "speaking": ["idle", "listening"], "sleeping": ["idle"]}}
    response = client.post("/api/v1/visual-source", json={"source": "GPIO18"})
    assert response.status_code == 400
    assert response.json == {"error": {"code": "invalid_visual_source", "message": "Unsupported visual source.",
                                        "details": {"source": "GPIO18"}}}
    overlay = client.post("/api/v1/overlay", json={"temperature": "warm", "air_quality": "warning", "duration_ms": 3000})
    assert overlay.status_code == 200
    assert overlay.json["resolved"] == {"temperature": "warm", "air_quality": "warning"}
    assert client.delete("/api/v1/overlay").json["override"]["active"] is False


def test_runtime_non_finite_sensor_values_become_json_null():
    runtime = Runtime()
    runtime.sensor_status = lambda: {"environmental": {
        "status": "available", "available": True,
        "measurements": {"temperature_c": float("-inf"), "pressure_hpa": float("nan")},
    }}
    app = Flask(__name__)
    app.register_blueprint(create_api(PhosApplicationService(runtime)))
    response = app.test_client().get("/api/v1/status")
    assert response.status_code == 200
    assert response.json["environment"]["measurements"] == {"pressure_hpa": None, "temperature_c": None}


def test_observed_expression_endpoint_is_read_only_and_unavailable_is_not_a_failure():
    runtime = Runtime()
    app = Flask(__name__)
    app.register_blueprint(create_api(PhosApplicationService(runtime)))
    client = app.test_client()
    unavailable = client.get("/api/v1/observed-expression")
    assert unavailable.status_code == 200
    assert unavailable.json["available"] is False and unavailable.json["label"] is None
    runtime._vision_pipeline = SimpleNamespace(observed_expression=ObservedExpression(
        True, "happy", .82, "local", "emotion-ferplus-8.onnx", 12.0))
    observed = client.get("/api/v1/observed-expression")
    assert observed.status_code == 200
    assert observed.json["label"] == "happy" and observed.json["confidence"] == .82
    snapshot = client.get("/api/v1/status")
    assert snapshot.status_code == 200
    assert snapshot.json["observed_expression"] == observed.json
    assert client.post("/api/v1/observed-expression", json={}).status_code == 405


def test_presence_and_attention_read_endpoints_use_live_runtime_state():
    runtime = Runtime()
    presence = SimpleNamespace(state=PresenceState())
    attention = SimpleNamespace(state=AttentionState())
    runtime._presence_interpreter = presence
    runtime._attention_manager = attention
    app = Flask(__name__)
    app.register_blueprint(create_api(PhosApplicationService(runtime)))
    client = app.test_client()
    assert client.get("/api/v1/presence").status_code == 200
    assert client.get("/api/v1/presence").json == {
        "state": "no_one", "people_count": 0, "primary_candidate_id": None,
        "visible_since": None, "last_seen": None, "confidence": None,
    }
    assert client.get("/api/v1/attention").json == {"state": "idle", "target": {
        "id": None, "x": None, "y": None, "confidence": None}, "acquired_at": None, "last_seen": None}
    presence.state = PresenceState(PresenceKind.PERSON_PRESENT, 1, "face-1", 1.0, 2.0, .82)
    attention.state = AttentionState(AttentionKind.TRACKING, "face-1", .2, -.1, .82, 1.0, 2.0)
    assert client.get("/api/v1/presence").json["state"] == "person_present"
    assert client.get("/api/v1/attention").json == {
        "state": "tracking", "target": {"id": "face-1", "x": .2, "y": -.1, "confidence": .82},
        "acquired_at": 1.0, "last_seen": 2.0,
    }


def test_presence_and_attention_read_endpoints_report_actual_remote_unavailability():
    class UnavailableLifecycle:
        def execute(self, *_):
            return {"ok": False, "error": "Application service is unavailable.", "status": 503}

    app = Flask(__name__)
    app.register_blueprint(create_api(RemoteApplicationService(UnavailableLifecycle())))
    client = app.test_client()
    for endpoint in ("/api/v1/presence", "/api/v1/attention"):
        assert client.get(endpoint).status_code == 503


def test_presence_and_attention_core_events_are_forwarded_as_semantic_application_events():
    runtime = Runtime()
    service = PhosApplicationService(runtime)
    received = []
    service.subscribe(received.append)

    asyncio.run(runtime.core.events.publish(Event(PRESENCE_CHANGED, {
        "state": "person_present", "people_count": 1, "primary_candidate_id": "face-1",
    })))
    asyncio.run(runtime.core.events.publish(Event(PERSON_ENTERED, {
        "state": "person_present", "people_count": 1, "primary_candidate_id": "face-1",
    })))
    asyncio.run(runtime.core.events.publish(Event(ATTENTION_CHANGED, {
        "state": "tracking", "target": {"id": "face-1", "x": 0.2, "y": -0.1, "confidence": 0.82},
    })))
    asyncio.run(runtime.core.events.publish(Event(OBSERVED_EXPRESSION_CHANGED, {
        "available": True, "label": "happy", "confidence": .82, "provider": "local",
    })))

    assert [(event["type"], event["payload"]) for event in received] == [
        ("presence_changed", {"state": "person_present", "people_count": 1, "primary_candidate_id": "face-1"}),
        ("person_entered", {"state": "person_present", "people_count": 1, "primary_candidate_id": "face-1"}),
        ("attention_changed", {"state": "tracking", "target": {"id": "face-1", "x": 0.2, "y": -0.1, "confidence": 0.82}}),
        ("observed_expression_changed", {"available": True, "label": "happy", "confidence": .82, "provider": "local"}),
    ]
    service.close()


def test_semantic_command_routes_forward_the_capability_field_to_application_service():
    class RecordingService:
        def __init__(self): self.calls = []
        def set_state(self, value): self.calls.append(("state", value)); return {"state": value}
        def set_expression(self, value): self.calls.append(("expression", value)); return {"expression": value}
        def set_visual_source(self, value): self.calls.append(("source", value)); return {"source": value}

    service = RecordingService()
    app = Flask(__name__)
    app.register_blueprint(create_api(service))
    client = app.test_client()
    assert client.post("/api/v1/state", json={"state": "listening"}).json == {"state": "listening"}
    assert client.post("/api/v1/expression", json={"expression": "curious"}).json == {"expression": "curious"}
    assert client.post("/api/v1/visual-source", json={"source": "manual"}).json == {"source": "manual"}
    assert service.calls == [("state", "listening"), ("expression", "curious"), ("source", "manual")]


def test_local_openapi_and_documentation_routes():
    from pathlib import Path
    from robot.web.app import create_app
    runtime = Runtime()
    app = create_app(Path("config/phos.json"), application_service=PhosApplicationService(runtime))
    # Documentation is still protected by the same local-admin authentication
    # boundary in a complete app; route registration is verified directly.
    routes = {rule.rule for rule in app.url_map.iter_rules()}
    assert {"/openapi.json", "/docs"} <= routes
    assert "/redoc" not in routes
    with app.test_request_context("/docs"):
        response = app.process_response(app.view_functions["api_docs"]())
    document = response.get_data(as_text=True)
    assert "https://" not in document and "http://" not in document
    assert "/static/swagger-ui/swagger-ui-bundle.js" in document
    assert "/static/swagger-ui/swagger-ui.css" in document
    assert response.headers["Content-Security-Policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:")
    assert app.test_client().get("/static/swagger-ui/swagger-ui-bundle.js").status_code == 200
