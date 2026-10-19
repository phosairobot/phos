"""Small server-rendered administration adapter."""
from __future__ import annotations

from collections import deque
from datetime import timedelta
import logging
from pathlib import Path
import secrets
from threading import RLock
import time

from flask import Flask, Response, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect

from robot import __version__
from robot.config import ConfigurationError
from robot.services import ApplicationError
from robot.secrets import SecretsError, SecretsService
from robot.web.auth import PasswordStore
from robot.web.configuration import ConfigurationService
from robot.web.domains import DOMAINS, GROUPS, domain_sections, error_domain
from robot.web.secrets import WebSecretsService

logger = logging.getLogger(__name__)


class AuthenticationState:
    """Bounded server-side revocation and a single-account attempt budget."""
    lifetime = 1800

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.sessions = {}
        self.attempts = deque(maxlen=5)
        self.lock = RLock()

    def allow_attempt(self):
        now = self.clock()
        while self.attempts and now - self.attempts[0] >= 60:
            self.attempts.popleft()
        if len(self.attempts) >= 5:
            return False
        self.attempts.append(now)
        return True

    def valid(self, token):
        now = self.clock()
        self.sessions = {key: expiry for key, expiry in self.sessions.items() if expiry > now}
        return token in self.sessions

    def issue(self):
        self.valid(None)
        if len(self.sessions) >= 32:
            self.sessions.pop(next(iter(self.sessions)))
        token = secrets.token_urlsafe(32)
        self.sessions[token] = self.clock() + self.lifetime
        return token


def create_app(config_path: Path, *, active_document=None, password_store=None, clock=time.monotonic,
               lifecycle=None, application_service=None, secrets_service=None):
    app = Flask(__name__)
    config = ConfigurationService(config_path)
    max_upload_bytes = config.read()["display"]["backgrounds"]["max_upload_bytes"]
    # Multipart framing is bounded separately while the canonical image limit
    # remains the service's source of truth.
    max_request_bytes = max_upload_bytes + 8 * 1024
    app.config.update(SECRET_KEY=secrets.token_bytes(32), MAX_CONTENT_LENGTH=max_request_bytes,
                      MAX_FORM_MEMORY_SIZE=max_request_bytes, MAX_FORM_PARTS=256,
                      SESSION_COOKIE_NAME="phos_admin", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=False,
                      SESSION_REFRESH_EACH_REQUEST=False,
                      PERMANENT_SESSION_LIFETIME=timedelta(minutes=30))
    passwords = password_store or PasswordStore(config.path.parent / ".phos-admin")
    web_secrets = WebSecretsService(secrets_service or SecretsService(config.path.parent / ".phos-secrets"))
    auth = AuthenticationState(clock)
    app.extensions.update(phos_auth=auth, phos_passwords=passwords, phos_config=config, phos_secrets=web_secrets,
                          )
    csrf = CSRFProtect(app)
    if application_service is not None:
        from robot.web.api import create_api
        from robot.web.openapi import load_spec
        api = create_api(application_service)
        # JSON clients do not carry the admin form CSRF token.  Network access
        # is still guarded by the surrounding administration authentication.
        csrf.exempt(api)
        app.register_blueprint(api)

        @app.get("/openapi.json")
        def openapi_document():
            return jsonify(load_spec())

        @app.get("/docs")
        def api_docs():
            response = app.response_class("""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><title>PHOS API documentation</title>
<link rel="stylesheet" href="/static/swagger-ui/swagger-ui.css"></head><body>
<div id="swagger-ui"></div><script src="/static/swagger-ui/swagger-ui-bundle.js"></script>
<script src="/static/swagger-ui/swagger-init.js"></script></body></html>""", mimetype="text/html")
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:"
            return response

    @app.context_processor
    def navigation():
        return {"domains": DOMAINS, "phos_version": __version__}

    @app.before_request
    def require_authentication():
        if request.endpoint in {None, "login", "recovery_page", "static"}:
            return None
        with auth.lock:
            if not auth.valid(session.get("sid")):
                # An unrelated browser request must not erase a login form
                # token (for example a favicon or another unauthenticated tab).
                session.pop("sid", None)
                if request.path.startswith("/api/"):
                    return jsonify({"error": {"code": "unauthorized", "message": "Authentication required.",
                                               "details": {}}}), 401
                return redirect(url_for("login"))
            if passwords.must_change and request.endpoint not in {"password", "logout"}:
                if request.path.startswith("/api/"):
                    return jsonify({"error": {"code": "forbidden", "message": "Complete the required password change first.",
                                               "details": {}}}), 403
                return redirect(url_for("password"))

    @app.after_request
    def security_headers(response):
        response.headers.update({"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
                                 "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"})
        response.headers.setdefault("Content-Security-Policy",
                                    "default-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
        return response

    @app.route("/login", methods=["GET", "POST"])
    def login():
        error = None
        status = 200
        if request.method == "POST":
            with auth.lock:
                if not auth.allow_attempt():
                    return render_template("login.html", error="Too many attempts. Wait one minute."), 429, {"Retry-After": "60"}
                if passwords.verify(request.form.get("password", "")):
                    auth.sessions.pop(session.get("sid"), None)
                    session.clear()
                    session.permanent = True
                    session["sid"] = auth.issue()
                    return redirect(url_for("password" if passwords.must_change else "configuration"))
            error, status = "Incorrect password.", 401
        return render_template("login.html", error=error), status

    @app.route("/recovery", methods=["GET", "POST"])
    def recovery_page():
        error = None
        if request.method == "POST":
            action = request.form.get("action")
            try:
                if action == "request":
                    if application_service is None:
                        raise ApplicationError("service_unavailable", "Recovery display is unavailable.", status=503)
                    application_service.request_admin_password_recovery()
                    flash("Recovery code requested. Check the PHOS display.")
                elif action == "reset":
                    if application_service is None: raise ApplicationError("service_unavailable", "Recovery service is unavailable.", status=503)
                    application_service.verify_admin_recovery_code(request.form.get("code", ""))
                    passwords.reset(request.form.get("new", ""), request.form.get("confirmation", ""))
                    auth.sessions.clear(); session.clear()
                    if application_service is not None: application_service.clear_admin_overlay()
                    logger.info("PHOS ADMIN: password recovery completed")
                    flash("Password updated. Sign in again.")
                    return redirect(url_for("login"))
                else: abort(400)
            except ValueError as exc:
                messages = {"recovery_code_invalid": "Invalid recovery code.", "recovery_code_expired": "Recovery code expired.",
                            "recovery_attempts_exceeded": "Too many failed attempts.", "recovery_rate_limited": "Recovery request rate limited.",
                            "recovery_not_active": "Invalid recovery code."}
                error = messages.get(str(exc), str(exc))
                status = 429 if str(exc) == "recovery_rate_limited" else 400
            except ApplicationError as exc:
                error, status = exc.message, exc.status
            except OSError:
                error, status = "Password could not be saved.", 503
        return render_template("recovery.html", error=error), locals().get("status", 200)

    @app.route("/password", methods=["GET", "POST"])
    def password():
        error = None
        status = 200
        if request.method == "POST":
            with auth.lock:
                # Recheck under the mutation lock: a concurrent password change
                # may have invalidated a request after before_request ran.
                if not auth.valid(session.get("sid")):
                    return redirect(url_for("login"))
                if not auth.allow_attempt():
                    return render_template("password.html", required=passwords.must_change,
                                           error="Too many attempts. Wait one minute."), 429
                try:
                    passwords.change(request.form.get("current", ""), request.form.get("new", ""),
                                     request.form.get("confirmation", ""))
                except ValueError as exc:
                    error, status = str(exc), 400
                except OSError:
                    error, status = "Password could not be saved. Check local filesystem permissions.", 503
                else:
                    auth.sessions.clear()
                    session.clear()
                    flash("Password changed. All sessions ended. Log in with your new password.")
                    return redirect(url_for("login"))
        return render_template("password.html", required=passwords.must_change, error=error), status

    @app.post("/logout")
    def logout():
        with auth.lock:
            auth.sessions.pop(session.get("sid"), None)
            session.clear()
        return redirect(url_for("login"))

    @app.route("/credentials", methods=["GET", "POST"])
    def credentials():
        error = None
        if request.method == "POST":
            action, name = request.form.get("action"), request.form.get("name")
            allowed = {"csrf_token", "action", "name", "value", "confirm_remove"}
            if set(request.form) - allowed or action not in {"set", "remove"}:
                abort(400)
            with auth.lock:
                if not auth.valid(session.get("sid")) or passwords.must_change:
                    return redirect(url_for("login"))
                try:
                    if action == "set":
                        web_secrets.set_secret(name, request.form.get("value", ""))
                        flash("Credential configured.")
                    else:
                        if request.form.get("confirm_remove") != "yes":
                            raise SecretsError("Confirm credential removal before continuing.")
                        web_secrets.remove_secret(name)
                        flash("Credential removed.")
                except (SecretsError, ValueError) as exc:
                    error = str(exc)
                except OSError:
                    logger.exception("Credential persistence failed for slot %s", name)
                    error = "Credential could not be saved. Check local filesystem permissions."
                else:
                    return redirect(url_for("credentials"))
        return render_template("credentials.html", slots=web_secrets.slots(), error=error), 400 if error else 200

    @app.route("/configuration/editor", methods=["GET", "POST"])
    def configuration_editor():
        error, valid, dirty = None, None, False
        try:
            source = config.editable_yaml()
            revision = config.revision(config.read())
        except (ConfigurationError, OSError):
            return render_template("error.html", error="Cannot load configuration. Repair the configuration file locally and reload."), 503
        if request.method == "POST":
            action = request.form.get("action")
            source = request.form.get("configuration", "")
            revision = request.form.get("revision", "")
            dirty = action == "validate"
            if set(request.form) - {"csrf_token", "action", "configuration", "revision"} or action not in {"validate", "save"}:
                abort(400)
            with auth.lock:
                if not auth.valid(session.get("sid")) or passwords.must_change:
                    return redirect(url_for("login"))
                try:
                    if action == "validate":
                        config.validate_yaml(source)
                        valid = "Configuration is valid."
                    else:
                        config.save_yaml(source, revision)
                except ConfigurationError as exc:
                    error = str(exc)
                except OSError:
                    error = "Configuration could not be saved. Check local filesystem permissions."
                else:
                    if action == "save":
                        flash("Configuration saved successfully. Restart PHOS to apply changes.")
                        return redirect(url_for("configuration_editor"))
        return render_template("configuration_editor.html", source=source, revision=revision,
                               config_path=config.path, storage_format=config.repository.format.upper(),
                               error=error, valid=valid, dirty=dirty), 400 if error else 200

    @app.post("/appearance/backgrounds/upload")
    def upload_background():
        if application_service is None:
            abort(503)
        with auth.lock:
            if not auth.valid(session.get("sid")) or passwords.must_change:
                return redirect(url_for("login"))
            uploaded = request.files.get("background")
            if uploaded is None:
                abort(400)
            try:
                application_service.upload_background({"content": uploaded.read(), "name": uploaded.filename})
            except Exception as error:
                flash(getattr(error, "message", "Background upload was rejected."))
            else:
                flash("Background uploaded.")
        return redirect(url_for("appearance_backgrounds"))

    @app.post("/appearance/backgrounds/delete")
    def delete_background():
        if application_service is None:
            abort(503)
        with auth.lock:
            if not auth.valid(session.get("sid")) or passwords.must_change:
                return redirect(url_for("login"))
            try:
                result = application_service.delete_background(request.form.get("name", ""))
                flash(f'Background "{result["name"]}" deleted. {len(result["reset_states"])} state assignments were reset to default.')
            except Exception as error:
                flash(getattr(error, "message", "Background deletion was rejected."))
        return redirect(url_for("appearance_backgrounds"))

    @app.route("/appearance/backgrounds", methods=["GET", "POST"])
    def appearance_backgrounds():
        return configuration("backgrounds")

    @app.route("/appearance/eye-safe-layer", methods=["GET", "POST"])
    def appearance_eye_safe_layer():
        error, status = None, 200
        if request.method == "POST":
            if application_service is None:
                abort(503)
            with auth.lock:
                if not auth.valid(session.get("sid")) or passwords.must_change:
                    return redirect(url_for("login"))
                mode = request.form.get("eye-safe-layer-mode", "")
                if mode not in {"subtle", "balanced", "expressive"}:
                    error, status = "Choose a supported eye safe layer mode.", 400
                else:
                    try:
                        application_service.configure_eye_safe_layer({"enabled": request.form.get("eye-safe-layer-enabled") == "on", "mode": mode})
                    except Exception as exc:
                        error, status = getattr(exc, "message", "Eye safe layer could not be applied."), 400
                    else:
                        flash("Eye safe layer saved and applied live.")
                        return redirect(url_for("appearance_eye_safe_layer"))
        try:
            document = config.read()
        except (ConfigurationError, OSError):
            return render_template("error.html", error="Cannot load configuration. Repair the configuration file locally and reload."), 503
        return render_template("eye_safe_layer.html", document=document, error=error, config_path=config.path), status

    @app.get("/appearance/preview.svg")
    def appearance_preview():
        if application_service is None:
            abort(503)
        options = {"state": request.args.get("state", "idle"), "expression": request.args.get("expression", "neutral"),
                   "eye_safe_enabled": request.args.get("eye_safe", "1") == "1",
                   "eye_safe_mode": request.args.get("eye_safe_mode", "balanced"),
                   "state_effects_enabled": request.args.get("effects", "1") == "1",
                   "skin_id": request.args.get("skin")}
        try:
            result = application_service.appearance_preview(options)
        except Exception as error:
            return Response(getattr(error, "message", "Appearance preview is unavailable."), status=400, mimetype="text/plain")
        return Response(result["svg"], mimetype="image/svg+xml")

    @app.get("/appearance/preview")
    def appearance_preview_page():
        from robot.ui.state import FaceExpression
        return render_template("appearance_preview.html", state=request.args.get("state", "idle"),
                               expression=request.args.get("expression", "neutral"),
                               eye_safe=request.args.get("eye_safe", "1") == "1",
                               eye_safe_mode=request.args.get("eye_safe_mode", "balanced"),
                               effects=request.args.get("effects", "1") == "1", skin=request.args.get("skin"),
                               expressions=[item.value for item in FaceExpression])

    @app.route("/appearance/skins", methods=["GET", "POST"])
    def appearance_skins():
        error = None
        if request.method == "POST":
            if application_service is None: abort(503)
            with auth.lock:
                if not auth.valid(session.get("sid")) or passwords.must_change: return redirect(url_for("login"))
                action, skin_id = request.form.get("action"), request.form.get("skin_id", "")
                try:
                    if action == "create": application_service.create_skin({"name": request.form.get("name", ""), "description": request.form.get("description", "")}); flash("Skin created from current appearance.")
                    elif action == "import":
                        upload = request.files.get("skin")
                        if upload is None or not upload.filename.lower().endswith((".phos-skin", ".zip")): raise ConfigurationError("Choose a Skin package (.phos-skin or .zip).")
                        application_service.import_skin({"content": upload.read()}); flash("Skin imported as a custom skin.")
                    elif action == "preview": application_service.preview_skin(skin_id); flash("Skin preview applied live temporarily. Apply to save it.")
                    elif action == "apply": application_service.apply_skin(skin_id); flash("Skin applied and saved.")
                    elif action == "delete": application_service.delete_skin(skin_id); flash("Skin deleted.")
                    else: raise ConfigurationError("Unsupported skin action.")
                    return redirect(url_for("appearance_skins"))
                except Exception as exc:
                    error = getattr(exc, "message", str(exc))
        try: skins = application_service.skins() if application_service is not None else []
        except Exception as exc: skins, error = [], getattr(exc, "message", "Skins are unavailable.")
        return render_template("skins.html", skins=skins, error=error)

    @app.get("/appearance/skins/<skin_id>/export")
    def appearance_skin_export(skin_id):
        if application_service is None: abort(503)
        try:
            content = application_service.export_skin(skin_id)["content"]
        except Exception as exc:
            return Response(getattr(exc, "message", "Skin export is unavailable."), status=400, mimetype="text/plain")
        return Response(content, mimetype="application/zip", headers={"Content-Disposition": f'attachment; filename="{skin_id}.phos-skin"'})

    @app.route("/appearance/state-effects", methods=["GET", "POST"])
    def appearance_state_effects():
        """Configure only the canonical State Effects values via the app boundary."""
        error, status = None, 200
        if request.method == "POST":
            if application_service is None:
                abort(503)
            with auth.lock:
                if not auth.valid(session.get("sid")) or passwords.must_change:
                    return redirect(url_for("login"))
                preset = request.form.get("state-effects-preset", "")
                palette = request.form.get("state-effects-palette", "")
                if preset not in {"subtle", "balanced", "expressive"} or palette not in {"phos", "cyan", "blue", "violet", "aqua", "amber", "white", "green"}:
                    error, status = "Choose supported state-effects options.", 400
                else:
                    payload = {
                        "enabled": request.form.get("state-effects-enabled") == "on",
                        "preset": preset,
                        "palette": palette,
                        "reduce_motion": request.form.get("state-effects-reduce-motion") == "on",
                    }
                    try:
                        application_service.configure_state_effects(payload)
                    except Exception as exc:
                        error, status = getattr(exc, "message", "State effects could not be applied."), 400
                    else:
                        flash("State effects saved and applied live.")
                        return redirect(url_for("appearance_state_effects"))
        try:
            document = config.read()
        except (ConfigurationError, OSError):
            return render_template("error.html", error="Cannot load configuration. Repair the configuration file locally and reload."), 503
        return render_template("state_effects.html", document=document, error=error,
                               config_path=config.path), status

    @app.route("/", methods=["GET", "POST"])
    @app.route("/configuration/<area>", methods=["GET", "POST"])
    def configuration(area="general"):
        aliases = {"settings": "appearance", "display": "appearance", "vision": "runtime",
                   "expression": "runtime", "logging": "runtime", "security": "integrations"}
        if area in aliases:
            return redirect(url_for("configuration", area=aliases[area]) + f"#{area}")
        if area == "backgrounds" and request.method == "GET" and request.path == "/configuration/backgrounds":
            return redirect(url_for("appearance_backgrounds"))
        # Keep the original save URL usable with a page's explicit area value.
        if request.method == "POST" and request.path == "/":
            area = request.form.get("area", "general")
        if area not in DOMAINS:
            abort(404)
        if request.method == "POST" and area not in GROUPS:
            abort(405)
        error, status = None, 200
        if request.method == "POST":
            with auth.lock:
                if not auth.valid(session.get("sid")) or passwords.must_change:
                    return redirect(url_for("login"))
                try:
                    if area == "backgrounds" and application_service is not None:
                        assets = {item["name"] for item in application_service.background_assets()}
                        assignments = {}
                        for state in ("idle", "listening", "thinking", "speaking", "sleeping", "error"):
                            name = request.form.get(f"background-{state}", "").strip()
                            if name and name not in assets:
                                raise ConfigurationError("Selected background asset is unavailable.")
                            assignments[state] = f"backgrounds/{name}" if name else None
                        result = application_service.configure_backgrounds({"enabled": request.form.get("backgrounds-enabled") == "on", **assignments})
                        flash("State backgrounds saved and applied live.")
                        return redirect(url_for("configuration", area=area))
                    if area == "voice" and application_service is not None:
                        prepared = config.prepare_form_update(request.form, area=area)
                        current = config.read()
                        tts_payload = dict(prepared.document["tts"])
                        tts_previous = dict(current["tts"])
                        # Audio output has its own transaction and must not
                        # rebuild the TTS provider when it alone changes.
                        tts_payload.pop("audio_output")
                        tts_previous.pop("audio_output")
                        # Each subsystem is independently transactional in the
                        # main process. Never infer a subsystem from a provider
                        # name: Cartesia is valid for both STT and TTS.
                        results = []
                        for label, method, payload, previous in (
                            ("Speech to text", application_service.configure_stt,
                             prepared.document["voice"], current["voice"]),
                            ("Text to speech", application_service.configure_tts,
                             tts_payload, tts_previous),
                            ("Audio output", application_service.configure_audio_output,
                             prepared.document["tts"]["audio_output"], current["tts"]["audio_output"]),
                        ):
                            if payload == previous:
                                continue
                            try:
                                result = method(payload)
                                results.append(f"{label}: applied ({result.get('active_provider') or 'active'})")
                            except Exception:
                                results.append(f"{label}: activation failed")
                        flash("; ".join(results) or "No Voice & Speech settings changed.")
                        return redirect(url_for("configuration", area=area))
                    if area == "eyes":
                        if application_service is None:
                            raise ApplicationError("service_unavailable", "Eyes configuration service is unavailable.", status=503)
                        prepared = config.prepare_form_update(request.form, area=area)
                        display = prepared.config.to_dict()["display"]
                        application_service.configure_eyes({
                            "iris_color": display["iris_color"],
                            "base_visual_source": display["base_visual_source"],
                            "environment_overlays_enabled": display["environment_overlays_enabled"],
                        })
                        flash("Eyes saved and applied live.")
                        return redirect(url_for("configuration", area=area))
                    config.save_form(request.form, area=area)
                except ConfigurationError as exc:
                    error, status = str(exc), 400
                except ApplicationError as exc:
                    error, status = exc.message, exc.status
                except OSError:
                    error, status = "Configuration could not be saved or applied. Check that PHOS is running and local filesystem permissions.", 503
                except Exception:
                    logger.exception("PHOS WEB: operation=appearance_eyes_update failed")
                    error, status = "Internal Server Error", 500
                else:
                    flash("Configuration saved. Use System actions to reload eligible settings, or restart PHOS for hardware and other pending changes.")
                    return redirect(url_for("configuration", area=area))
        try:
            document = config.read()
        except (ConfigurationError, OSError):
            return render_template("error.html", error="Cannot load configuration. Repair the configuration file locally and reload."), 503
        # Sensors are a read-only status view just like System / Status.  The
        # lifecycle status service owns the cross-process, provider-neutral
        # runtime snapshot; the web worker must not reach into sensor adapters.
        runtime_state = lifecycle.execute("status") if area in {"status", "sensors", "diagnostics", "voice"} and lifecycle is not None else None
        admin_network = None
        if area in {"general", "network"} and application_service is not None:
            try:
                admin_network = application_service.get_admin_network_info()
            except Exception:
                admin_network = {"hostname": "Not available", "ip_address": None, "interface": None,
                                 "admin_url": None, "status": "unavailable"}
        if area == "backgrounds":
            assets, status_view = [], {"enabled": document["display"]["backgrounds"]["enabled"],
                                       "current_state": None, "current_asset": None,
                                       "available": False, "reason": None}
            if application_service is not None:
                try:
                    assets = application_service.background_assets()
                    status_view = application_service.status().get("backgrounds", status_view)
                except Exception:
                    # The editor is a configuration view. A temporarily
                    # unavailable runtime IPC endpoint must not make its
                    # canonical persisted settings inaccessible.
                    logger.warning("Background runtime status is unavailable")
            return render_template("backgrounds.html", document=document, assets=assets, status=status_view,
                                   error=error, config_path=config.path), status
        current = runtime_state["active"] if runtime_state and runtime_state["ok"] else active_document
        related_area, error_group = error_domain(document, error) if error else (None, None)
        from robot.iris_colors import IrisColorResolver
        return render_template("configuration.html", sections=domain_sections(document, area),
                               area=area, page=DOMAINS[area], related_area=related_area,
                               error_group=error_group,
                               revision=request.form.get("revision", config.revision(document)),
                               submitted=request.form if request.method == "POST" else None,
                               error=error, config_path=config.path, document=document,
                               active=current, runtime_state=runtime_state,
                               admin_network=admin_network,
                               elevenlabs_credential_configured=any(slot["name"] == "tts.elevenlabs.api_key" and slot["configured"]
                                                                    for slot in web_secrets.slots()),
                               cartesia_credential_configured=any(slot["name"] == "tts.cartesia.api_key" and slot["configured"]
                                                                  for slot in web_secrets.slots()),
                               pending=current is not None and document != current,
                               iris_resolver=IrisColorResolver), status

    @app.post("/admin-access/show")
    def show_admin_access():
        if application_service is None:
            abort(503)
        try:
            application_service.show_admin_access_overlay()
        except ApplicationError as exc:
            flash(exc.message)
        else:
            flash("Admin access QR is shown on PHOS.")
        return redirect(url_for("configuration", area="general"))

    @app.route("/system", methods=["GET"])
    def system():
        state = lifecycle.execute("status") if lifecycle is not None else {
            "ok": False, "error": "Runtime lifecycle service is unavailable in this standalone administration instance."}
        return render_template("system.html", state=state, error=None), 200 if state["ok"] else 503

    @app.post("/system/reload")
    def reload_configuration():
        with auth.lock:
            if not auth.valid(session.get("sid")) or passwords.must_change:
                return redirect(url_for("login"))
            if set(request.form) - {"csrf_token"}:
                abort(400)
            if lifecycle is None:
                return render_template("error.html", error="Runtime lifecycle service is unavailable."), 503
            result = lifecycle.execute("reload")
            if not result["ok"]:
                return render_template("error.html", error=result["error"]), 400
            return render_template("system.html", state=result, reloaded=True, error=None)

    @app.route("/system/restart", methods=["GET", "POST"])
    def restart_phos():
        with auth.lock:
            if not auth.valid(session.get("sid")) or passwords.must_change:
                return redirect(url_for("login"))
            if lifecycle is None:
                return render_template("error.html", error="Runtime lifecycle service is unavailable."), 503
            if request.method == "GET":
                state = lifecycle.execute("status")
                if not state["ok"]:
                    return render_template("error.html", error=state["error"]), 400
                if not state["restart_supported"]:
                    return render_template("error.html", error="Restart PHOS requires the documented user systemd service."), 409
                session["restart_confirmation"] = secrets.token_urlsafe(32)
                return render_template("restart.html", error=None)
            token = session.pop("restart_confirmation", None)
            if (set(request.form) - {"csrf_token", "confirmation", "confirm"}
                    or not token or request.form.get("confirmation") != token
                    or request.form.get("confirm") != "restart"):
                return render_template("error.html", error="Open Restart PHOS and explicitly confirm the restart."), 400
            result = lifecycle.execute("restart")
            if not result["ok"]:
                return render_template("error.html", error=result["error"]), 400
            return render_template("restart.html", requested=True, error=None), 202

    @app.errorhandler(CSRFError)
    def csrf_error(error):
        # Framework-generated reasons contain no submitted credentials/tokens.
        app.logger.warning("Administration CSRF rejection: %s", error.description)
        return render_template("error.html", error=(
            "Login or form session expired or could not be verified. "
            "Reload the page, allow cookies for this address, and try again."
        )), 400

    @app.errorhandler(400)
    @app.errorhandler(413)
    def bad_request(error):
        return render_template("error.html", error="Request rejected. Reload the page and try again."), error.code

    return app
