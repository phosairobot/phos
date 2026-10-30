from flask import Flask

from robot.web.api import create_api
from robot.web.openapi import validate


class Service:
    def status(self): return {}
    def robot_state(self): return {}
    def environment(self): return {}
    def motion(self): return {}
    def health(self): return {}
    def capabilities(self): return {}
    def overlay(self): return {}
    def set_overlay(self, value): return {}
    def clear_overlay(self): return {}
    def config(self): return {}
    def update_config(self, value): return {}
    def set_expression(self, value): return {}
    def set_state(self, value): return {}
    def set_visual_source(self, value): return {}
    def subscribe(self, listener): return lambda: None
    def emit_snapshot_changes(self): pass


def test_openapi_is_valid_and_covers_every_public_api_route():
    spec = validate()
    assert spec["servers"] == [{"url": "/", "description": "The same configured PHOS Web Admin/API listener that served this document"}]
    app = Flask(__name__)
    app.register_blueprint(create_api(Service()))
    actual = {(rule.rule, method.lower()) for rule in app.url_map.iter_rules()
              if rule.rule.startswith("/api/v1/") for method in rule.methods
              if method not in {"HEAD", "OPTIONS"}}
    documented = {(path, method) for path, operations in spec["paths"].items()
                  for method in operations}
    assert actual == documented


def test_mcp_settings_openapi_matches_the_authenticated_admin_contract():
    spec = validate()
    assert spec["security"] == [{"phosAdminSession": []}]
    operations = spec["paths"]["/api/v1/settings/mcp"]
    assert set(operations) == {"get", "patch"}
    assert operations["get"]["responses"]["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/McpSettings"}
    patch = operations["patch"]
    assert patch["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/McpSettingsPatch"}
    assert set(patch["responses"]) == {"200", "400", "409", "503", "500"}
    schemas = spec["components"]["schemas"]
    assert schemas["McpSettingsPatch"] == {
        "type": "object", "required": ["enabled", "port"], "additionalProperties": False,
        "properties": {
            "enabled": {"type": "boolean"},
            "port": {"type": "integer", "minimum": 1, "maximum": 65535},
        },
    }
    assert schemas["McpSettings"]["required"] == [
        "enabled", "port", "status", "bound_port", "endpoint", "error"]
