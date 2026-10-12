"""Focused dry-run tests for the interaction command-routing boundary."""
from robot.interaction import (AllowListedCommandPolicy, CommandRouteStatus,
                               CommandRouter, ResolvedIntent)


def test_catalog_routes_canonical_intents_without_execution():
    router = CommandRouter()
    expected = {
        "sleep": "robot.sleep",
        "wake": "robot.wake",
        "get_temperature": "environment.get_temperature",
        "get_air_quality": "environment.get_air_quality",
    }
    for name, command in expected.items():
        result = router.route(ResolvedIntent(name))
        assert result.document() == {"status": "routable", "intent": name,
                                     "command": command, "parameters": {},
                                     "execution_allowed": False, "reason": None}


def test_set_expression_routes_only_approved_semantic_values():
    router = CommandRouter()
    for expression in ("neutral", "happy", "curious", "surprised"):
        result = router.route(ResolvedIntent("set_expression", {"expression": expression}))
        assert result.status is CommandRouteStatus.ROUTABLE
        assert result.command == "face.set_expression"
        assert result.parameters == {"expression": expression}
        assert result.execution_allowed is False


def test_router_rejects_unknown_and_invalid_parameter_shapes():
    router = CommandRouter()
    assert router.route(ResolvedIntent("unknown_command")).reason == "unknown_intent"
    for parameters in ({}, {"expression": "sleepy"}, {"expression": 3},
                       {"expression": "happy", "extra": True}):
        result = router.route(ResolvedIntent("set_expression", parameters))
        assert (result.status, result.reason) == (CommandRouteStatus.REJECTED, "invalid_parameter")
    unexpected = router.route(ResolvedIntent("sleep", {"now": True}))
    assert (unexpected.status, unexpected.reason) == (CommandRouteStatus.REJECTED, "invalid_parameter")


def test_policy_can_deny_a_valid_route_while_execution_stays_disabled():
    router = CommandRouter(AllowListedCommandPolicy(frozenset({"robot.wake"})))
    denied = router.route(ResolvedIntent("sleep"))
    permitted = router.route(ResolvedIntent("wake"))
    assert (denied.status, denied.reason, denied.execution_allowed) == (
        CommandRouteStatus.REJECTED, "policy_denied", False)
    assert (permitted.status, permitted.command, permitted.execution_allowed) == (
        CommandRouteStatus.ROUTABLE, "robot.wake", False)


def test_policy_failure_is_distinct_from_unknown_or_invalid_intents():
    class BrokenPolicy:
        def permits(self, route):
            raise RuntimeError("policy store unavailable")

    result = CommandRouter(BrokenPolicy()).route(ResolvedIntent("sleep"))
    assert (result.status, result.reason, result.execution_allowed) == (
        CommandRouteStatus.FAILED, "internal_error", False)


def test_router_has_no_runtime_or_hardware_dependency():
    # The router accepts only domain data and has no injected service to invoke.
    result = CommandRouter().route(ResolvedIntent("sleep"))
    assert result.status is CommandRouteStatus.ROUTABLE
