"""Small server-rendered administration adapter."""
from __future__ import annotations

from collections import deque
from datetime import timedelta
from pathlib import Path
import secrets
from threading import RLock
import time

from flask import Flask, abort, flash, jsonify, redirect, render_template, request, session, url_for
from flask_wtf.csrf import CSRFError, CSRFProtect

from robot import __version__
from robot.config import ConfigurationError
from robot.web.auth import PasswordStore
from robot.web.configuration import ConfigurationService
from robot.web.domains import DOMAINS, GROUPS, domain_sections, error_domain


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
               lifecycle=None, application_service=None):
    app = Flask(__name__)
    app.config.update(SECRET_KEY=secrets.token_bytes(32), MAX_CONTENT_LENGTH=64 * 1024,
                      MAX_FORM_MEMORY_SIZE=64 * 1024, MAX_FORM_PARTS=256,
                      SESSION_COOKIE_NAME="phos_admin", SESSION_COOKIE_HTTPONLY=True,
                      SESSION_COOKIE_SAMESITE="Strict", SESSION_COOKIE_SECURE=False,
                      SESSION_REFRESH_EACH_REQUEST=False,
                      PERMANENT_SESSION_LIFETIME=timedelta(minutes=30))
    config = ConfigurationService(config_path)
    passwords = password_store or PasswordStore(config.path.parent / ".phos-admin")
    auth = AuthenticationState(clock)
    app.extensions.update(phos_auth=auth, phos_passwords=passwords, phos_config=config)
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
        if request.endpoint in {None, "login", "static"}:
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

    @app.route("/", methods=["GET", "POST"])
    @app.route("/configuration/<area>", methods=["GET", "POST"])
    def configuration(area="general"):
        aliases = {"settings": "appearance", "display": "appearance", "vision": "runtime",
                   "expression": "runtime", "logging": "runtime", "security": "integrations"}
        if area in aliases:
            return redirect(url_for("configuration", area=aliases[area]) + f"#{area}")
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
                    config.save_form(request.form, area=area)
                except ConfigurationError as exc:
                    error, status = str(exc), 400
                except OSError:
                    error, status = "Configuration could not be saved. Check local filesystem permissions.", 503
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
        runtime_state = lifecycle.execute("status") if area in {"status", "sensors", "diagnostics"} and lifecycle is not None else None
        current = runtime_state["active"] if runtime_state and runtime_state["ok"] else active_document
        related_area, error_group = error_domain(document, error) if error else (None, None)
        return render_template("configuration.html", sections=domain_sections(document, area),
                               area=area, page=DOMAINS[area], related_area=related_area,
                               error_group=error_group,
                               revision=request.form.get("revision", config.revision(document)),
                               submitted=request.form if request.method == "POST" else None,
                               error=error, config_path=config.path, document=document,
                               active=current, runtime_state=runtime_state,
                               pending=current is not None and document != current), status

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
