"""Hardware-free administration/security tests using real CSRF and password hashes."""
import json
import html
from html.parser import HTMLParser
import re
import socket
from pathlib import Path
from urllib.request import urlopen

import pytest
import yaml

from robot.config import ConfigRepository, ConfigurationError, RuntimeConfig, load_document
from robot.secrets import SecretsService
from robot.web.app import create_app
from robot.web.auth import PasswordStore
from robot.web.server import WebServer

PASSWORD = "a new long test password"


@pytest.fixture
def setup(tmp_path):
    document = load_document()
    document["logging"]["file"] = None
    path = tmp_path / "phos.json"
    path.write_text(json.dumps(document))
    now = [100.0]
    app = create_app(path, active_document=document, clock=lambda: now[0])
    app.testing = True
    return app, path, now


def csrf(response):
    return re.search(r'name="csrf_token" value="([^"]+)"', response.get_data(as_text=True)).group(1)


def post(client, route, data=None):
    values = {"csrf_token": csrf(client.get("/password" if route in {"/", "/logout"} else route, follow_redirects=True)), **(data or {})}
    return client.post(route, data=values)


def login(client, password="phos"):
    return post(client, "/login", {"password": password})


def authorize(app):
    client = app.test_client()
    assert login(client).location == "/password"
    assert post(client, "/password", {"current": "phos", "new": PASSWORD, "confirmation": PASSWORD}).status_code == 302
    assert login(client, PASSWORD).location == "/"
    return client


class FormParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.values = {}
        self.select = None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and "name" in a:
            if a.get("type") != "checkbox" or "checked" in a:
                self.values[a["name"]] = a.get("value", "on")
        elif tag == "select":
            self.select = a["name"]
        elif tag == "option" and "selected" in a:
            self.values[self.select] = a["value"]


def form(client, area="display"):
    parser = FormParser()
    parser.feed(client.get(f"/configuration/{area}", follow_redirects=True).get_data(as_text=True))
    return parser.values


def editor_form(client):
    response = client.get("/configuration/editor")
    source = html.unescape(re.search(r'<textarea[^>]*name="configuration"[^>]*>(.*?)</textarea>',
                                     response.get_data(as_text=True), re.DOTALL).group(1))
    return response, {"csrf_token": csrf(response), "revision": re.search(
        r'name="revision" value="([^"]+)"', response.get_data(as_text=True)).group(1),
        "configuration": source}


def test_bootstrap_login_forces_change_and_never_exposes_config(setup):
    app, path, _ = setup
    client = app.test_client()
    assert client.get("/").location == "/login"
    assert client.get("/password").location == "/login"
    assert client.post("/", data={}).status_code == 400
    assert login(client).location == "/password"
    assert client.get("/").location == "/password"
    assert post(client, "/", {}).location == "/password"
    page = client.get("/password").get_data(as_text=True)
    assert "First login" in page
    data = json.loads((path.parent / ".phos-admin/password.json").read_text())
    assert "phos" not in data["password_hash"]
    assert data["must_change"] is True
    assert (path.parent / ".phos-admin").stat().st_mode & 0o777 == 0o700
    assert (path.parent / ".phos-admin/password.json").stat().st_mode & 0o777 == 0o600


def test_credentials_are_write_only_csrf_protected_and_persist_encrypted(setup, caplog):
    app, path, _ = setup
    assert app.test_client().get("/credentials").location == "/login"
    client = authorize(app)
    value = "do-not-render-or-log-this-secret"

    page = client.get("/credentials")
    body = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "ElevenLabs API Key" in body and "Not configured" in body
    assert 'type="password"' in body and value not in body
    assert not hasattr(app.extensions["phos_secrets"], "get_secret")
    assert client.post("/credentials", data={"action": "set"}).status_code == 400

    response = client.post("/credentials", data={
        "csrf_token": csrf(page), "action": "set", "name": "tts.elevenlabs.api_key", "value": value,
    })
    assert response.status_code == 302 and response.location == "/credentials"
    saved = client.get("/credentials").get_data(as_text=True)
    assert "Configured" in saved and value not in saved
    service = SecretsService(path.parent / ".phos-secrets")
    assert service.get_secret("tts.elevenlabs.api_key") == value
    assert value.encode("utf-8") not in service.store_path.read_bytes()

    blank = client.post("/credentials", data={
        "csrf_token": csrf(client.get("/credentials")), "action": "set",
        "name": "tts.elevenlabs.api_key", "value": "",
    })
    assert blank.status_code == 400
    assert service.get_secret("tts.elevenlabs.api_key") == value
    not_confirmed = client.post("/credentials", data={
        "csrf_token": csrf(client.get("/credentials")), "action": "remove",
        "name": "tts.elevenlabs.api_key",
    })
    assert not_confirmed.status_code == 400
    removed = client.post("/credentials", data={
        "csrf_token": csrf(client.get("/credentials")), "action": "remove",
        "name": "tts.elevenlabs.api_key", "confirm_remove": "yes",
    })
    assert removed.status_code == 302
    assert not service.has_secret("tts.elevenlabs.api_key")
    assert value not in caplog.text


def test_existing_phos_images_are_used_as_responsive_visuals(setup):
    app, _, _ = setup
    login_page = app.test_client().get("/login").get_data(as_text=True)
    dashboard = authorize(app).get("/").get_data(as_text=True)
    assert 'images/phos-login-black.webp' in login_page and 'alt="PHOS"' in login_page
    assert 'images/phos-dashboard-black.webp' in dashboard and 'alt="" loading="lazy"' in dashboard
    css = (app.root_path and __import__("pathlib").Path(app.root_path) / "static" / "admin.css").read_text()
    assert ".login-panel { display: grid" in css
    assert ".login-branding img" in css and "height: auto" in css
    assert "mask-image: radial-gradient" in css
    assert ".dashboard-phos-image" in css and "@media (max-width: 430px)" in css


def test_api_authentication_returns_json_while_browser_pages_redirect(setup):
    from robot.core.behavior_engine import BehaviorEngine
    from robot.core.runtime import RobotCore
    from robot.services import PhosApplicationService

    class Runtime:
        def __init__(self):
            self.core = RobotCore()
            self._behavior_engine = BehaviorEngine(self.core.events)
        def sensor_status(self): return {}
        def apply_base_visual_source(self, config): pass

    _, path, now = setup
    app = create_app(path, clock=lambda: now[0], application_service=PhosApplicationService(Runtime()))
    client = app.test_client()
    # Browser administration retains its redirect-based user experience.
    assert client.get("/").status_code == 302
    assert client.get("/").location == "/login"
    # API clients always receive a machine-readable authentication failure.
    response = client.get("/api/v1/status", follow_redirects=False)
    assert response.status_code == 401
    assert response.is_json
    assert response.json == {"error": {"code": "unauthorized", "message": "Authentication required.", "details": {}}}
    assert response.headers.get("Location") is None
    assert "<html" not in response.get_data(as_text=True).lower()
    assert login(client).location == "/password"
    response = client.get("/api/v1/status", follow_redirects=False)
    assert response.status_code == 403
    assert response.is_json and response.json["error"]["code"] == "forbidden"
    assert response.headers.get("Location") is None
    authenticated = authorize(app)
    response = authenticated.get("/api/v1/status")
    assert response.status_code == 200 and response.is_json


def test_canonical_admin_navigation_has_unique_destinations_and_redirects_legacy_areas(setup):
    app, _, _ = setup
    client = authorize(app)
    page = client.get("/").get_data(as_text=True)
    for label in ("Dashboard", "PHOS Status", "Controls", "Sensors", "API", "Diagnostics", "System Actions", "Settings"):
        assert label in page
    assert page.count('href="/system"') == 1
    system_routes = [rule for rule in app.url_map.iter_rules() if rule.rule == "/system"]
    assert len(system_routes) == 1 and system_routes[0].endpoint == "system"
    assert 'href="/configuration/vision"' not in page
    assert 'href="/configuration/logging"' not in page
    assert client.get("/configuration/display").location == "/configuration/appearance#display"
    assert client.get("/configuration/logging").location == "/configuration/runtime#logging"
    controls = client.get("/configuration/controls").get_data(as_text=True)
    assert "Apply robot state" in controls and "Editing saved settings" not in controls
    status = client.get("/configuration/status").get_data(as_text=True)
    assert "Detailed runtime diagnostics" in status


def test_focused_settings_and_page_responsibilities(setup):
    app, _, _ = setup
    client = authorize(app)
    eyes = client.get("/configuration/eyes").get_data(as_text=True)
    network = client.get("/configuration/network").get_data(as_text=True)
    dashboard = client.get("/").get_data(as_text=True)
    status = client.get("/configuration/status").get_data(as_text=True)
    controls = client.get("/configuration/controls").get_data(as_text=True)
    sensors = client.get("/configuration/sensors").get_data(as_text=True)
    assert 'name="display.iris_color"' in eyes
    assert 'name="web.host"' in network and 'name="web.port"' in network
    assert 'data-live="environment.temperature"' in dashboard
    for key in ("presence.state", "presence.people_count", "attention.state", "attention.target_id",
                "attention.target_position", "attention.target_confidence"):
        assert f'data-live="{key}"' in dashboard
        assert f'data-live="{key}"' in status
    for key in ("observed_expression.label", "observed_expression.confidence", "observed_expression.provider",
                "observed_expression.model", "observed_expression.available", "observed_expression.detail",
                "observed_expression.observed_at"):
        assert f'data-live="{key}"' in dashboard
        assert f'data-live="{key}"' in status
    assert "PHOS Expression" in status
    for key in ("expression_reaction.active", "expression_reaction.reaction", "expression_reaction.observed_label"):
        assert f'data-live="{key}"' in status
    assert 'data-command="set_robot_state"' not in status
    assert 'data-command="set_robot_state"' in controls
    assert 'data-command="set_robot_state"' not in sensors


def test_observed_expression_ui_has_no_neutral_fallback():
    script = Path("src/robot/web/static/admin.js").read_text()
    assert 'display("observed_expression.label", observedExpression.label);' in script
    assert 'observedExpression.label || "neutral"' not in script


def test_controls_are_separated_into_responsive_action_sections(setup):
    app, _, _ = setup
    client = authorize(app)
    controls = client.get("/configuration/controls").get_data(as_text=True)
    for section, command, action in (
        ("robot-state", "set_robot_state", "Apply robot state"),
        ("expression", "set_expression", "Apply expression"),
        ("visual-source", "set_visual_source", "Apply visual source"),
    ):
        match = re.search(rf'<section class="control-card" data-control-section="{section}">(.*?)</section>', controls)
        assert match and f'data-command="{command}"' in match.group(1) and action in match.group(1)
    overlay = re.search(r'<section class="control-card" data-control-section="overlay">(.*?)</section>', controls)
    assert overlay and 'data-overlay-submit' in overlay.group(1) and 'data-overlay-clear' in overlay.group(1)
    assert controls.count('data-command="set_robot_state"') == 1
    assert controls.count('data-command="set_expression"') == 1
    assert controls.count('data-command="set_visual_source"') == 1
    for page in (client.get("/").get_data(as_text=True), client.get("/configuration/status").get_data(as_text=True), client.get("/configuration/sensors").get_data(as_text=True)):
        assert 'data-command=' not in page and 'data-overlay-submit' not in page


def test_controls_responsive_layout_and_button_groups_do_not_overflow():
    from pathlib import Path

    css = (Path(__file__).parents[1] / "src/robot/web/static/admin.css").read_text()
    assert ".control-grid { grid-template-columns: repeat(2, minmax(0, 1fr))" in css
    assert ".button-group { display: flex; flex-wrap: wrap;" in css
    assert "@media (max-width: 760px)" in css
    assert ".control-grid { grid-template-columns: minmax(0, 1fr); }" in css
    assert ".button-group { flex-direction: column; }" in css


def test_live_dashboard_is_an_api_client_and_retains_server_rendered_sensor_fallback(setup):
    from robot.core.behavior_engine import BehaviorEngine
    from robot.core.runtime import RobotCore
    from robot.services import PhosApplicationService

    class Runtime:
        def __init__(self):
            self.core = RobotCore()
            self._behavior_engine = BehaviorEngine(self.core.events)
        def sensor_status(self): return {}
        def apply_base_visual_source(self, config): pass

    _, path, now = setup
    app = create_app(path, clock=lambda: now[0], application_service=PhosApplicationService(Runtime()))
    page = authorize(app).get("/configuration/controls").get_data(as_text=True)
    assert 'data-phos-live' in page
    assert 'data-command="set_robot_state"' in page
    assert 'data-overlay-submit' in page
    source = (app.root_path and __import__("pathlib").Path(app.root_path) / "static" / "admin.js").read_text()
    assert '"/events"' in source and '"/capabilities"' in source
    for endpoint in ('"/status"', '"/health"', '"/environment"', '"/motion"', '"/overlay"'):
        assert endpoint in source
    assert 'const apiUrl = (path) => path.startsWith(`${endpoint}/`) ? path : endpoint + path;' in source
    assert 'fetch(apiUrl(path)' in source
    assert 'snapshot({refreshCapabilities: true}).then(connect)' in source
    assert 'node.prepend(option); node.value = value;' in source
    assert 'state: payload.state || payload.current' in source
    assert 'PHOS returned HTML instead of its API response' in source
    assert 'Your administrator session has expired' in source
    assert 'sensor_status' not in source and 'GPIO' not in source
    for event_type in ("presence_changed", "attention_changed", "person_entered", "person_left",
                       "attention_target_acquired", "attention_target_changed", "attention_target_lost",
                       "observed_expression_changed"):
        assert f'"{event_type}"' in source


def test_incorrect_login_and_password_validation(setup):
    app, _, now = setup
    client = app.test_client()
    assert login(client, "wrong").status_code == 401
    assert login(client).status_code == 302
    for current, new, confirm in [("wrong", PASSWORD, PASSWORD), ("phos", "short", "short"), ("phos", PASSWORD, "different")]:
        response = post(client, "/password", {"current": current, "new": new, "confirmation": confirm})
        assert response.status_code == 400
        assert 'value="phos"' not in response.get_data(as_text=True)
    assert app.extensions["phos_passwords"].must_change
    now[0] += 61
    assert post(client, "/password", {"current": "phos", "new": PASSWORD, "confirmation": PASSWORD}).status_code == 302
    assert login(client).status_code == 401
    assert login(client, PASSWORD).location == "/"


def test_password_change_revokes_all_sessions_and_persists(setup):
    app, path, now = setup
    first = authorize(app)
    second = app.test_client()
    assert login(second, PASSWORD).status_code == 302
    now[0] += 61
    replacement = "a different long password"
    assert post(first, "/password", {"current": PASSWORD, "new": replacement, "confirmation": replacement}).status_code == 302
    assert first.get("/").location == "/login"
    assert second.get("/").location == "/login"
    assert login(first, PASSWORD).status_code == 401
    assert login(first, replacement).location == "/"
    reloaded = PasswordStore(path.parent / ".phos-admin")
    assert reloaded.verify(replacement) and not reloaded.must_change


def test_logout_cookie_replay_and_expiration(setup):
    app, _, now = setup
    client = authorize(app)
    cookie = client.get_cookie("phos_admin").value
    assert client.get("/logout").status_code == 405
    assert post(client, "/logout").location == "/login"
    client.set_cookie("phos_admin", cookie)
    assert client.get("/").location == "/login"
    assert login(client, PASSWORD).location == "/"
    now[0] += 1801
    assert client.get("/").location == "/login"


@pytest.mark.parametrize("route", ["/", "/password", "/logout", "/login"])
def test_csrf_protects_every_mutation(setup, route):
    app, path, _ = setup
    client = authorize(app)
    original = path.read_bytes()
    assert client.post(route, data={"password": PASSWORD}).status_code == 400
    assert client.post(route, data={"csrf_token": "forged"}).status_code == 400
    assert path.read_bytes() == original


def test_login_rate_limit_is_global_and_expires(setup):
    app, _, now = setup
    for _ in range(5):
        assert login(app.test_client(), "wrong").status_code == 401
    client = app.test_client()
    response = login(client)
    assert response.status_code == 429 and response.headers["Retry-After"] == "60"
    now[0] += 61
    assert login(client).location == "/password"


def test_configuration_controls_switch_both_providers_and_persist(setup):
    app, path, _ = setup
    client = authorize(app)
    vision_form = form(client, "vision")
    assert {"vision.camera_preview.position",
            "vision.camera_preview.scale", "vision.camera_preview.max_fps",
            "vision.camera_preview.show_face_box", "vision.camera_preview.show_expression",
            "vision.camera_preview.show_confidence"} <= vision_form.keys()
    vision_form.update({"vision.camera_preview.enabled": "on",
                        "vision.camera_preview.position": "top_left",
                        "vision.camera_preview.scale": "0.3",
                        "vision.camera_preview.max_fps": "4"})
    assert client.post("/", data=vision_form).status_code == 302
    saved_preview = RuntimeConfig.from_file(path)
    assert saved_preview.camera_preview_enabled
    assert saved_preview.camera_preview_position == "top_left"
    assert saved_preview.camera_preview_scale == 0.3
    assert saved_preview.camera_preview_max_fps == 4
    model = path.parent / "model.onnx"
    model.write_bytes(b"fake model; never inferred")
    for provider in ("aws", "local"):
        data = form(client, "expression")
        data.update({"expression.provider": provider, "expression.enabled": "on",
                     "expression.local.model_path": "model.onnx"})
        response = client.post("/", data=data)
        assert response.status_code == 302
        config = RuntimeConfig.from_file(path)
        assert config.expression_provider == provider and config.expression_enabled
        assert config.display_fps == 30
        page = client.get("/configuration/expression", follow_redirects=True).get_data(as_text=True)
        assert "Configuration saved. Use System actions to reload eligible settings, or restart PHOS for hardware and other pending changes." in page


def test_voice_settings_show_provider_availability_and_persist_local_settings(setup):
    app, path, _ = setup
    client = authorize(app)
    page = client.get("/configuration/voice").get_data(as_text=True)
    assert "Local / Piper" in page
    assert "ElevenLabs (Coming soon)" in page
    assert "Google Cloud TTS (Coming soon)" in page
    assert "Cartesia (Coming soon)" in page
    model = path.parent / "voice.onnx"; model.write_bytes(b"model")
    data = form(client, "voice")
    data.update({"tts.enabled": "on", "tts.provider": "local", "tts.local.model_path": "voice.onnx"})
    response = client.post("/", data=data, follow_redirects=True)
    assert response.status_code == 200
    saved = RuntimeConfig.from_file(path)
    assert saved.tts_enabled and saved.tts_provider == "local"
    assert saved.tts_local_model_path == Path("voice.onnx")
    assert "restart PHOS" in response.get_data(as_text=True)


@pytest.mark.parametrize("field,value", [("display.fps", "0"), ("vision.camera_resolution", "1,2,3"),
    ("expression.aws.refresh_seconds", "999999"), ("expression.provider", "bad"),
    ("display.fps", "not-a-number"), ("web.port", "65536")])
def test_invalid_form_preserves_file_and_input(setup, field, value):
    app, path, _ = setup
    client = authorize(app)
    old = path.read_bytes()
    area = {"display": "display", "vision": "vision", "expression": "expression", "web": "network"}[field.split(".")[0]]
    data = form(client, area)
    preserved, entered = {"display": ("display.width", "777"), "vision": ("vision.capture_fps", "13"),
                          "expression": ("expression.aws.region", "test-region"), "network": ("web.host", "192.168.1.127")}[area]
    data.update({field: value, preserved: entered})
    response = client.post("/", data=data)
    assert response.status_code == 400
    assert path.read_bytes() == old
    assert f'value="{entered}"' in response.get_data(as_text=True)


def test_display_appearance_theme_is_selectable_and_saved_canonically(setup):
    app, path, _ = setup
    client = authorize(app)
    data = form(client, "display")
    page = client.get("/configuration/display").get_data(as_text=True)
    assert 'name="display.iris_color"' in page
    assert all(color in page for color in ("cyan", "turquoise", "amber", "violet"))
    data["display.iris_color"] = "violet"
    assert client.post("/configuration/display", data=data).status_code == 302
    assert RuntimeConfig.from_file(path).iris_color == "violet"


def test_led_ring_palette_is_exposed_and_selected_color_round_trips_through_web_admin(setup):
    app, path, _ = setup
    client = authorize(app)
    page = client.get("/configuration/display").get_data(as_text=True)
    expected = ("green", "red", "yellow", "blue", "violet", "white", "cyan", "turquoise", "orange", "magenta")
    assert all(f'<option value="{color}"' in page for color in expected)
    data = form(client, "display")
    data["led_ring.base_color"] = "magenta"
    assert client.post("/configuration/display", data=data).status_code == 302
    assert RuntimeConfig.from_file(path).led_ring_base_color == "magenta"


def test_stale_form_cannot_overwrite_new_save(setup):
    app, path, _ = setup
    client = authorize(app)
    old = form(client)
    new = dict(old, **{"display.fps": "21"})
    assert client.post("/", data=new).status_code == 302
    assert client.post("/", data=old).status_code == 400
    assert RuntimeConfig.from_file(path).display_fps == 21


def test_aws_secrets_never_exposed_or_persisted(setup, monkeypatch, caplog):
    app, path, _ = setup
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(name, "SENSITIVE_TEST_VALUE")
    client = authorize(app)
    data = form(client, "expression")
    assert not any("access_key" in name or "token" in name and name != "csrf_token" for name in data)
    data["expression.aws.access_key"] = "SENSITIVE_TEST_VALUE"
    assert client.post("/", data=data).status_code == 400
    assert "SENSITIVE_TEST_VALUE" not in client.get("/").get_data(as_text=True) + path.read_text() + caplog.text
    assert PASSWORD not in path.read_text() + caplog.text


def test_safe_save_failure_keeps_existing_configuration(setup, monkeypatch):
    app, path, _ = setup
    client = authorize(app)
    original = path.read_bytes()
    data = form(client)
    data["display.fps"] = "19"
    def fail(*args):
        raise OSError("simulated replace failure")
    monkeypatch.setattr("robot.config.os.replace", fail)
    assert client.post("/", data=data).status_code == 503
    assert path.read_bytes() == original
    assert not list(path.parent.glob(".phos.json.*"))


def test_web_save_uses_explicit_canonical_yaml_without_rewriting_json(tmp_path):
    document = load_document()
    document["logging"]["file"] = None
    yaml_path = tmp_path / "phos.yaml"
    json_path = tmp_path / "phos.json"
    yaml_path.write_text(yaml.safe_dump(document, default_flow_style=False, sort_keys=False))
    json_path.write_text(json.dumps(document))
    app = create_app(yaml_path, active_document=document)
    app.testing = True
    client = authorize(app)
    data = form(client)
    data["display.fps"] = "19"

    assert client.post("/", data=data).status_code == 302
    assert yaml.safe_load(yaml_path.read_text())["display"]["fps"] == 19
    assert json.loads(json_path.read_text()) == document


def test_configuration_editor_renders_and_validates_yaml_without_writing(tmp_path):
    document = load_document()
    document["logging"]["file"] = None
    path = tmp_path / "phos.yaml"
    path.write_text(yaml.safe_dump(document, default_flow_style=False, sort_keys=False))
    app = create_app(path, active_document=document)
    app.testing = True
    client = authorize(app)
    response, data = editor_form(client)
    original = path.read_bytes()

    assert response.status_code == 200
    assert b"Storage format</dt><dd>YAML" in response.data
    assert b"Advanced configuration editor" in response.data
    data["action"] = "validate"
    response = client.post("/configuration/editor", data=data)

    assert response.status_code == 200
    assert b"Configuration is valid." in response.data
    assert path.read_bytes() == original


def test_configuration_editor_requires_authentication_and_csrf(setup):
    app, _, _ = setup
    assert app.test_client().get("/configuration/editor").location == "/login"
    client = authorize(app)
    assert client.post("/configuration/editor", data={"action": "validate"}).status_code == 400


def test_configuration_editor_rejects_invalid_yaml_and_stale_edits(setup):
    app, path, _ = setup
    client = authorize(app)
    _, data = editor_form(client)
    original = path.read_bytes()
    data.update(action="validate", configuration="display: [")
    response = client.post("/configuration/editor", data=data)
    assert response.status_code == 400 and b"Invalid YAML at line" in response.data
    assert path.read_bytes() == original

    _, invalid = editor_form(client)
    invalid.update(action="validate", configuration="display: {}\n")
    response = client.post("/configuration/editor", data=invalid)
    assert response.status_code == 400 and b"missing fields" in response.data
    assert path.read_bytes() == original

    _, stale = editor_form(client)
    changed = load_document(path)
    changed["display"]["fps"] = 19
    path.write_text(json.dumps(changed))
    stale["action"] = "save"
    response = client.post("/configuration/editor", data=stale)
    assert response.status_code == 400
    assert b"Configuration changed since this page was loaded" in response.data


def test_configuration_editor_saves_yaml_or_legacy_json_in_active_format(tmp_path):
    document = load_document()
    document["logging"]["file"] = None
    for index, (suffix, loader) in enumerate(((".yaml", yaml.safe_load), (".json", json.loads))):
        directory = tmp_path / str(index)
        directory.mkdir()
        path = directory / f"phos{suffix}"
        path.write_text(yaml.safe_dump(document, default_flow_style=False, sort_keys=False)
                        if suffix == ".yaml" else json.dumps(document))
        app = create_app(path, active_document=document)
        app.testing = True
        client = authorize(app)
        _, data = editor_form(client)
        edited = yaml.safe_load(data["configuration"])
        edited["display"]["fps"] = 19
        data.update(action="save", configuration=yaml.safe_dump(edited, default_flow_style=False, sort_keys=False))

        response = client.post("/configuration/editor", data=data, follow_redirects=True)

        assert response.status_code == 200
        assert b"Configuration saved successfully. Restart PHOS to apply changes." in response.data
        assert loader(path.read_text())["display"]["fps"] == 19
        assert path.read_text().lstrip().startswith("{") is (suffix == ".json")


def test_missing_active_model_can_be_repaired_in_editor(setup):
    app, path, _ = setup
    client = authorize(app)
    document = load_document(path)
    document["expression"]["enabled"] = True
    path.write_text(json.dumps(document))
    assert client.get("/").status_code == 200
    data = form(client, "expression")
    data.pop("expression.enabled")
    assert client.post("/", data=data).status_code == 302
    assert not RuntimeConfig.from_file(path).expression_enabled


def test_security_headers_and_cookie(setup):
    app, _, _ = setup
    response = app.test_client().get("/login")
    cookie = response.headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]


def test_corrupt_password_store_fails_closed_and_local_reset_bootstraps(setup):
    _, path, _ = setup
    directory = path.parent / ".phos-admin"
    (directory / "password.json").write_text("broken")
    with pytest.raises(ValueError):
        PasswordStore(directory)
    directory.rename(path.parent / "retired-admin")
    reset = PasswordStore(directory)
    assert reset.must_change and reset.verify("phos")


@pytest.mark.parametrize("key,value", [("enabled", "true"), ("port", True), ("port", 0),
                                       ("port", 65536), ("host", 123), ("host", "hostname")])
def test_web_settings_use_canonical_validation(key, value, tmp_path):
    document = load_document()
    document["web"][key] = value
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_dict(document, base_dir=tmp_path)


def test_web_server_uses_canonical_configured_host_and_port(tmp_path):
    from robot.web.server import bind_address
    document = load_document()
    document["web"].update(enabled=True, host="0.0.0.0", port=8081)
    config = RuntimeConfig.from_dict(document, base_dir=tmp_path)
    assert bind_address(config.to_dict()) == ("0.0.0.0", 8081)


def test_real_web_worker_serves_and_releases_port(setup):
    _, path, _ = setup
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    document = load_document(path)
    document["web"].update(enabled=True, host="127.0.0.1", port=port)
    config = RuntimeConfig.from_dict(document, base_dir=path.parent)
    worker = WebServer(path, config)
    with pytest.raises(RuntimeError, match="simulated runtime failure"):
        with worker:
            assert worker.process.is_alive()
            with urlopen(f"http://127.0.0.1:{port}/login", timeout=5) as response:
                assert b"Administrator login" in response.read()
            raise RuntimeError("simulated runtime failure")
    assert worker.process is None
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", port))


def test_password_save_failure_keeps_old_hash_and_session(setup, monkeypatch):
    app, path, now = setup
    client = authorize(app)
    now[0] += 61
    credential = path.parent / ".phos-admin/password.json"
    before = credential.read_bytes()
    def fail(*args):
        raise OSError("simulated credential replace failure")
    monkeypatch.setattr("robot.web.auth.os.replace", fail)
    response = post(client, "/password", {"current": PASSWORD, "new": "another long password", "confirmation": "another long password"})
    assert response.status_code == 503
    assert credential.read_bytes() == before
    assert app.extensions["phos_passwords"].verify(PASSWORD)
    assert client.get("/").status_code == 200
    assert list(credential.parent.iterdir()) == [credential]


def test_main_owns_enabled_worker_and_cleans_up_on_runtime_failure(setup, monkeypatch):
    from robot import main
    app, path, _ = setup
    document = load_document(path)
    document["web"]["enabled"] = True
    path.write_text(json.dumps(document))
    seen = []
    repositories = []
    repository_factory = main.ConfigRepository

    def make_repository(config_path):
        repository = repository_factory(config_path)
        repositories.append(repository)
        return repository

    class Worker:
        def __init__(self, repository, config):
            assert isinstance(repository, ConfigRepository)
            assert repository is repositories[0]
            assert repository.active_path == path.resolve()
            assert config.web_enabled
            self.lifecycle = None
        def __enter__(self):
            seen.append("start")
            return self
        def __exit__(self, *args):
            seen.append("stop")
    async def fail(*, config, lifecycle=None, web_server=None):
        assert seen == ["start"]
        raise RuntimeError("runtime failed")
    monkeypatch.setattr("robot.web.server.WebServer", Worker)
    monkeypatch.setattr(main, "ConfigRepository", make_repository)
    monkeypatch.setattr(main, "async_main", fail)
    monkeypatch.setattr(main.logging, "basicConfig", lambda **kw: None)
    monkeypatch.setattr("sys.argv", ["phos", "--config", str(path)])
    with pytest.raises(RuntimeError, match="runtime failed"):
        main.main()
    assert seen == ["start", "stop"]


def test_disabled_worker_does_not_spawn_or_create_credentials(tmp_path, monkeypatch):
    config = RuntimeConfig.from_file()
    monkeypatch.setattr("robot.web.server.multiprocessing.get_context", lambda *a: pytest.fail("disabled web spawned"))
    with WebServer(tmp_path / "phos.json", config) as worker:
        assert worker.process is None
    assert not (tmp_path / ".phos-admin").exists()


def test_sessions_do_not_survive_worker_restart(setup):
    app, path, _ = setup
    client = authorize(app)
    cookie = client.get_cookie("phos_admin").value
    restarted = create_app(path).test_client()
    restarted.set_cookie("phos_admin", cookie)
    assert restarted.get("/").location == "/login"
    assert login(restarted, PASSWORD).location == "/"


@pytest.mark.parametrize("background_path,status", [("/favicon.ico", 404), ("/", 302), ("/missing", 404)])
def test_browser_background_requests_preserve_login_csrf(setup, background_path, status):
    app, _, _ = setup
    client = app.test_client()
    token = csrf(client.get("/login"))
    assert client.get(background_path).status_code == status
    response = client.post("/login", data={"csrf_token": token, "password": "phos"})
    assert response.status_code == 302
    assert response.location == "/password"


def test_csrf_error_explains_recovery_without_logging_secrets(setup, caplog):
    app, _, _ = setup
    response = app.test_client().post("/login", data={"password": "secret-test-password"})
    assert response.status_code == 400
    assert b"allow cookies" in response.data
    assert "Administration CSRF rejection" in caplog.text
    assert "secret-test-password" not in caplog.text



def test_domain_pages_partition_canonical_fields_and_are_protected(setup):
    from robot.web.domains import DOMAINS, GROUPS
    from robot.web.configuration import editor_sections
    app, path, _ = setup
    anonymous = app.test_client()
    for area in DOMAINS:
        assert anonymous.get(f"/configuration/{area}").location == "/login"
    client = authorize(app)
    exposed = []
    for area in DOMAINS:
        response = client.get(f"/configuration/{area}")
        assert response.status_code == 200
        page = response.get_data(as_text=True)
        assert f'<h2>{DOMAINS[area]["title"].replace("&", "&amp;")}</h2>' in page
        assert 'aria-current="page"' in page
        controls = form(client, area)
        settings = [name for name in controls if "." in name]
        exposed.extend(settings)
        if area not in GROUPS:
            assert not settings
            assert client.post(f"/configuration/{area}", data=controls).status_code == 405
        else:
            assert client.post(f"/configuration/{area}", data={}).status_code == 400
    # Checkboxes that are off are omitted from successful form values, but still
    # need to be present exactly once in the rendered pages.
    all_html = "".join(client.get(f"/configuration/{area}").get_data(as_text=True) for area in GROUPS)
    for group in editor_sections(load_document(path)):
        for field in group["fields"]:
            assert all_html.count(f'name="{field["name"]}"') == 1
    assert len(exposed) == len(set(exposed))
    assert client.get("/configuration/not-an-area").status_code == 404


def test_each_domain_save_preserves_other_domains_and_rejects_injected_fields(setup):
    app, path, _ = setup
    client = authorize(app)
    before = load_document(path)
    data = form(client, "network")
    data["web.port"] = "8181"
    assert client.post("/configuration/network", data=data).status_code == 302
    after = load_document(path)
    expected = json.loads(json.dumps(before))
    expected["web"]["port"] = 8181
    assert after == expected
    # A Network form cannot silently modify Display or disable the web service.
    data = form(client, "network")
    data.update({"display.fps": "1", "web.enabled": "on"})
    assert client.post("/configuration/network", data=data).status_code == 400
    assert load_document(path) == after
    data = form(client, "security")
    assert client.post("/configuration/security", data=data).status_code == 302
    assert load_document(path) == after


def test_cross_domain_validation_links_to_relevant_area(setup):
    app, path, _ = setup
    client = authorize(app)
    document = load_document(path)
    document["expression"]["enabled"] = True
    document["expression"]["local"]["model_path"] = "missing.onnx"
    path.write_text(json.dumps(document))
    before = path.read_bytes()
    data = form(client, "display")
    data["display.width"] = "777"
    response = client.post("/configuration/display", data=data)
    assert response.status_code == 400
    assert b'/configuration/expression#local' in response.data
    assert b'value="777"' in response.data
    assert path.read_bytes() == before


def test_expression_groups_status_and_separate_password_page(setup, monkeypatch):
    app, _, _ = setup
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "secret-never-rendered")
    client = authorize(app)
    page = client.get("/configuration/runtime").get_data(as_text=True)
    for label in ("Provider selection", "Local ONNX provider", "AWS provider", "Cloud cost &amp; rate limits"):
        assert label in page
    assert page.count('data-provider="aws"') == 2
    assert 'data-provider="local"' in page
    assert 'name="display.width"' not in page
    status = client.get("/configuration/status").get_data(as_text=True)
    assert 'name="revision"' not in status
    security = client.get("/configuration/integrations").get_data(as_text=True)
    assert 'href="/password"' in security
    assert 'type="password"' not in security
    logging = client.get("/configuration/runtime").get_data(as_text=True)
    assert 'name="logging.level"' in logging and 'name="logging.file"' in logging
    assert 'name="logging.expression_diagnostics"' in logging
    assert "secret-never-rendered" not in page + status + security + logging


def test_domain_error_hints_match_fields_without_matching_unrelated_words():
    from robot.web.domains import error_domain
    document = load_document()
    assert error_domain(document, "display_fps: invalid number/range") == ("display", "display")
    assert error_domain(document, "expression.local.model_path: readable file required") == ("expression", "local")
    assert error_domain(document, "expression.aws: refresh_seconds must be less than cache_ttl_seconds") == ("expression", "cloud-limits")
    assert error_domain(document, "Check local filesystem permissions.") == (None, None)


@pytest.fixture
def lifecycle_setup(setup):
    from robot.lifecycle import LifecycleService
    app, path, _ = setup
    service = LifecycleService(path, RuntimeConfig.from_file(path), restart_supported=True,
                               log_level_setter=lambda level: None)
    app = create_app(path, lifecycle=service)
    app.testing = True
    return app, path, service


def test_lifecycle_routes_require_authentication_and_rotation(lifecycle_setup):
    app, _, service = lifecycle_setup
    client = app.test_client()
    for route in ("/system", "/system/restart"):
        assert client.get(route).location == "/login"
    token = csrf(client.get("/login"))
    for route in ("/system/reload", "/system/restart"):
        assert client.post(route, data={"csrf_token": token}).location == "/login"
    login(client)
    assert client.get("/system").location == "/password"
    token = csrf(client.get("/password"))
    assert client.post("/system/reload", data={"csrf_token": token}).location == "/password"
    assert service.restart_at is None


def test_restart_confirmation_and_csrf(lifecycle_setup):
    app, _, service = lifecycle_setup
    client = authorize(app)
    assert client.get("/system").status_code == 200
    assert client.post("/system/restart", data={}).status_code == 400
    token = csrf(client.get("/system"))
    assert client.post("/system/restart", data={"csrf_token": token, "confirm": "restart"}).status_code == 400
    page = client.get("/system/restart")
    parser = FormParser()
    parser.feed(page.get_data(as_text=True))
    data = parser.values
    assert service.restart_at is None  # GET never changes lifecycle.
    assert client.post("/system/restart", data=data).status_code == 400  # Checkbox required.
    page = client.get("/system/restart")
    parser = FormParser()
    parser.feed(page.get_data(as_text=True))
    data = {**parser.values, "confirm": "restart"}
    response = client.post("/system/restart", data=data)
    assert response.status_code == 202 and b"Restart requested" in response.data
    assert service.restart_at is not None
    assert client.post("/system/restart", data=data).status_code == 400  # Confirmation consumed.


def test_web_saved_iris_theme_applies_only_after_reload(lifecycle_setup):
    app, path, service = lifecycle_setup
    client = authorize(app)
    applied = []
    service.register_appearance_applier(lambda config: applied.append(config.iris_color))
    data = form(client, "display")
    data["display.iris_color"] = "green"
    assert client.post("/configuration/display", data=data).status_code == 302
    assert service.active["display"]["iris_color"] == "cyan"
    token = csrf(client.get("/system"))
    response = client.post("/system/reload", data={"csrf_token": token})
    assert response.status_code == 200
    assert b"Applied: display.iris_color" in response.data
    assert service.active["display"]["iris_color"] == "green"
    assert applied == ["green"]


def test_web_reload_reports_active_and_saved_and_rejects_commands(lifecycle_setup):
    app, path, service = lifecycle_setup
    client = authorize(app)
    document = load_document(path)
    document["logging"]["level"] = "ERROR"
    document["display"]["fps"] = 22
    path.write_text(json.dumps(document))
    token = csrf(client.get("/system"))
    assert client.post("/system/reload", data={"csrf_token": token, "command": "anything"}).status_code == 400
    assert service.active["logging"]["level"] == "INFO"
    assert client.post("/system/reload").status_code == 400
    response = client.post("/system/reload", data={"csrf_token": token})
    assert response.status_code == 200
    assert b"Applied: logging.level" in response.data and b"display.fps" in response.data
    assert service.active["logging"]["level"] == "ERROR"
    assert service.active["display"]["fps"] == 30
    status = client.get("/configuration/status")
    assert status.status_code == 200
    document["display"]["fps"] = 0
    document["logging"]["level"] = "DEBUG"
    path.write_text(json.dumps(document))
    response = client.post("/system/reload", data={"csrf_token": token})
    assert response.status_code == 400
    assert service.active["logging"]["level"] == "ERROR"


def test_real_worker_reload_reaches_parent_application(setup):
    import http.cookiejar
    import logging
    from urllib.parse import urlencode
    from urllib.request import build_opener, HTTPCookieProcessor, Request
    app, path, _ = setup
    app.extensions["phos_passwords"].change("phos", PASSWORD, PASSWORD)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    document = load_document(path)
    document["web"].update(enabled=True, port=port)
    path.write_text(json.dumps(document))
    worker = WebServer(path, RuntimeConfig.from_file(path))
    browser = build_opener(HTTPCookieProcessor(http.cookiejar.CookieJar()))
    base = f"http://127.0.0.1:{port}"
    def get(route):
        with browser.open(base + route, timeout=5) as response:
            return response.read().decode()
    def token(page):
        return re.search(r'name="csrf_token" value="([^"]+)"', page).group(1)
    def send(route, values):
        with browser.open(Request(base + route, data=urlencode(values).encode()), timeout=5) as response:
            return response.read().decode()
    original_level = logging.getLogger().level
    try:
        with worker:
            send("/login", {"csrf_token": token(get("/login")), "password": PASSWORD})
            page = get("/system")
            assert '<form action="/system/reload" method="post">' in page
            document["logging"]["level"] = "ERROR"
            path.write_text(json.dumps(document))
            response = send("/system/reload", {"csrf_token": token(page)})
            assert "Applied: logging.level" in response
            assert worker.lifecycle.active["logging"]["level"] == "ERROR"
            assert logging.getLogger().level == logging.ERROR
    finally:
        logging.getLogger().setLevel(original_level)


def test_sensors_form_validates_and_preserves_other_domains(setup):
    app, path, _ = setup
    client = authorize(app)
    original = load_document(path)
    data = form(client, "sensors")
    data.update({"sensors.environmental.type": "bmp280", "sensors.environmental.enabled": "on", "sensors.environmental.i2c_address": "0x77",
                 "sensors.environmental.poll_interval_seconds": "10",
                 "sensors.environmental.stale_after_seconds": "60"})
    assert client.post("/configuration/sensors", data=data).status_code == 302
    expected = json.loads(json.dumps(original))
    expected["sensors"]["environmental"].update(type="bmp280", enabled=True, i2c_address="0x77",
        poll_interval_seconds=10, stale_after_seconds=60)
    assert load_document(path) == expected
    for field, value in [("type", "unsupported"), ("i2c_address", "0x75"), ("poll_interval_seconds", "0"),
                         ("stale_after_seconds", "10")]:
        invalid = form(client, "sensors")
        invalid[f"sensors.environmental.{field}"] = value
        assert client.post("/configuration/sensors", data=invalid).status_code == 400
        assert load_document(path) == expected
    invalid = form(client, "sensors")
    invalid["display.fps"] = "1"
    assert client.post("/configuration/sensors", data=invalid).status_code == 400
    assert load_document(path) == expected


def test_sensor_status_panel_uses_parent_service_and_hides_stale_values(setup):
    from robot.lifecycle import LifecycleService
    app, path, _ = setup
    config = RuntimeConfig.from_file(path)
    lifecycle = LifecycleService(path, config)
    state = {"sensor_type": "bme280",
             "available_measurements": ["temperature_c", "humidity_percent", "pressure_hpa"],
             "status": "available", "available": True,
             "measurements": {"temperature_c": 22.5, "humidity_percent": 48.25,
                              "pressure_hpa": 1008.75},
             "last_update": "2026-09-23T10:00:00+00:00", "age_seconds": 2, "error": None}
    lifecycle.register_sensor_status(lambda: {"environmental": dict(state)})
    app = create_app(path, active_document=config.to_dict(), lifecycle=lifecycle)
    client = authorize(app)
    page = client.get("/configuration/sensors").get_data(as_text=True)
    for value in ("22.50 °C", "48.25 %", "1008.75 hPa", state["last_update"], "2.0 seconds"):
        assert value in page
    state.update(status="stale", available=False, measurements=None, error="No fresh reading")
    page = client.get("/configuration/sensors").get_data(as_text=True)
    assert "stale" in page and "No fresh reading" in page
    assert "22.50" not in page and "1008.75" not in page
    state.update(status="disabled", error=None)
    assert "disabled" in client.get("/configuration/sensors").get_data(as_text=True)


def test_environmental_behavior_status_uses_runtime_interpreter_state(setup):
    from robot.lifecycle import LifecycleService
    app, path, _ = setup
    config = RuntimeConfig.from_file(path)
    lifecycle = LifecycleService(path, config)
    environmental = {"sensor_type": "bmp280", "status": "available", "available": True,
                     "measurements": {"temperature_c": 28.0, "pressure_hpa": 1008.75},
                     "age_seconds": 2, "available_measurements": ["temperature_c", "pressure_hpa"],
                     "last_update": "2026-09-26T10:00:00+00:00", "error": None}
    air = {"sensor_type": "ccs811", "status": "available", "available": True,
           "measurements": {"eco2_ppm": 1300, "tvoc_ppb": 321}, "age_seconds": 3,
           "last_update": "2026-09-26T10:00:00+00:00", "compensation_input": "device_defaults", "error": None}
    lifecycle.register_sensor_status(lambda: {"environmental": environmental, "ccs811": air,
        "environmental_behavior": {"state": "warm", "reason": "temperature above warm threshold"}})
    client = authorize(create_app(path, active_document=config.to_dict(), lifecycle=lifecycle))
    page = client.get("/configuration/sensors").get_data(as_text=True)
    for text in ("Environmental State</dt><dd>WARM", "temperature above warm threshold",
                 "28.00 °C", "1008.75 hPa", "1300 ppm", "321 ppb", "2.0 seconds old", "3.0 seconds old"):
        assert text in page


def test_environmental_behavior_status_separates_warm_from_ccs811_warming_up(setup):
    from robot.lifecycle import LifecycleService
    app, path, _ = setup
    config = RuntimeConfig.from_file(path)
    lifecycle = LifecycleService(path, config)
    lifecycle.register_sensor_status(lambda: {
        "environmental": {"sensor_type": "bmp280", "status": "available", "available": True,
            "measurements": {"temperature_c": 28.18, "pressure_hpa": 995.66}, "age_seconds": 1,
            "available_measurements": ["temperature_c", "pressure_hpa"], "last_update": None, "error": None},
        "ccs811": {"sensor_type": "ccs811", "status": "warming_up", "available": False,
            "measurements": None, "age_seconds": None, "last_update": None,
            "compensation_input": None, "error": "Conditioning"},
        "environmental_behavior": {"state": "warm", "reason": "temperature above warm threshold"},
    })
    client = authorize(create_app(path, active_document=config.to_dict(), lifecycle=lifecycle))
    page = client.get("/configuration/sensors").get_data(as_text=True)
    assert "Environmental State</dt><dd>WARM" in page
    assert "temperature above warm threshold" in page
    assert "CCS811 freshness</dt><dd>warming_up" in page


def test_sensors_page_shows_shared_semantic_runtime_status(setup):
    from robot.lifecycle import LifecycleService
    app, path, _ = setup
    config = RuntimeConfig.from_file(path)
    lifecycle = LifecycleService(path, config)
    lifecycle.register_sensor_status(lambda: {"environmental_behavior": {
        "state": "warm", "reason": "temperature above warm threshold",
        "temperature_overlay": "warm", "air_quality_overlay": "none"}})
    lifecycle.register_application_status(lambda: {
        "robot": {"state": "idle", "running": True},
        "active_visual_source": "environment",
        "visual": {"expression": "curious", "motion_state": "tilt_left"},
    })
    page = authorize(create_app(path, active_document=config.to_dict(), lifecycle=lifecycle)).get(
        "/configuration/sensors").get_data(as_text=True)
    for text in ("Robot State</dt><dd>IDLE", "Active visual source</dt><dd>ENVIRONMENT",
                 "Resolved expression</dt><dd>CURIOUS", "Motion state</dt><dd>TILT LEFT",
                 "Environmental State</dt><dd>WARM", "Temperature overlay</dt><dd>WARM"):
        assert text in page


def test_bmp280_status_shows_unsupported_humidity_even_when_disabled(setup):
    from robot.lifecycle import LifecycleService
    app, path, _ = setup
    config = RuntimeConfig.from_file(path)
    lifecycle = LifecycleService(path, config)
    state = {"sensor_type": "bmp280", "available_measurements": ["temperature_c", "pressure_hpa"],
             "status": "available", "available": True,
             "measurements": {"temperature_c": 22.5, "humidity_percent": None, "pressure_hpa": 1008.75},
             "last_update": None, "age_seconds": 0, "error": None}
    lifecycle.register_sensor_status(lambda: {"environmental": state})
    client = authorize(create_app(path, active_document=config.to_dict(), lifecycle=lifecycle))
    for available in (True, False):
        state['available'] = available
        page = client.get('/configuration/sensors').get_data(as_text=True)
        assert 'Not supported' in page and 'BMP280' in page
        assert '0.00 %' not in page
        assert '<select' in page and 'name="sensors.environmental.type"' in page
        assert 'value="bme280"' in page and 'value="bmp280"' in page
        if available:
            assert '22.50 °C' in page and '1008.75 hPa' in page


def test_ccs811_form_and_status_use_shared_configuration_and_lifecycle(setup):
    from robot.lifecycle import LifecycleService
    app, path, _ = setup
    config = RuntimeConfig.from_file(path)
    lifecycle = LifecycleService(path, config)
    state = {'sensor_type': 'ccs811', 'status': 'available', 'available': True,
             'measurements': {'eco2_ppm': 1234, 'tvoc_ppb': 321},
             'last_update': '2026-09-24T12:00:00+00:00', 'age_seconds': 2,
             'compensation_input': 'environmental', 'error': None}
    lifecycle.register_sensor_status(lambda: {'ccs811': dict(state)})
    client = authorize(create_app(path, active_document=config.to_dict(), lifecycle=lifecycle))
    page = client.get('/configuration/sensors').get_data(as_text=True)
    for text in ('1234 ppm', '321 ppb', 'estimated equivalent CO2', 'not a direct NDIR',
                 'Fresh environmental temperature/humidity', state['last_update'],
                 'name="sensors.ccs811.enabled"', 'value="0x5b"'):
        assert text in page
    data = form(client, 'sensors')
    data.update({'sensors.ccs811.enabled': 'on', 'sensors.ccs811.i2c_address': '0x5b',
                 'sensors.ccs811.poll_interval_seconds': '10', 'sensors.ccs811.stale_after_seconds': '60'})
    assert client.post('/configuration/sensors', data=data).status_code == 302
    expected = config.to_dict()
    expected['sensors']['ccs811'].update(enabled=True, i2c_address='0x5b',
                                        poll_interval_seconds=10, stale_after_seconds=60)
    assert load_document(path) == expected
    assert len(lifecycle.execute('status')['restart_required']) == 4
    for field, value in [('i2c_address', '0x76'), ('poll_interval_seconds', '0'), ('stale_after_seconds', '10')]:
        invalid = form(client, 'sensors')
        invalid['sensors.ccs811.' + field] = value
        assert client.post('/configuration/sensors', data=invalid).status_code == 400
        assert load_document(path) == expected
    for status in ('warming_up', 'unavailable', 'stale', 'disabled'):
        state.update(status=status, available=False, measurements=None, error='Waiting for sensor')
        page = client.get('/configuration/sensors').get_data(as_text=True)
        assert status in page and 'Waiting for sensor' in page
        assert '1234 ppm' not in page and '321 ppb' not in page


def test_imu_form_and_status_use_shared_configuration_and_lifecycle(setup):
    from robot.lifecycle import LifecycleService
    app, path, _ = setup
    config = RuntimeConfig.from_file(path)
    lifecycle = LifecycleService(path, config)
    state = {'sensor_type': 'mpu6050', 'status': 'available', 'available': True,
             'measurements': {'acceleration_x_m_s2': 1.25, 'acceleration_y_m_s2': 2.5,
                              'acceleration_z_m_s2': -9.80665, 'angular_velocity_x_deg_s': 3,
                              'angular_velocity_y_deg_s': 4, 'angular_velocity_z_deg_s': 5},
             'last_update': '2026-09-25T12:00:00+00:00', 'age_seconds': 2,
             'calibration': 'factory_scale_only', 'motion_state': 'tilt_left',
             'tilt_direction': 'tilt_left',
             'last_motion_event': {'state': 'tilt_left', 'timestamp': '2026-09-25T11:59:59+00:00'},
             'error': None}
    lifecycle.register_sensor_status(lambda: {'imu': dict(state)})
    client = authorize(create_app(path, active_document=config.to_dict(), lifecycle=lifecycle))
    page = client.get('/configuration/sensors').get_data(as_text=True)
    for text in ('1.250 / 2.500 / -9.807 m/s²', '3.000 / 4.000 / 5.000 °/s',
                 'Factory scale only; no offset calibration', state['last_update'],
                 'Tilt Left', '2026-09-25T11:59:59+00:00',
                 'name="sensors.imu.enabled"', 'name="sensors.imu.motion.impact_threshold_m_s2"', 'value="0x69"'):
        assert text in page
    data = form(client, 'sensors')
    data.update({'sensors.imu.enabled': 'on', 'sensors.imu.i2c_address': '0x69',
                 'sensors.imu.poll_interval_seconds': '10', 'sensors.imu.stale_after_seconds': '60'})
    assert client.post('/configuration/sensors', data=data).status_code == 302
    expected = config.to_dict()
    expected['sensors']['imu'].update(enabled=True, i2c_address='0x69', poll_interval_seconds=10, stale_after_seconds=60)
    assert load_document(path) == expected
    state.update(status='stale', available=False, measurements=None, error='No fresh reading')
    page = client.get('/configuration/sensors').get_data(as_text=True)
    assert 'stale' in page and 'No fresh reading' in page
    assert '1.250 / 2.500' not in page
