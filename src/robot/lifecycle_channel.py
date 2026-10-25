"""Bounded local process channel for application lifecycle operations."""
import logging
from threading import Lock


logger = logging.getLogger(__name__)


class LifecycleClient:
    def __init__(self, connection):
        self.connection = connection
        self.lock = Lock()
        self.available = True

    def execute(self, operation, payload=None):
        request = ({"operation": operation, "payload": payload}
                   if isinstance(operation, str) and operation.startswith("application.")
                   else operation if payload is None else {"operation": operation, "payload": payload})
        allowed = {"status", "reload", "restart"}
        application = {"application.status", "application.state", "application.environment", "application.motion",
                       "application.presence", "application.attention", "application.observed_expression", "application.health", "application.capabilities", "application.config", "application.update_config", "application.expression",
                       "application.set_state", "application.visual_source", "application.signal_user", "application.overlay", "application.set_overlay", "application.clear_overlay", "application.voice", "application.start_listening", "application.stop_listening", "application.cancel_voice_session", "application.speak"}
        application.update({"application.configure_stt", "application.configure_tts", "application.configure_audio_output", "application.configure_backgrounds", "application.configure_eyes", "application.configure_eye_safe_layer", "application.appearance_preview", "application.configure_state_effects", "application.background_assets", "application.upload_background", "application.delete_background", "application.skins", "application.create_skin", "application.delete_skin", "application.preview_skin", "application.apply_skin", "application.import_skin", "application.export_skin"})
        application.update({"application.request_admin_password_recovery", "application.verify_admin_recovery_code", "application.clear_admin_overlay", "application.get_admin_network_info", "application.show_admin_access_overlay"})
        application.update({"application.controller_status", "application.controller_serial_ports", "application.controller_recent_messages", "application.clear_controller_recent_messages"})
        if not ((isinstance(request, str) and request in allowed)
                or (isinstance(request, dict) and request.get("operation") in application)):
            return {"ok": False, "error": "Unsupported lifecycle operation."}
        with self.lock:
            if not self.available:
                return self._unavailable()
            try:
                self.connection.send(request)
                if not self.connection.poll(5):
                    # Retiring the channel avoids interpreting a late reply as
                    # the response to a different operation.
                    self.available = False
                    return self._unavailable()
                return self.connection.recv()
            except (OSError, EOFError):
                self.available = False
                return self._unavailable()

    @staticmethod
    def _unavailable():
        return {"ok": False, "error": "Runtime lifecycle service is unavailable. An in-flight request may have completed; check PHOS locally before retrying."}


def serve_lifecycle(connection, service, stop, ready=None):
    try:
        if ready is not None:
            ready.set()
        while not stop.is_set():
            if connection.poll(.2):
                try:
                    operation = connection.recv()
                except (EOFError, OSError):
                    return
                try:
                    result = service.execute(operation)
                except Exception:
                    logger.exception("Lifecycle dispatcher failed")
                    result = {"ok": False, "error": "PHOS could not process the lifecycle request.", "status": 500}
                try:
                    connection.send(result)
                except (EOFError, OSError):
                    return
    except (EOFError, OSError):
        pass
    finally:
        connection.close()
