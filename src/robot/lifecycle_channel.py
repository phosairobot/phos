"""Bounded local process channel for application lifecycle operations."""
from threading import Lock


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
                       "application.set_state", "application.visual_source", "application.overlay", "application.set_overlay", "application.clear_overlay", "application.voice", "application.start_listening", "application.stop_listening", "application.cancel_voice_session", "application.speak"}
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


def serve_lifecycle(connection, service, stop):
    try:
        while not stop.is_set():
            if connection.poll(.2):
                operation = connection.recv()
                result = service.execute(operation)
                connection.send(result)
    except (EOFError, OSError):
        pass
    finally:
        connection.close()
