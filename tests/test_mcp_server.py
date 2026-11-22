import asyncio
import base64
from dataclasses import asdict
from datetime import datetime, timezone
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

pytest.importorskip("mcp")

from robot.mcp.server import (
    MCP_RESOURCE_URIS,
    MCP_TOOL_NAMES,
    PHYSICAL_ASSESSMENT_TOOL_DESCRIPTION,
    PHYSICAL_CONTEXT_TOOL_DESCRIPTION,
    McpSemanticAdapter,
    McpSemanticEventBridge,
    McpToolError,
    _SnapshotToolRateLimiter,
    create_mcp_server,
)
from robot.core.behavior_engine import BehaviorEngine
from robot.core.agent_visual_state import AgentVisualStateLeaseManager
from robot.core.runtime import RobotCore
from robot.vision.pipeline import CameraSnapshot
from robot.config import ConfigRepository, RuntimeConfig, load_document
from robot.secrets import SecretsService
from robot.services import (AgentAccess, AgentApplicationService, AgentCredentialService, AgentIdentity,
                            AgentManagementService, AgentRegistry, AgentRegistryProvider,
                            BearerAgentAuthenticator, ApplicationError, AgentProfile)
from robot.services.application import PhosApplicationService
from robot.services.semantic_events import SemanticEventPublisher


class ApplicationSpy:
    def __init__(self):
        self.calls = []

    def _call(self, name, *arguments):
        self.calls.append((name, *arguments))
        return {"operation": name, "arguments": list(arguments)}

    def status(self):
        self._call("status")
        return {"robot": {"running": True, "state": "idle"},
                "environment": {"status": "available", "available": True},
                "motion": {"status": "available", "available": True},
                "sensors": {"ens160": {"status": "available", "available": True}},
                "observed_expression": {"available": True},
                "voice": {"enabled": True, "listening": False, "stt_available": False, "last_error": None},
                "speech": {"tts": {"available": True}}, "semantic_led": {"available": True}}
    def get_physical_context(self):
        self._call("get_physical_context")
        return _physical_context_document()
    def get_physical_assessment(self):
        self._call("get_physical_assessment")
        return _physical_assessment_document()
    def presence(self): return self._call("presence")
    def attention(self): return self._call("attention")
    def observed_expression(self): return self._call("observed_expression")
    async def visible_objects(self):
        self._call("visible_objects")
        return {"captured_at": "2026-01-02T03:04:05Z", "age_ms": 25, "freshness": "fresh",
                "objects": [{"label": "car", "confidence": .9}], "truncated": False}
    def query_measurements(self, metric, start=None, end=None, limit=360):
        self._call("query_measurements", metric, start, end, limit)
        return {"metric": metric, "samples": [], "count": 0, "summary": None}
    def list_physical_transitions(self, event_type=None, limit=100):
        self._call("list_physical_transitions", event_type, limit)
        return {"transitions": [], "count": 0}
    def get_camera_snapshot(self):
        self._call("get_camera_snapshot")
        return b"\xff\xd8fake-jpeg\xff\xd9"
    def get_camera_snapshot_with_metadata(self):
        self._call("get_camera_snapshot_with_metadata")
        return CameraSnapshot(b"\xff\xd8fake-jpeg\xff\xd9", datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
                              250, "fresh")
    async def capture_camera_snapshot_with_metadata(self):
        self._call("capture_camera_snapshot_with_metadata")
        return CameraSnapshot(b"\xff\xd8fresh-jpeg\xff\xd9", datetime(2026, 1, 2, 3, 4, 6, tzinfo=timezone.utc),
                              0, "fresh")
    def set_expression(self, expression): return self._call("set_expression", expression)
    def request_agent_visual_state(self, agent_id, state, duration_ms=None):
        self._call("request_agent_visual_state", agent_id, state, duration_ms)
        return {"accepted": True, "requested_state": state,
                "effective_state": "thinking" if state == "thinking" else "idle",
                "lease_active": state == "thinking",
                "remaining_expiry_ms": duration_ms if state == "thinking" else None,
                "suppressed_by": None}
    async def wait_for_agent_visual_appearance(self): return None
    def speak(self, text): return self._call("speak", text)
    def set_state(self, state): return self._call("set_state", state)
    def signal_user(self, signal): return self._call("signal_user", signal)
    async def speak_async(self, text): return self.speak(text)
    async def set_state_async(self, state): return self.set_state(state)
    async def signal_user_async(self, signal): return self.signal_user(signal)


def _physical_context_document():
    return {
        "robot": {"state": "idle"},
        "environment": {"status": "available", "available": True, "temperature_c": 21.5,
                        "pressure_hpa": 1012.0, "humidity_percent": 45.0,
                        "last_update": "2026-01-02T03:04:05Z", "age_seconds": 1.0,
                        "condition": "normal", "reason": None},
        "air_quality": {"status": "available", "available": True, "eco2_ppm": 500.0,
                        "tvoc_ppb": 12.0, "aqi_uba": 2.0, "aqi_scale": "uba_1_to_5",
                        "humidity_percent": None, "aht21_temperature_c": None,
                        "temperature_provenance": "aht21", "last_update": "2026-01-02T03:04:05Z",
                        "age_seconds": 1.0, "condition": "normal"},
        "motion": {"status": "available", "available": True, "motion_state": "still",
                   "motion_intensity": None, "tilt_direction": None, "last_motion_event": None,
                   "last_update": "2026-01-02T03:04:05Z", "age_seconds": 1.0},
        "presence": {"state": "no_one", "people_count": 0, "confidence": None},
        "attention": {"state": "idle", "target": {"x": None, "y": None, "confidence": None}},
        "observed_expression": {"available": False, "label": None, "confidence": None,
                                "unavailable_reason": "unavailable"},
    }


def _physical_assessment_document():
    def metric(name):
        current = {"key": "environmental", "status": "available", "available": True,
                   "last_update": "2026-01-02T03:04:05Z", "age_seconds": 1.0}
        return {"metric": name, "unit": None, "source": "environment",
                "requested_window": {"start": "2026-01-01T21:04:05Z", "end": "2026-01-02T03:04:05Z",
                                     "retention_seconds": 21600}, "current_source": current,
                "evidence": {"sample_count": 0, "first_observed_at": None, "last_observed_at": None,
                             "coverage_seconds": None, "largest_gap_seconds": None, "continuity": "no_observed_discontinuity",
                             "availability_discontinuity": False},
                "recent_continuous_segment": {"start": None, "end": None, "recovery_boundary": None,
                                              "sample_count": 0, "coverage_seconds": None, "largest_gap_seconds": None,
                                              "continuity": "no_observed_discontinuity", "current_source": current,
                                              "outcome": "insufficient_data", "reason": "no_post_recovery_samples"},
                "outcome": "unknown", "reason": "no_retained_samples"}
    return {"assessment_version": "pci-01a", "timestamp_semantics": "phos_receipt_time",
            "metrics": {name: metric(name) for name in ("temperature_c", "pressure_hpa", "humidity_percent",
                                                           "eco2_ppm", "tvoc_ppb", "aqi_uba")}}


def _adapter(application=None, profile=AgentProfile.STANDARD):
    application = application or ApplicationSpy()
    return McpSemanticAdapter(AgentApplicationService(application, AgentAccess.for_profile(profile))), application


def _identity_adapter(application=None, profile=AgentProfile.STANDARD, agent_id="agent-a"):
    application = application or ApplicationSpy()
    identity = AgentIdentity(agent_id, profile, agent_id)
    return McpSemanticAdapter(AgentApplicationService(application, identity)), application


def test_history_tools_use_agent_permission_and_preserve_bounded_arguments():
    adapter, application = _adapter()
    assert adapter.query_measurements("temperature_c", "2026-01-01T00:00:00Z", None, 10)["metric"] == "temperature_c"
    assert adapter.list_physical_transitions("presence_changed", 10)["transitions"] == []
    assert adapter.physical_assessment()["assessment_version"] == "pci-01a"
    denied, _ = _adapter(profile=AgentProfile.OBSERVER)
    with pytest.raises(McpToolError, match="read_physical_history"):
        denied.query_measurements("temperature_c")
    with pytest.raises(McpToolError, match="read_physical_history"):
        denied.physical_assessment()


def test_server_registers_exactly_the_stage_one_semantic_tools_without_runtime_creation():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)
    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == MCP_TOOL_NAMES
    assert len(MCP_TOOL_NAMES) == 17
    source = inspect.getsource(__import__("robot.mcp.server", fromlist=["server"]))
    assert all(forbidden not in source for forbidden in (
        "robot.hardware", "serial_controller", "LEDRingController", "robot.ui.renderer", "robot.vision.provider",
    ))
    expression = next(tool for tool in tools if tool.name == "phos_set_expression")
    assert expression.input_schema["properties"]["expression"]["enum"] == [
        "neutral", "happy", "curious", "surprised", "sad", "angry", "focused", "tired", "confused",
        "scared", "skeptical",
    ]
    visible = next(tool for tool in tools if tool.name == "phos_get_visible_objects")
    assert visible.input_schema["properties"] == {}
    context = next(tool for tool in tools if tool.name == "phos_get_physical_context")
    assessment = next(tool for tool in tools if tool.name == "phos_get_physical_assessment")
    assert context.description == PHYSICAL_CONTEXT_TOOL_DESCRIPTION
    assert assessment.description == PHYSICAL_ASSESSMENT_TOOL_DESCRIPTION
    assert context.input_schema["properties"] == assessment.input_schema["properties"] == {}
    assert context.output_schema["type"] == assessment.output_schema["type"] == "object"
    assert assessment.annotations.read_only_hint is True
    assert assessment.annotations.destructive_hint is False
    assert assessment.annotations.idempotent_hint is True
    assert assessment.annotations.open_world_hint is False
    visual_state = next(tool for tool in tools if tool.name == "phos_set_state")
    assert visual_state.input_schema["required"] == ["state"]
    assert visual_state.input_schema["properties"]["state"]["enum"] == ["thinking", "idle"]
    duration = visual_state.input_schema["properties"]["duration_ms"]
    assert duration["anyOf"][0]["type"] == "integer"
    assert visual_state.input_schema["additionalProperties"] is False


def test_tool_discovery_metadata_describes_open_history_contracts_and_real_side_effects():
    """Keep tools/list useful without narrowing any established input contract."""
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert set(tools) == MCP_TOOL_NAMES
    assert all(tool.description and len(tool.description) > 40 for tool in tools.values())
    expected_annotations = {
        "phos_get_status": (True, True),
        "phos_get_physical_context": (True, True),
        "phos_get_physical_assessment": (True, True),
        "phos_get_presence": (True, True),
        "phos_get_attention": (True, True),
        "phos_get_observed_expression": (True, True),
        "phos_get_visible_objects": (True, False),
        "phos_get_camera_snapshot": (True, True),
        "phos_capture_camera_snapshot": (True, False),
        "phos_set_expression": (False, True),
        "phos_set_state": (False, False),
        "phos_speak": (False, False),
        "phos_sleep": (False, True),
        "phos_wake": (False, True),
        "phos_signal_user": (False, False),
        "phos_query_measurements": (True, True),
        "phos_list_physical_transitions": (True, True),
    }
    for name, (read_only, idempotent) in expected_annotations.items():
        annotation = tools[name].annotations
        assert annotation.read_only_hint is read_only
        assert annotation.destructive_hint is False
        assert annotation.idempotent_hint is idempotent
        assert annotation.open_world_hint is False

    history = tools["phos_query_measurements"].input_schema["properties"]
    assert "enum" not in history["metric"]
    assert all(metric in history["metric"]["description"] for metric in (
        "temperature_c", "pressure_hpa", "humidity_percent", "eco2_ppm", "tvoc_ppb", "aqi_uba",
    ))
    assert "RFC 3339" in history["start"]["anyOf"][0]["description"]
    assert history["limit"]["default"] == 360
    assert history["limit"]["minimum"] == 1
    assert history["limit"]["maximum"] == 360
    transitions = tools["phos_list_physical_transitions"].input_schema["properties"]
    assert "enum" not in transitions["event_type"]["anyOf"][0]
    assert transitions["limit"]["default"] == 100
    assert transitions["limit"]["minimum"] == 1
    assert transitions["limit"]["maximum"] == 360
    state_duration = tools["phos_set_state"].input_schema["properties"]["duration_ms"]
    assert "thinking" in state_duration["anyOf"][0]["description"]
    assert "idle" in state_duration["anyOf"][0]["description"]


def test_tool_discovery_metadata_preserves_open_history_and_existing_control_calls():
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service)

    async def exercise():
        unknown_metric = await server.call_tool("phos_query_measurements", {"metric": "future_metric"})
        transition = await server.call_tool("phos_list_physical_transitions", {"event_type": "future_event"})
        expression = await server.call_tool("phos_set_expression", {"expression": "happy"})
        signal = await server.call_tool("phos_signal_user", {"signal": "success"})
        return unknown_metric, transition, expression, signal

    results = asyncio.run(exercise())
    assert all(not result.is_error for result in results)
    assert application.calls == [
        ("query_measurements", "future_metric", None, None, 360),
        ("list_physical_transitions", "future_event", 100),
        ("set_expression", "happy"),
        ("signal_user", "success"),
    ]


def test_selected_read_tools_publish_typed_output_contracts_only_for_their_existing_documents():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    status = tools["phos_get_status"].output_schema
    assert status["type"] == "object"
    assert status["required"] == ["running", "state", "availability", "degraded", "degraded_reasons"]
    availability = status["$defs"]["McpStatusAvailability"]
    assert availability["required"] == [
        "physical_context", "environment", "air_quality", "motion", "vision", "voice", "speech", "signals",
    ]
    assert status["properties"]["state"]["type"] == "string"

    presence = tools["phos_get_presence"].output_schema
    assert presence["required"] == ["state", "people_count", "confidence"]
    assert presence["properties"]["state"]["anyOf"][0]["enum"] == [
        "no_one", "person_present", "person_engaged",
    ]
    assert presence["properties"]["people_count"]["anyOf"][1]["type"] == "null"
    assert presence["properties"]["confidence"]["maximum"] == 1

    attention = tools["phos_get_attention"].output_schema
    assert attention["required"] == ["state", "target", "acquired_at", "last_seen"]
    assert attention["properties"]["state"]["enum"] == ["idle", "acquiring", "tracking", "lost"]
    target = attention["$defs"]["McpAttentionTarget"]
    assert target["required"] == ["x", "y", "confidence"]
    assert target["properties"]["x"]["minimum"] == -1
    assert target["properties"]["x"]["maximum"] == 1
    assert "monotonic-clock" in attention["properties"]["acquired_at"]["description"]

    for name, tool in tools.items():
        if name in {"phos_get_status", "phos_get_presence", "phos_get_attention",
                    "phos_get_physical_context", "phos_get_physical_assessment",
                    "phos_query_measurements", "phos_list_physical_transitions",
                    "phos_get_observed_expression", "phos_get_visible_objects",
                    "phos_set_expression", "phos_set_state", "phos_speak",
                    "phos_sleep", "phos_wake", "phos_signal_user"}:
            continue
        if name in {"phos_get_camera_snapshot", "phos_capture_camera_snapshot"}:
            assert tool.output_schema is None
        else:
            assert tool.output_schema["type"] == "object"
            assert tool.output_schema["additionalProperties"] is True


def test_physical_tools_publish_typed_output_contracts_and_validate_empty_retained_data():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    context = tools["phos_get_physical_context"].output_schema
    assert context["required"] == ["robot", "environment", "air_quality", "motion", "presence", "attention", "observed_expression"]
    assert "Current environmental semantic snapshot" in context["properties"]["environment"]["description"]
    assessment = tools["phos_get_physical_assessment"].output_schema
    assert assessment["required"] == ["assessment_version", "timestamp_semantics", "metrics"]
    assert set(assessment["$defs"]["McpPhysicalAssessmentMetrics"]["required"]) == {
        "temperature_c", "pressure_hpa", "humidity_percent", "eco2_ppm", "tvoc_ppb", "aqi_uba",
    }
    history = tools["phos_query_measurements"].output_schema
    assert history["required"] == ["metric", "samples", "count", "summary"]
    assert history["properties"]["summary"]["anyOf"][1]["type"] == "null"
    transitions = tools["phos_list_physical_transitions"].output_schema
    assert transitions["required"] == ["transitions", "count"]
    assert transitions["$defs"]["McpPhysicalTransition"]["properties"]["type"]["type"] == "string"

    async def call():
        results = []
        for name, arguments in (("phos_get_physical_context", {}), ("phos_get_physical_assessment", {}),
                                ("phos_query_measurements", {"metric": "unknown_metric"}),
                                ("phos_list_physical_transitions", {"event_type": "unknown_event"})):
            results.append((await server.call_tool(name, arguments)).structured_content)
        return tuple(results)

    context_result, assessment_result, history_result, transitions_result = asyncio.run(call())
    assert context_result == _physical_context_document()
    assert assessment_result == _physical_assessment_document()
    assert history_result == {"metric": "unknown_metric", "samples": [], "count": 0, "summary": None}
    assert transitions_result == {"transitions": [], "count": 0}


def test_perception_tools_publish_typed_output_contracts_without_changing_errors_or_camera_tools():
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    expression = tools["phos_get_observed_expression"].output_schema
    assert expression["required"] == ["available", "label", "confidence", "observed_at", "unavailable_reason"]
    assert expression["properties"]["available"]["anyOf"][1]["type"] == "null"
    assert expression["properties"]["confidence"]["maximum"] == 1
    assert "not proof" in expression["properties"]["label"]["description"]
    objects = tools["phos_get_visible_objects"].output_schema
    assert objects["required"] == ["captured_at", "age_ms", "freshness", "objects", "truncated"]
    assert objects["properties"]["freshness"]["enum"] == ["fresh", "stale"]
    object_item = objects["$defs"]["McpVisibleObject"]
    assert object_item["required"] == ["label", "confidence"]
    assert tools["phos_get_camera_snapshot"].output_schema is None
    assert tools["phos_capture_camera_snapshot"].output_schema is None

    async def call():
        expression_result = await server.call_tool("phos_get_observed_expression", {})
        objects_result = await server.call_tool("phos_get_visible_objects", {})
        return expression_result.structured_content, objects_result.structured_content

    expression_result, objects_result = asyncio.run(call())
    assert expression_result == {"available": None, "label": None, "confidence": None,
                                 "observed_at": None, "unavailable_reason": None}
    assert objects_result == {"captured_at": "2026-01-02T03:04:05Z", "age_ms": 25,
                              "freshness": "fresh", "objects": [{"label": "car", "confidence": .9}],
                              "truncated": False}
    assert application.calls == [("observed_expression",), ("visible_objects",)]


def test_embodiment_controls_publish_typed_acknowledgements_without_new_side_effects():
    adapter, _ = _identity_adapter()
    server = create_mcp_server(adapter._agent_service)
    tools = {tool.name: tool for tool in asyncio.run(server.list_tools())}

    assert tools["phos_set_expression"].output_schema["required"] == ["expression", "applied"]
    state_schema = tools["phos_set_state"].output_schema
    assert state_schema["required"] == ["accepted", "requested_state", "effective_state", "lease_active",
                                        "remaining_expiry_ms", "suppressed_by"]
    assert state_schema["properties"]["remaining_expiry_ms"]["anyOf"][1]["type"] == "null"
    assert tools["phos_speak"].output_schema["required"] == ["accepted"]
    assert tools["phos_sleep"].output_schema["required"] == ["state", "applied"]
    assert tools["phos_wake"].output_schema == tools["phos_sleep"].output_schema
    assert tools["phos_signal_user"].output_schema["required"] == ["intent", "applied"]
    assert tools["phos_get_camera_snapshot"].output_schema is None
    assert tools["phos_capture_camera_snapshot"].output_schema is None

    async def call():
        return (
            (await server.call_tool("phos_set_expression", {"expression": "happy"})).structured_content,
            (await server.call_tool("phos_set_state", {"state": "thinking", "duration_ms": 1000})).structured_content,
            (await server.call_tool("phos_speak", {"text": "Hello"})).structured_content,
            (await server.call_tool("phos_sleep", {})).structured_content,
            (await server.call_tool("phos_wake", {})).structured_content,
            (await server.call_tool("phos_signal_user", {"signal": "warning"})).structured_content,
        )

    expression, state, speech, sleep, wake, signal = asyncio.run(call())
    assert expression == {"expression": "happy", "applied": True}
    assert state["accepted"] is True and state["effective_state"] == "thinking"
    assert speech == {"accepted": True}
    assert sleep == {"state": "sleeping", "applied": True}
    assert wake == {"state": "idle", "applied": True}
    assert signal == {"intent": "warning", "applied": True}


def test_selected_read_tool_contracts_validate_normal_degraded_startup_and_empty_documents():
    class ContractApplication(ApplicationSpy):
        def __init__(self, *, status, presence, attention):
            super().__init__()
            self._status, self._presence, self._attention = status, presence, attention

        def status(self):
            self._call("status")
            return self._status

        def presence(self):
            self._call("presence")
            return self._presence

        def attention(self):
            self._call("attention")
            return self._attention

    normal_status = ApplicationSpy().status()
    degraded_status = {
        **normal_status,
        "robot": {"running": False, "state": "sleeping"},
        "environment": {"available": False, "status": "unavailable"},
        "motion": {"available": False, "status": "stale"},
        "sensors": {"ens160": {"available": False, "status": "starting"}},
    }
    variants = (
        (normal_status, {"state": "person_present", "people_count": 1, "confidence": .8},
         {"state": "tracking", "target": {"x": .25, "y": -.1, "confidence": .9},
          "acquired_at": 12.5, "last_seen": 13.0}),
        (degraded_status, {"state": "no_one", "people_count": 0, "confidence": None},
         {"state": "idle", "target": None, "acquired_at": None, "last_seen": None}),
        (normal_status, {}, {"state": "acquiring", "target": {"x": .1}, "acquired_at": None, "last_seen": 5.0}),
    )

    async def call(application):
        adapter, _ = _adapter(application)
        server = create_mcp_server(adapter._agent_service)
        results = []
        for name in ("phos_get_status", "phos_get_presence", "phos_get_attention"):
            results.append((await server.call_tool(name, {})).structured_content)
        return tuple(results)

    normal, degraded, startup = (asyncio.run(call(ContractApplication(status=status, presence=presence,
                                                                        attention=attention)))
                                 for status, presence, attention in variants)
    assert normal[1] == {"state": "person_present", "people_count": 1, "confidence": .8}
    assert normal[2]["target"] == {"x": .25, "y": -.1, "confidence": .9}
    assert degraded[0]["degraded"] is True and degraded[0]["state"] == "sleeping"
    assert degraded[1] == {"state": "no_one", "people_count": 0, "confidence": None}
    assert degraded[2] == {"state": "idle", "target": {"x": None, "y": None, "confidence": None},
                           "acquired_at": None, "last_seen": None}
    assert startup[1] == {"state": None, "people_count": None, "confidence": None}
    assert startup[2] == {"state": "acquiring", "target": {"x": .1, "y": None, "confidence": None},
                          "acquired_at": None, "last_seen": 5.0}


def test_physical_tool_discovery_and_calls_distinguish_current_and_temporal_questions():
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service)

    async def exercise():
        tools = {tool.name: tool for tool in await server.list_tools()}
        current = await server.call_tool("phos_get_physical_context", {})
        assessment = await server.call_tool("phos_get_physical_assessment", {})
        history = await server.call_tool("phos_query_measurements", {"metric": "temperature_c", "limit": 3})
        return tools, current, assessment, history

    tools, current, assessment, history = asyncio.run(exercise())
    # Current room conditions use context; interrupted-evidence questions use
    # assessment; change-over-time questions combine assessment and history.
    assert "currently perceives" in tools["phos_get_physical_context"].description
    assert "before interpreting changes over time" in tools["phos_get_physical_assessment"].description
    assert not current.is_error and not assessment.is_error and not history.is_error
    assert [call[0] for call in application.calls] == [
        "get_physical_context", "get_physical_assessment", "query_measurements",
    ]
    denied, _ = _adapter(profile=AgentProfile.OBSERVER)
    with pytest.raises(McpToolError, match="read_physical_history"):
        denied.get_physical_assessment()


def test_visual_state_tool_validates_arguments_and_forwards_only_authenticated_identity():
    adapter, application = _identity_adapter()
    server = create_mcp_server(adapter._agent_service)

    async def exercise():
        thinking = await server.call_tool("phos_set_state", {"state": "thinking", "duration_ms": 1000})
        idle = await server.call_tool("phos_set_state", {"state": "idle"})
        idle_with_duration = await server.call_tool("phos_set_state", {"state": "idle", "duration_ms": 10_000})
        idle_with_zero = await server.call_tool("phos_set_state", {"state": "idle", "duration_ms": 0})
        idle_with_out_of_range = await server.call_tool("phos_set_state", {"state": "idle", "duration_ms": 60_001})
        invalid = (
            {"state": "speaking", "duration_ms": 1000},
            {"state": "thinking"},
            {"state": "thinking", "duration_ms": 249}, {"state": "thinking", "duration_ms": 60_001},
            {"state": "thinking", "duration_ms": True}, {"state": "thinking", "duration_ms": "1000"},
            {"state": "idle", "duration_ms": True}, {"state": "idle", "duration_ms": "1000"},
            {"state": "thinking", "duration_ms": 1000, "agent_id": "agent-b"},
        )
        for arguments in invalid:
            with pytest.raises(Exception):
                await server.call_tool("phos_set_state", arguments)
        return thinking, idle, idle_with_duration, idle_with_zero, idle_with_out_of_range

    thinking, idle, idle_with_duration, idle_with_zero, idle_with_out_of_range = asyncio.run(exercise())
    assert thinking.structured_content == {"accepted": True, "requested_state": "thinking",
                                           "effective_state": "thinking", "lease_active": True,
                                           "remaining_expiry_ms": 1000, "suppressed_by": None}
    assert idle.structured_content == {"accepted": True, "requested_state": "idle",
                                       "effective_state": "idle", "lease_active": False,
                                       "remaining_expiry_ms": None, "suppressed_by": None}
    assert idle_with_duration.structured_content == idle.structured_content
    assert idle_with_zero.structured_content == idle.structured_content
    assert idle_with_out_of_range.structured_content == idle.structured_content
    assert application.calls == [
        ("request_agent_visual_state", "agent-a", "thinking", 1000),
        ("request_agent_visual_state", "agent-a", "idle", None),
        ("request_agent_visual_state", "agent-a", "idle", None),
        ("request_agent_visual_state", "agent-a", "idle", None),
        ("request_agent_visual_state", "agent-a", "idle", None),
    ]


def test_visual_state_adapter_enforces_permission_and_maps_application_errors():
    observer, application = _identity_adapter(profile=AgentProfile.OBSERVER)
    with pytest.raises(McpToolError, match="permission_denied: operation=set_visual_state required_permission=control_visual_state"):
        observer.set_visual_state("thinking", 1000)
    assert application.calls == []

    class Unavailable(ApplicationSpy):
        def request_agent_visual_state(self, *_args):
            raise ApplicationError("runtime_unavailable", "PHOS runtime is not running.", status=503)

    adapter, _application = _identity_adapter(Unavailable())
    with pytest.raises(McpToolError, match="service_unavailable: PHOS runtime is not running"):
        adapter.set_visual_state("thinking", 1000)


def test_visual_state_mcp_leases_are_identity_scoped_without_leaking_other_leases():
    class LeaseApplication:
        def __init__(self):
            self.leases = AgentVisualStateLeaseManager()

        async def wait_for_agent_visual_appearance(self):
            return None

        def request_agent_visual_state(self, agent_id, state, duration_ms=None):
            return asdict(self.leases.request(agent_id, state, duration_ms=duration_ms, actual_state="idle"))

    application = LeaseApplication()
    agent_a, _ = _identity_adapter(application, agent_id="agent-a")
    agent_b, _ = _identity_adapter(application, agent_id="agent-b")
    server_a = create_mcp_server(agent_a._agent_service)
    server_b = create_mcp_server(agent_b._agent_service)

    async def exercise():
        first = await server_a.call_tool("phos_set_state", {"state": "thinking", "duration_ms": 1000})
        second = await server_b.call_tool("phos_set_state", {"state": "thinking", "duration_ms": 1000})
        release_a = await server_a.call_tool("phos_set_state", {"state": "idle"})
        release_b = await server_b.call_tool("phos_set_state", {"state": "idle"})
        return first, second, release_a, release_b

    first, second, release_a, release_b = asyncio.run(exercise())
    assert first.structured_content["effective_state"] == "thinking"
    assert second.structured_content["effective_state"] == "thinking"
    # Agent A can release only its own lease, so B's newer lease remains active.
    assert release_a.structured_content == {
        "accepted": True, "requested_state": "idle", "effective_state": "thinking",
        "lease_active": False, "remaining_expiry_ms": None, "suppressed_by": None,
    }
    assert release_b.structured_content == {
        "accepted": True, "requested_state": "idle", "effective_state": "idle",
        "lease_active": False, "remaining_expiry_ms": None, "suppressed_by": None,
    }
    application.leases.shutdown()


def test_visible_objects_tool_uses_permission_checked_agent_result_without_image_content():
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service)
    result = asyncio.run(server.call_tool("phos_get_visible_objects", {})).structured_content
    assert result == {"captured_at": "2026-01-02T03:04:05Z", "age_ms": 25, "freshness": "fresh",
                      "objects": [{"label": "car", "confidence": .9}], "truncated": False}
    assert application.calls == [("visible_objects",)]
    assert "frame" not in result and set(result["objects"][0]) == {"label", "confidence"}


def test_visible_objects_denial_happens_before_application_work():
    adapter, application = _adapter(profile=AgentProfile.OBSERVER)
    # Observer has READ_PERCEPTION, so use snapshot-only explicit access instead.
    snapshot_only = McpSemanticAdapter(AgentApplicationService(application, AgentAccess({"read_camera_snapshot"})))
    with pytest.raises(McpToolError, match="read_perception"):
        asyncio.run(snapshot_only.visible_objects())
    assert application.calls == []


@pytest.mark.parametrize(("code", "message", "status", "category"), [
    ("object_awareness_disabled", "Object awareness is unavailable.", 503, "service_unavailable"),
    ("object_awareness_unavailable", "Object awareness is unavailable.", 503, "service_unavailable"),
    ("object_awareness_busy", "Object awareness is busy.", 429, "semantic_error"),
    ("object_awareness_timeout", "Object awareness timed out.", 504, "runtime_failure"),
])
def test_visible_object_runtime_errors_remain_tool_errors_not_success_documents(code, message, status, category):
    class Unavailable(ApplicationSpy):
        async def visible_objects(self):
            raise ApplicationError(code, message, status=status)

    adapter, _ = _adapter(Unavailable())
    with pytest.raises(McpToolError, match=f"{category}: {message}"):
        asyncio.run(adapter.visible_objects())


def test_server_lists_semantic_resources_and_reads_a_binary_camera_snapshot():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)

    async def read():
        resources = await server.list_resources()
        status = await server.read_resource("phos://status")
        physical_context = await server.read_resource("phos://physical-context")
        assessment = await server.read_resource("phos://physical-context/assessment")
        environment = await server.read_resource("phos://environment")
        snapshot = await server.read_resource("phos://vision/snapshot")
        return resources, status, physical_context, assessment, environment, snapshot

    resources, status, physical_context, assessment, environment, snapshot = asyncio.run(read())
    assert {resource.uri for resource in resources} == MCP_RESOURCE_URIS
    assert next(resource for resource in resources if resource.uri == "phos://vision/snapshot").mime_type == "image/jpeg"
    assert all(resource.mime_type == "application/json" for resource in resources if resource.uri != "phos://vision/snapshot")
    assert json.loads(status[0].content)["running"] is True
    assert json.loads(physical_context[0].content)["robot"]["state"] == "idle"
    assert json.loads(assessment[0].content)["assessment_version"] == "pci-01a"
    assert json.loads(environment[0].content)["status"] == "available"
    assert snapshot[0].content == b"\xff\xd8fake-jpeg\xff\xd9"
    assert snapshot[0].mime_type == "image/jpeg"


def test_camera_snapshot_tool_returns_native_image_content_without_changing_semantic_state():
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service)

    result = asyncio.run(server.call_tool("phos_get_camera_snapshot", {}))

    image, metadata = result.content
    assert image.type == "image" and image.mime_type == "image/jpeg"
    assert base64.b64decode(image.data) == b"\xff\xd8fake-jpeg\xff\xd9"
    assert metadata.type == "text" and "captured_at=2026-01-02T03:04:05Z" in metadata.text
    assert "age_ms=250" in metadata.text and "freshness=fresh" in metadata.text
    assert application.calls == [("get_camera_snapshot_with_metadata",)]


def test_camera_snapshot_tool_keeps_observer_camera_permission_denied():
    adapter, application = _adapter(profile=AgentProfile.OBSERVER)
    server = create_mcp_server(adapter._agent_service)

    async def call():
        with pytest.raises(Exception, match="permission_denied"):
            await server.call_tool("phos_get_camera_snapshot", {})

    asyncio.run(call())
    assert application.calls == []


def test_on_demand_camera_snapshot_tool_returns_native_fresh_image_content():
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service)

    result = asyncio.run(server.call_tool("phos_capture_camera_snapshot", {}))

    image, metadata = result.content
    assert image.type == "image" and base64.b64decode(image.data) == b"\xff\xd8fresh-jpeg\xff\xd9"
    assert "source=on_demand" in metadata.text and "age_ms=0" in metadata.text
    assert application.calls == [("capture_camera_snapshot_with_metadata",)]


def test_snapshot_tool_rate_limit_is_per_identity_and_uses_a_bounded_identity_cache():
    now = [100.0]
    limiter = _SnapshotToolRateLimiter(clock=lambda: now[0])

    limiter.require_allowed("standard-agent")
    with pytest.raises(McpToolError, match="rate_limited"):
        limiter.require_allowed("standard-agent")
    limiter.require_allowed("another-agent")
    now[0] += 1.0
    limiter.require_allowed("standard-agent")
    for index in range(64):
        limiter.require_allowed(f"agent-{index}")

    assert len(limiter._last_request) == 64


def test_camera_snapshot_tool_rejects_an_immediate_repeat_before_reading_the_provider():
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service)

    async def call_twice():
        await server.call_tool("phos_get_camera_snapshot", {})
        with pytest.raises(Exception):
            await server.call_tool("phos_get_camera_snapshot", {})

    asyncio.run(call_twice())
    assert application.calls == [("get_camera_snapshot_with_metadata",)]


def test_mcp_middleware_authenticates_once_for_each_tool_or_resource_request():
    class Authenticator:
        def __init__(self):
            self.contexts = []

        async def authenticate(self, context):
            self.contexts.append(context)
            return AgentIdentity("observer-agent", AgentProfile.OBSERVER)

    adapter, _ = _adapter()
    authenticator = Authenticator()
    server = create_mcp_server(adapter._agent_service, authenticator=authenticator)
    middleware = server.middleware[-1]

    async def exercise():
        resource_context, tool_context = object(), object()
        resource = await middleware(resource_context, lambda _: server.read_resource("phos://status"))
        tool = await middleware(tool_context, lambda _: server.call_tool("phos_get_status", {}))
        return resource, tool, resource_context, tool_context

    resource, tool, resource_context, tool_context = asyncio.run(exercise())

    assert json.loads(resource[0].content)["running"] is True
    assert tool.structured_content["running"] is True
    assert authenticator.contexts == [resource_context, tool_context]


def test_bearer_middleware_isolates_profiles_and_sanitizes_failed_authentication():
    class Secrets:
        def get_secret(self, reference): return {"mcp.standard.token": "super-secret-a", "mcp.observer.token": "super-secret-b"}.get(reference)
    registry = AgentRegistry("standard", (AgentIdentity("standard", "standard"), AgentIdentity("observer", "observer")),
                             {"standard": "mcp.standard.token", "observer": "mcp.observer.token"})
    adapter, application = _adapter()
    server = create_mcp_server(adapter._agent_service, authenticator=BearerAgentAuthenticator(registry, Secrets()))
    middleware = server.middleware[-1]
    def context(token): return type("Context", (), {"request": type("Request", (), {"headers": {"Authorization": token}})()})()
    async def exercise():
        allowed = await middleware(context("Bearer super-secret-a"), lambda _: server.call_tool("phos_sleep", {}))
        with pytest.raises(Exception, match="permission_denied"):
            await middleware(context("Bearer super-secret-b"), lambda _: server.call_tool("phos_sleep", {}))
        with pytest.raises(PermissionError, match="^authentication_failed$") as error:
            await middleware(context("Bearer invalid"), lambda _: server.call_tool("phos_sleep", {}))
        return allowed, str(error.value)
    allowed, error = asyncio.run(exercise())
    assert allowed.is_error is False
    assert error == "authentication_failed" and "super-secret" not in error
    assert application.calls == [("set_state", "sleeping")]


def test_mcp_activity_is_recorded_after_authorization_not_authentication_or_keepalive():
    standard = AgentIdentity("standard", "standard")
    observer = AgentIdentity("observer", "observer")

    class Authenticator:
        async def authenticate(self, context):
            token = context.request.headers.get("Authorization")
            if token == "Bearer standard": return standard
            if token == "Bearer observer": return observer
            raise PermissionError("authentication_failed")

    class Tracker:
        def __init__(self): self.identities = []
        def record(self, identity): self.identities.append(identity); return True

    tracker = Tracker()
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service, authenticator=Authenticator(), activity_tracker=tracker)
    middleware = server.middleware[-1]
    def context(token):
        return type("Context", (), {"request": type("Request", (), {"headers": {"Authorization": token}})()})()

    async def exercise():
        # Authentication alone (the transport's initialization path) has no
        # semantic adapter call and therefore does not renew a lease.
        await Authenticator().authenticate(context("Bearer standard"))
        await middleware(context("Bearer standard"), lambda _: server.call_tool("phos_get_status", {}))
        # A state-only visual lease remains accepted Agent activity so a
        # follow-agent installation can activate this Agent's assigned skin.
        await middleware(context("Bearer standard"), lambda _: server.call_tool(
            "phos_set_state", {"state": "thinking", "duration_ms": 1000}))
        with pytest.raises(Exception, match="permission_denied"):
            await middleware(context("Bearer observer"), lambda _: server.call_tool("phos_sleep", {}))
        with pytest.raises(PermissionError, match="authentication_failed"):
            await middleware(context("Bearer invalid"), lambda _: server.call_tool("phos_get_status", {}))

    asyncio.run(exercise())
    assert tracker.identities == [standard, standard]


def test_visual_state_tool_records_anonymous_activity_through_existing_fallback_policy():
    anonymous = AgentIdentity("anonymous-mcp", "standard", "Anonymous MCP")

    class Authenticator:
        async def authenticate(self, _context): return anonymous
        def is_anonymous_identity(self, identity): return identity is anonymous

    class Tracker:
        def __init__(self): self.anonymous = 0
        def record_anonymous_mcp(self): self.anonymous += 1; return True

    tracker = Tracker()
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service, authenticator=Authenticator(), activity_tracker=tracker)
    middleware = server.middleware[-1]
    context = type("Context", (), {"request": type("Request", (), {"headers": {}})()})()

    async def exercise():
        await middleware(context, lambda _: server.call_tool(
            "phos_set_state", {"state": "thinking", "duration_ms": 1000}))
        await middleware(context, lambda _: server.call_tool("phos_get_status", {}))

    asyncio.run(exercise())
    # State-only and generic accepted anonymous work share the existing
    # low-priority fallback policy; eligibility is decided by configuration,
    # never by MCP arguments.
    assert tracker.anonymous == 2


def test_revoked_bearer_cannot_renew_mcp_activity(tmp_path):
    registry = AgentRegistry("standard", (AgentIdentity("standard", "standard"),),
                             {"standard": "mcp.standard.token"})
    secrets = SecretsService(tmp_path / ".secrets")
    issued = AgentCredentialService(registry, secrets).create("standard")

    class Tracker:
        def __init__(self): self.identities = []
        def record(self, identity): self.identities.append(identity); return True

    tracker = Tracker()
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service,
                               authenticator=BearerAgentAuthenticator(registry, secrets),
                               activity_tracker=tracker)
    middleware = server.middleware[-1]
    context = lambda: type("Context", (), {"request": type("Request", (), {
        "headers": {"Authorization": f"Bearer {issued.token}"}})()})()

    async def exercise():
        await middleware(context(), lambda _: server.call_tool("phos_get_status", {}))
        AgentCredentialService(registry, secrets).revoke("standard")
        with pytest.raises(PermissionError, match="authentication_failed"):
            await middleware(context(), lambda _: server.call_tool("phos_get_status", {}))

    asyncio.run(exercise())
    assert [identity.id for identity in tracker.identities] == ["standard"]


def test_streamable_http_bearer_authentication_uses_real_asgi_request_path(tmp_path, caplog):
    from mcp.client import ClientSession
    from mcp.client.streamable_http import httpx2 as httpx, streamable_http_client
    from mcp.shared.exceptions import MCPError

    document = load_document()
    document["agents"] = {"default_agent": "local-agent", "entries": [
        {"id": "local-agent", "display_name": "Local MCP agent", "permission_profile": "standard"},
        {"id": "stage3-standard", "display_name": "Stage 3 Standard Agent", "permission_profile": "standard",
         "credential": {"type": "bearer", "secret_ref": "mcp.stage3_standard.token"}},
        {"id": "stage3-observer", "display_name": "Stage 3 Observer Agent", "permission_profile": "observer",
         "credential": {"type": "bearer", "secret_ref": "mcp.stage3_observer.token"}},
    ]}
    config_path = tmp_path / "phos.yaml"
    config_path.write_text(yaml.safe_dump(document, default_flow_style=False, sort_keys=False))
    config = RuntimeConfig.from_file(config_path)
    registry = config.agent_registry
    store = SecretsService(config.resolve_path(Path(".")) / ".phos-secrets")
    standard_token = "STAGE3_TOKEN_MUST_NOT_LEAK_91A7"
    observer_token = "HTTP_OBSERVER_TOKEN_MUST_NOT_LEAK_4C2B"
    store.set_secret("mcp.stage3_standard.token", standard_token)
    store.set_secret("mcp.stage3_observer.token", observer_token)
    adapter, application = _adapter()
    authenticator = BearerAgentAuthenticator(registry, store)
    seen_request = {}
    original_authenticate = authenticator.authenticate

    async def authenticate(context):
        request = context.request
        headers = request.headers
        seen_request.update(request_type=type(request).__name__, headers_type=type(headers).__name__,
                            authorization_present=isinstance(headers.get("authorization"), str))
        return await original_authenticate(context)

    authenticator.authenticate = authenticate
    server = create_mcp_server(adapter._agent_service, authenticator=authenticator)
    app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp")
    async def session_for(authorization):
        headers = {} if authorization is None else {"Authorization": authorization}
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001", headers=headers)
        async with streamable_http_client("http://127.0.0.1:8001/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    async def rejected(authorization):
        try:
            async for _ in session_for(authorization):
                pass
        except BaseExceptionGroup as error:
            return repr(error)
        raise AssertionError("unauthenticated MCP initialization unexpectedly succeeded")

    async def exercise():
        async with server.session_manager.run():
            async for standard in session_for(f"Bearer {standard_token}"):
                allowed_first = await standard.call_tool("phos_sleep")
                snapshot = await standard.read_resource("phos://vision/snapshot")
                async for observer in session_for(f"Bearer {observer_token}"):
                    denied = await observer.call_tool("phos_sleep")
                    with pytest.raises(MCPError) as denied_camera:
                        await observer.read_resource("phos://vision/snapshot")
                allowed_second = await standard.call_tool("phos_sleep")
            failures = [await rejected(value) for value in (None, "Bearer invalid", "Bearer", "Basic invalid")]
            return allowed_first, allowed_second, denied, snapshot, repr(denied_camera.value), failures
    allowed_first, allowed_second, denied, snapshot, denied_camera, failures = asyncio.run(exercise())
    assert registry.default_agent == "local-agent"
    assert registry.credential_refs == {"stage3-standard": "mcp.stage3_standard.token",
                                        "stage3-observer": "mcp.stage3_observer.token"}
    assert allowed_first.is_error is False and allowed_second.is_error is False
    assert denied.is_error is True and "permission_denied" in denied.content[0].text
    assert snapshot.contents[0].mime_type == "image/jpeg" and snapshot.contents[0].blob
    assert "permission_denied" in denied_camera
    assert all("authentication_failed" in failure and standard_token not in failure and observer_token not in failure
               for failure in failures)
    assert seen_request == {"request_type": "Request", "headers_type": "Headers", "authorization_present": True}
    assert standard_token not in caplog.text and observer_token not in caplog.text
    assert application.calls == [("set_state", "sleeping"), ("get_camera_snapshot",), ("set_state", "sleeping")]


def test_streamable_http_camera_snapshot_uses_existing_active_pipeline_and_real_jpeg(tmp_path):
    """Exercise the MCP resource path without creating a second camera/runtime.

    The active pipeline fixture owns one already-encoded, standards-conformant
    JPEG.  The MCP SDK client reads it over the real Streamable HTTP ASGI path;
    no request may construct another runtime or camera provider.
    """
    from mcp.client import ClientSession
    from mcp.client.streamable_http import httpx2 as httpx, streamable_http_client
    from mcp.shared.exceptions import MCPError

    # A baseline 1×1 JPEG.  Keeping this fixture independent of cv2 is
    # intentional: standard desktop test environments need not install the
    # Raspberry Pi OS OpenCV package used by production VisionPipeline.
    jpeg = bytes.fromhex(
        "ffd8ffe000104a46494600010100000100010000ffdb004300"
        + "08" * 64
        + "ffc0000b080001000101011100ffc40014000100000000000000000000000000000000"
        + "ffda0008010100003f00d2cf20ffd9"
    )

    class ActivePipeline:
        def __init__(self):
            self.snapshot = jpeg
            self.calls = 0

        def camera_snapshot_jpeg(self):
            self.calls += 1
            if self.snapshot is None:
                raise RuntimeError("no_frame")
            return self.snapshot

        def camera_snapshot(self):
            return CameraSnapshot(self.camera_snapshot_jpeg(), datetime(2026, 1, 2, tzinfo=timezone.utc), 0, "fresh")

    document = load_document()
    document["agents"] = {"default_agent": "local-agent", "entries": [
        {"id": "local-agent", "display_name": "Local MCP agent", "permission_profile": "standard"},
        {"id": "camera-standard", "display_name": "Camera standard", "permission_profile": "standard",
         "credential": {"type": "bearer", "secret_ref": "mcp.camera.standard"}},
        {"id": "camera-observer", "display_name": "Camera observer", "permission_profile": "observer",
         "credential": {"type": "bearer", "secret_ref": "mcp.camera.observer"}},
    ]}
    config_path = tmp_path / "phos.yaml"
    config_path.write_text(yaml.safe_dump(document, default_flow_style=False, sort_keys=False))
    config = RuntimeConfig.from_file(config_path)
    store = SecretsService(config.resolve_path(Path(".")) / ".phos-secrets")
    standard_token, observer_token = "camera-standard-token", "camera-observer-token"
    store.set_secret("mcp.camera.standard", standard_token)
    store.set_secret("mcp.camera.observer", observer_token)

    pipeline = ActivePipeline()
    runtime = SimpleNamespace(core=RobotCore(), _behavior_engine=object(), _vision_pipeline=pipeline)
    application = PhosApplicationService(runtime)
    registry = config.agent_registry
    server = create_mcp_server(
        AgentApplicationService(application, registry.default_identity),
        authenticator=BearerAgentAuthenticator(registry, store),
    )
    app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp")

    async def session_for(token):
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001",
            headers={"Authorization": f"Bearer {token}"},
        )
        async with streamable_http_client("http://127.0.0.1:8001/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    async def exercise():
        async with server.session_manager.run():
            async for standard in session_for(standard_token):
                snapshot = await standard.read_resource("phos://vision/snapshot")
                tool_snapshot = await standard.call_tool("phos_get_camera_snapshot")
                pipeline.snapshot = None
                with pytest.raises(MCPError) as no_frame:
                    await standard.read_resource("phos://vision/snapshot")
                runtime._vision_pipeline = None
                with pytest.raises(MCPError) as unavailable:
                    await standard.read_resource("phos://vision/snapshot")
            runtime._vision_pipeline = pipeline
            async for observer in session_for(observer_token):
                with pytest.raises(MCPError) as denied:
                    await observer.read_resource("phos://vision/snapshot")
                denied_tool = await observer.call_tool("phos_get_camera_snapshot")
        return snapshot, tool_snapshot, str(no_frame.value), str(unavailable.value), str(denied.value), denied_tool

    snapshot, tool_snapshot, no_frame, unavailable, denied, denied_tool = asyncio.run(exercise())
    content = snapshot.contents[0]
    received = base64.b64decode(content.blob)

    def jpeg_dimensions(value):
        assert value.startswith(b"\xff\xd8") and value.endswith(b"\xff\xd9")
        offset = 2
        while offset < len(value) - 9:
            if value[offset] != 0xFF:
                offset += 1
                continue
            marker = value[offset + 1]
            offset += 2
            if marker in {0xD8, 0xD9}:
                continue
            size = int.from_bytes(value[offset:offset + 2], "big")
            if marker in {0xC0, 0xC1, 0xC2}:
                return int.from_bytes(value[offset + 3:offset + 5], "big"), int.from_bytes(value[offset + 5:offset + 7], "big")
            offset += size
        raise AssertionError("JPEG Start Of Frame marker not found")

    assert content.mime_type == "image/jpeg"
    assert jpeg_dimensions(received) == (1, 1)
    assert len(received) <= 512 * 1024
    assert tool_snapshot.is_error is False
    assert tool_snapshot.content[0].type == "image"
    assert tool_snapshot.content[0].mime_type == "image/jpeg"
    assert base64.b64decode(tool_snapshot.content[0].data) == received
    assert "captured_at=2026-01-02T00:00:00Z" in tool_snapshot.content[1].text
    assert "age_ms=0" in tool_snapshot.content[1].text
    assert "service_unavailable: No camera frame is available yet" in no_frame
    assert "service_unavailable: Vision is unavailable" in unavailable
    assert "permission_denied" in denied
    assert denied_tool.is_error is True and "permission_denied" in denied_tool.content[0].text
    assert runtime._vision_pipeline is pipeline
    assert pipeline.calls == 3  # resource, tool, then no-frame; observer is denied first.


def test_streamable_http_dynamic_agent_crud_updates_authentication_without_restart(tmp_path):
    from mcp.client import ClientSession
    from mcp.client.streamable_http import httpx2 as httpx, streamable_http_client

    document = load_document()
    document["agents"] = {"default_agent": "local-agent", "entries": [
        {"id": "local-agent", "display_name": "Local MCP agent", "permission_profile": "standard",
         "credential": {"type": "bearer", "secret_ref": "mcp.agents.local_agent.token"}},
    ]}
    path = tmp_path / "phos.yaml"
    path.write_text(yaml.safe_dump(document, default_flow_style=False, sort_keys=False))
    repository = ConfigRepository(path)
    config = repository.load()
    before_fps = config.display_fps
    provider = AgentRegistryProvider(config.agent_registry)
    store = SecretsService(tmp_path / ".phos-secrets")
    credentials = AgentCredentialService(provider, store)
    management = AgentManagementService(repository, provider, credentials)
    application = ApplicationSpy()
    authenticator = BearerAgentAuthenticator(provider, store)
    authenticated = []
    original_authenticate = authenticator.authenticate

    async def authenticate(context):
        identity = await original_authenticate(context)
        authenticated.append((identity.id, identity.permission_profile.value))
        return identity

    authenticator.authenticate = authenticate
    server = create_mcp_server(AgentApplicationService(application, config.agent_registry.default_identity),
                               authenticator=authenticator)
    app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp")

    async def session_for(token):
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8001",
                                 headers={"Authorization": f"Bearer {token}"})
        async with streamable_http_client("http://127.0.0.1:8001/mcp", http_client=client) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                yield session

    async def rejected(token):
        try:
            async for _ in session_for(token):
                pass
        except BaseExceptionGroup as error:
            return repr(error)
        raise AssertionError("deleted bearer unexpectedly initialized an MCP session")

    async def exercise(token):
        async with server.session_manager.run():
            async for observer in session_for(token):
                allowed_read = await observer.call_tool("phos_get_status")
                denied_sleep = await observer.call_tool("phos_sleep")
            management.update_agent("live-observer", "Live Observer", "standard")
            async for standard in session_for(token):
                allowed_sleep = await standard.call_tool("phos_sleep")
                await standard.call_tool("phos_wake")
            management.delete_agent("live-observer")
            failure = await rejected(token)
            return allowed_read, denied_sleep, allowed_sleep, failure

    management.create_agent("live-observer", "Live Observer", "observer")
    created = repository.load()
    assert created.agent_registry.get("live-observer").permission_profile.value == "observer"
    assert created.display_fps == before_fps
    issued = management.create_credential("live-observer")
    reference = issued.status.secret_ref
    allowed_read, denied_sleep, allowed_sleep, failure = asyncio.run(exercise(issued.token))

    assert allowed_read.is_error is False
    assert denied_sleep.is_error is True and "permission_denied" in denied_sleep.content[0].text
    assert allowed_sleep.is_error is False
    assert ("live-observer", "observer") in authenticated
    assert ("live-observer", "standard") in authenticated
    assert "authentication_failed" in failure
    assert store.has_secret(reference) is False
    reloaded = repository.load()
    assert reloaded.agent_registry.get("live-observer") is None
    assert reloaded.display_fps == before_fps
    assert application.calls == [("status",), ("set_state", "sleeping"), ("set_state", "idle")]


def test_camera_snapshot_uses_dedicated_permission_and_observer_is_denied():
    adapter, application = _adapter(profile=AgentProfile.OBSERVER)
    with pytest.raises(McpToolError, match="permission_denied: operation=camera_snapshot required_permission=read_camera_snapshot"):
        adapter.camera_snapshot()
    assert application.calls == []

    standard, application = _adapter()
    assert standard.camera_snapshot().startswith(b"\xff\xd8")
    assert application.calls == [("get_camera_snapshot",)]


def test_camera_snapshot_unavailable_is_sanitized():
    class Unavailable(ApplicationSpy):
        def get_camera_snapshot(self):
            raise ApplicationError("camera_frame_unavailable", "No camera frame is available yet.", status=503)
    adapter, _ = _adapter(Unavailable())
    with pytest.raises(McpToolError, match="service_unavailable: No camera frame is available yet"):
        adapter.camera_snapshot()


@pytest.mark.parametrize("snapshot", [
    b"not-a-jpeg",
    b"\xff\xd8" + (b"x" * (512 * 1024)) + b"\xff\xd9",
])
def test_camera_snapshot_service_rejects_invalid_or_oversized_jpegs_without_exposing_bytes(snapshot):
    class Pipeline:
        def camera_snapshot_jpeg(self):
            return snapshot

    runtime = SimpleNamespace(core=RobotCore(), _behavior_engine=object(), _vision_pipeline=Pipeline())

    with pytest.raises(ApplicationError, match="Camera snapshot is unavailable") as error:
        PhosApplicationService(runtime).get_camera_snapshot()

    assert error.value.code == "camera_snapshot_invalid"
    assert repr(snapshot) not in str(error.value)


@pytest.mark.parametrize(("method", "expected"), [
    ("get_status", ("status",)),
    ("get_physical_context", ("get_physical_context",)),
    ("get_presence", ("presence",)),
    ("get_attention", ("attention",)),
    ("get_observed_expression", ("observed_expression",)),
])
def test_read_tools_preserve_agent_results(method, expected):
    adapter, application = _adapter()
    result = getattr(adapter, method)()
    if method in {"get_status", "get_attention"}:
        assert result["state"] == "idle"
    elif method == "get_physical_context":
        assert result["robot"]["state"] == "idle"
    elif method == "get_observed_expression":
        assert result["available"] is None
    elif method == "get_presence":
        assert result["state"] is None
    else:
        assert result["operation"] == expected[0]
    assert application.calls == [expected]


@pytest.mark.parametrize(("method", "arguments", "expected"), [
    ("set_expression", ("happy",), ("set_expression", "happy")),
    ("speak", ("Hello",), ("speak", "Hello")),
    ("sleep", (), ("set_state", "sleeping")),
    ("wake", (), ("set_state", "idle")),
    ("signal_user", ("warning",), ("signal_user", "warning")),
])
def test_action_tools_delegate_only_through_agent_service(method, arguments, expected):
    adapter, application = _adapter()
    result = getattr(adapter, method)(*arguments)
    assert result["applied"] is True if method in {"set_expression", "sleep", "wake", "signal_user"} else result["accepted"] is True
    assert application.calls == [expected]


@pytest.mark.parametrize(("method", "arguments", "operation", "permission"), [
    ("set_expression", ("happy",), "set_expression", "control_expression"),
    ("speak", ("Hello",), "speak", "control_voice"),
    ("sleep", (), "sleep", "control_sleep"),
    ("wake", (), "wake", "control_sleep"),
    ("signal_user", ("warning",), "signal_user", "signal_user"),
])
def test_observer_reads_but_denied_actions_never_reach_the_application(method, arguments, operation, permission):
    adapter, application = _adapter(profile=AgentProfile.OBSERVER)
    assert adapter.get_physical_context()["robot"]["state"] == "idle"

    with pytest.raises(McpToolError, match=f"permission_denied: operation={operation} required_permission={permission}"):
        getattr(adapter, method)(*arguments)
    assert application.calls == [("get_physical_context",)]


def test_adapter_maps_semantic_errors_without_tracebacks_or_provider_details():
    class FailingApplication(ApplicationSpy):
        def set_expression(self, expression):
            raise ApplicationError("invalid_expression", "Unsupported semantic expression.")

        def speak(self, text):
            raise ApplicationError("tts_unavailable", "Text-to-speech is currently unavailable.", status=503)

        def signal_user(self, signal):
            raise RuntimeError("provider secret traceback")

    adapter, _ = _adapter(FailingApplication())
    with pytest.raises(McpToolError, match="invalid_argument: Unsupported semantic expression"):
        adapter.set_expression("not-real")
    with pytest.raises(McpToolError, match="service_unavailable: Text-to-speech"):
        adapter.speak("Hello")
    with pytest.raises(McpToolError) as error:
        adapter.signal_user("warning")
    assert str(error.value) == "internal_error: PHOS could not complete the requested operation."

    class UnsupportedApplication(ApplicationSpy):
        def set_expression(self, expression):
            raise ApplicationError("unsupported_expression_command", "Expression is not writable through this command.")

    unsupported, _ = _adapter(UnsupportedApplication())
    with pytest.raises(McpToolError, match="unsupported_capability: Expression is not writable"):
        unsupported.set_expression("happy")


def test_mcp_server_returns_structured_results_and_schema_rejects_bad_speech_input():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)

    async def call():
        result = await server.call_tool("phos_get_physical_context", {})
        with pytest.raises(Exception):
            await server.call_tool("phos_speak", {"text": ""})
        return result

    assert asyncio.run(call()).structured_content["robot"]["state"] == "idle"


def test_streamable_http_server_configuration_is_kept_outside_the_semantic_surface():
    adapter, _ = _adapter()
    server = create_mcp_server(adapter._agent_service)
    app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/robot-mcp")

    assert [route.path for route in app.routes] == ["/robot-mcp"]


def test_mcp_status_uses_the_sanitized_agent_status_contract():
    adapter, _ = _adapter()

    status = adapter.get_status()

    assert set(status) == {"running", "state", "availability", "degraded", "degraded_reasons"}
    assert status["availability"]["physical_context"] is True


def test_mcp_sleep_and_wake_use_the_runtime_loop_without_self_blocking():
    class Runtime:
        def __init__(self):
            self.core = RobotCore()
            self._behavior_engine = BehaviorEngine(self.core.events)

    async def exercise():
        runtime = Runtime()
        runtime._loop = asyncio.get_running_loop()
        agent = AgentApplicationService(PhosApplicationService(runtime), AgentAccess.for_profile())
        adapter = McpSemanticAdapter(agent)
        assert await adapter.sleep_async() == {"state": "sleeping", "applied": True}
        assert await adapter.wake_async() == {"state": "idle", "applied": True}

    asyncio.run(exercise())


def test_semantic_events_publish_only_mapped_resource_update_cues_and_close_cleanly():
    class SemanticApplication(ApplicationSpy):
        def __init__(self):
            super().__init__()
            self.events = SemanticEventPublisher(deduplication_window_seconds=0)

        def subscribe_semantic_events(self, listener, *, event_types=None):
            return self.events.subscribe(listener, event_types=event_types)

    async def exercise():
        application = SemanticApplication()
        agent = AgentApplicationService(application, AgentAccess.for_profile())
        bridge = McpSemanticEventBridge(agent)
        received = []
        bridge.bus.subscribe(received.append)
        bridge.start(asyncio.get_running_loop())

        application.events.publish("robot_state_changed", "robot", {"current": "sleeping"})
        application.events.publish("environment_condition_changed", "environment", {"current": "warm"})
        application.events.publish("air_quality_condition_changed", "air_quality", {"current": "poor"})
        application.events.publish("motion_state_changed", "motion", {"current": "moving"})
        application.events.publish("presence_changed", "presence", {"current": "present"})
        application.events.publish("attention_changed", "attention", {"current": "engaged"})
        application.events.publish("observed_expression_changed", "vision", {"current": "happy"})
        application.events.publish("capability_changed", "system", {"current": True})
        application.events.publish("system_health_changed", "system", {"current": "healthy"})
        application.events.publish("environment_availability_changed", "environment", {"current": "stale"})
        application.events.publish("air_quality_availability_changed", "air_quality", {"current": "unavailable"})
        application.events.publish("motion_availability_changed", "motion", {"current": "available"})
        await asyncio.sleep(.05)

        assert {event.uri for event in received} == {
            "phos://status", "phos://physical-context", "phos://physical-context/assessment", "phos://environment",
        }
        assert len(received) == 4
        assert "phos://vision/snapshot" not in {event.uri for event in received}
        bridge.close()
        assert application.events.active_subscription_count == 0

    asyncio.run(exercise())


def test_semantic_event_bridge_coalesces_repeated_uri_updates_without_blocking_publishers():
    class SemanticApplication(ApplicationSpy):
        def __init__(self):
            super().__init__()
            self.events = SemanticEventPublisher(deduplication_window_seconds=0)

        def subscribe_semantic_events(self, listener, *, event_types=None):
            return self.events.subscribe(listener, event_types=event_types)

    async def exercise():
        application = SemanticApplication()
        bridge = McpSemanticEventBridge(AgentApplicationService(application, AgentAccess.for_profile()))
        received = []
        bridge.bus.subscribe(received.append)
        bridge.start(asyncio.get_running_loop())
        for state in ("sleeping", "idle", "thinking"):
            application.events.publish("robot_state_changed", "robot", {"current": state})
        await asyncio.sleep(.05)
        assert {event.uri for event in received} == {"phos://status", "phos://physical-context"}
        assert len(received) == 2
        bridge.close()

    asyncio.run(exercise())


def test_streamable_http_listen_delivers_presence_and_attention_resource_cues():
    """Exercise the modern subscription protocol over a loopback HTTP listener.

    The client receives only a resource-update cue, then obtains the current
    anonymous projection by re-reading the resource.  This mirrors the bridge
    client's contract and deliberately avoids an in-process subscription mock.
    """
    from mcp.client import Client
    from mcp.client.streamable_http import httpx2 as httpx, streamable_http_client
    from mcp.shared.subscriptions import ResourceUpdated
    import uvicorn

    class SemanticApplication(ApplicationSpy):
        def __init__(self):
            super().__init__()
            self.events = SemanticEventPublisher(deduplication_window_seconds=0)
            self.context = {
                "robot": {"state": "idle"},
                "environment": {"available": True}, "air_quality": {"available": True},
                "motion": {"available": True}, "observed_expression": {"available": False},
                "presence": {"state": "no_one", "people_count": 0, "confidence": None},
                "attention": {"state": "idle", "target": {"x": None, "y": None, "confidence": None}},
            }

        def subscribe_semantic_events(self, listener, *, event_types=None):
            return self.events.subscribe(listener, event_types=event_types)

        def get_physical_context(self):
            # Return a detached document just as the production read model does.
            return json.loads(json.dumps(self.context))

    async def next_update(subscription):
        event = await asyncio.wait_for(anext(subscription), timeout=1)
        assert isinstance(event, ResourceUpdated)
        assert event.uri == "phos://physical-context"

    async def read_context(client):
        result = await client.read_resource("phos://physical-context")
        return json.loads(result.contents[0].text)

    async def exercise():
        application = SemanticApplication()
        agent = AgentApplicationService(application, AgentAccess.for_profile())
        bridge = McpSemanticEventBridge(agent)

        class Authenticator:
            async def authenticate(self, context):
                if context.request.headers.get("authorization") == "Bearer allowed":
                    return AgentIdentity("allowed", AgentProfile.STANDARD)
                raise PermissionError("authentication_failed")

        server = create_mcp_server(agent, subscriptions=bridge.bus, authenticator=Authenticator())
        app = server.streamable_http_app(host="127.0.0.1", streamable_http_path="/mcp")
        bridge.start(asyncio.get_running_loop())
        listener = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0,
                                                  log_level="critical", access_log=False))
        listener_task = asyncio.create_task(listener.serve())
        try:
            for _ in range(100):
                if listener.started:
                    break
                await asyncio.sleep(.01)
            assert listener.started
            port = listener.servers[0].sockets[0].getsockname()[1]
            url = f"http://127.0.0.1:{port}/mcp"

            async def connect(authorization):
                http_client = httpx.AsyncClient(headers={"Authorization": authorization})
                transport = streamable_http_client(url, http_client=http_client)
                return http_client, Client(transport, cache=None)

            rejected_http, rejected_client = await connect("Bearer rejected")
            try:
                try:
                    async with rejected_client:
                        raise AssertionError("unauthorized client unexpectedly initialized")
                except BaseException as error:
                    rejected = repr(error)
            finally:
                await rejected_http.aclose()

            allowed_http, client = await connect("Bearer allowed")
            try:
                async with client:
                    assert client.protocol_version == "2026-07-28"
                    async with client.listen(resource_subscriptions=["phos://physical-context"]) as updates:
                        application.context["presence"] = {
                            "state": "person_present", "people_count": 1, "confidence": 0.92,
                        }
                        application.events.publish(
                            "presence_changed", "presence",
                            {"current": "person_present", "people_count": 1},
                        )
                        await next_update(updates)
                        present = await read_context(client)

                        application.context["attention"] = {
                            "state": "tracking", "target": {"x": 0.25, "y": -0.1, "confidence": 0.88},
                        }
                        application.events.publish(
                            "attention_changed", "attention", {"current": "tracking"},
                        )
                        await next_update(updates)
                        tracking = await read_context(client)

                        application.context["presence"] = {
                            "state": "no_one", "people_count": 0, "confidence": None,
                        }
                        application.events.publish(
                            "presence_changed", "presence",
                            {"current": "no_one", "people_count": 0},
                        )
                        await next_update(updates)
                        absent = await read_context(client)
            finally:
                await allowed_http.aclose()
        finally:
            listener.should_exit = True
            await listener_task
            bridge.close()
        return application, present, tracking, absent, rejected

    application, present, tracking, absent, rejected = asyncio.run(exercise())
    assert present["presence"] == {"state": "person_present", "people_count": 1, "confidence": 0.92}
    assert tracking["attention"] == {
        "state": "tracking", "target": {"x": 0.25, "y": -0.1, "confidence": 0.88},
    }
    assert absent["presence"] == {"state": "no_one", "people_count": 0, "confidence": None}
    projection = json.dumps({"present": present, "tracking": tracking, "absent": absent})
    assert "candidate_id" not in projection and "target_id" not in projection
    assert "authentication_failed" in rejected
    assert application.events.active_subscription_count == 0
