"""Versioned JSON adapter for :class:`PhosApplicationService`."""
from __future__ import annotations

from flask import Blueprint, jsonify, request, Response
import json
from queue import Empty, Queue

from robot.services import ApplicationError


def create_api(service):
    api = Blueprint("api_v1", __name__, url_prefix="/api/v1")

    def body():
        value = request.get_json(silent=True)
        if not isinstance(value, dict):
            raise ApplicationError("invalid_request", "A JSON object is required.")
        return value

    @api.errorhandler(ApplicationError)
    def application_error(error):
        return jsonify(error.document()), error.status

    @api.errorhandler(Exception)
    def internal_error(error):
        # Flask logs the exception; clients receive only a stable error shape.
        return jsonify({"error": {"code": "internal_error", "message": "PHOS could not process the request.", "details": {}}}), 500

    @api.get("/status")
    def status(): return jsonify(service.status())
    @api.get("/state")
    def state(): return jsonify(service.robot_state())
    @api.get("/environment")
    def environment(): return jsonify(service.environment())
    @api.get("/motion")
    def motion(): return jsonify(service.motion())
    @api.get("/presence")
    def presence(): return jsonify(service.presence())
    @api.get("/attention")
    def attention(): return jsonify(service.attention())
    @api.get("/voice")
    def voice(): return jsonify(service.voice())
    @api.post("/voice/listen")
    def listen(): return jsonify(service.start_listening())
    @api.post("/voice/stop")
    def stop_listening(): return jsonify(service.stop_listening())
    @api.post("/voice/cancel")
    def cancel_voice(): return jsonify(service.cancel_voice_session())
    @api.get("/observed-expression")
    def observed_expression(): return jsonify(service.observed_expression())
    @api.get("/health")
    def health(): return jsonify(service.health())
    @api.get("/capabilities")
    def capabilities(): return jsonify(service.capabilities())
    @api.get("/overlay")
    def overlay(): return jsonify(service.overlay())
    @api.post("/overlay")
    def set_overlay(): return jsonify(service.set_overlay(body()))
    @api.delete("/overlay")
    def clear_overlay(): return jsonify(service.clear_overlay())
    @api.get("/config")
    def config(): return jsonify(service.config())
    @api.patch("/config")
    def update_config(): return jsonify(service.update_config(body()))
    @api.post("/expression")
    def expression(): return jsonify(service.set_expression(body().get("expression")))
    @api.post("/state")
    def state_command(): return jsonify(service.set_state(body().get("state")))
    @api.post("/visual-source")
    def visual_source(): return jsonify(service.set_visual_source(body().get("source")))

    @api.get("/events")
    def events():
        """Low-rate semantic event fallback for WSGI deployments.

        Waitress is WSGI and cannot safely upgrade connections to WebSocket.
        The same event contract is therefore exposed as SSE here; deployments
        needing a true WebSocket attach a small ASGI/WebSocket adapter to this
        application service, not to hardware.  This preserves the contract and
        keeps the Pi's default process lightweight.
        """
        queue = Queue(maxsize=16)
        unsubscribe = service.subscribe(lambda event: _offer(queue, event))
        def stream():
            try:
                service.emit_snapshot_changes()
                while True:
                    try:
                        event = queue.get(timeout=.25)
                        # Application-service values are JSON-safe.  Keep
                        # this strict as a final adapter guard: browsers must
                        # never receive Python's non-standard NaN/Infinity.
                        yield "event: %s\ndata: %s\n\n" % (event["type"], json.dumps(event, separators=(",", ":"), allow_nan=False))
                    except Empty:
                        # A separate-process WSGI worker can only ask its
                        # parent for a bounded snapshot at this keepalive
                        # boundary.  Direct in-process services simply emit
                        # their already-published semantic changes here.
                        # Runtime and WSGI worker are separate processes. A
                        # short transient reaction must cross this bounded
                        # snapshot bridge before it completes.
                        service.emit_snapshot_changes()
                        yield ": keepalive\n\n"
            finally:
                unsubscribe()
        return Response(stream(), mimetype="text/event-stream", headers={"Cache-Control": "no-store"})

    return api


def _offer(queue, event):
    try:
        queue.put_nowait(event)
    except Exception:
        # A slow remote client is isolated; the next meaningful event wins.
        pass
