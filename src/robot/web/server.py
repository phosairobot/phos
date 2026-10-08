"""Separate-process WSGI lifecycle; no web dependencies when disabled."""
from __future__ import annotations

import multiprocessing
import os
from threading import Event, Thread

from robot.config import ConfigRepository
from robot.lifecycle import LifecycleService
from robot.lifecycle_channel import LifecycleClient, serve_lifecycle
from pathlib import Path


def bind_address(active_document):
    """Return the already-validated canonical Web Admin/API listener address."""
    web = active_document["web"]
    return web["host"], web["port"]


def _serve(path, active, connection, lifecycle_connection):
    server = None
    try:
        from waitress import create_server
        from robot.web.app import create_app
        from robot.services import RemoteApplicationService
        lifecycle = LifecycleClient(lifecycle_connection)
        app = create_app(Path(path), active_document=active, lifecycle=lifecycle,
                         application_service=RemoteApplicationService(lifecycle))
        host, port = bind_address(active)
        server = create_server(app, host=host, port=port,
                               threads=2, connection_limit=32, channel_timeout=30,
                               max_request_body_size=64 * 1024, max_request_header_size=8192,
                               expose_tracebacks=False, clear_untrusted_proxy_headers=True)
        connection.send(None)
        connection.close()
        server.run()
    except Exception as error:
        # Send only type information: exception messages may contain local data.
        try:
            connection.send(type(error).__name__)
        except (OSError, EOFError):
            pass
    finally:
        lifecycle_connection.close()
        connection.close()
        if server is not None:
            server.close()


class WebServer:
    """Own one isolated worker and always release its port on PHOS exit.

    Termination can interrupt a request; both configuration and credential writes
    use atomic replacement. Sessions and rate limits intentionally reset on restart.
    """
    def __init__(self, config_repository, config):
        self.config_repository = (config_repository if isinstance(config_repository, ConfigRepository)
                                  else ConfigRepository(config_repository))
        self.path = str(self.config_repository.active_path)
        self.config = config
        self.process = None
        self.lifecycle = LifecycleService(
            self.config_repository, config, restart_supported=(
                os.environ.get("PHOS_SERVICE_MANAGED") == "1" and bool(os.environ.get("INVOCATION_ID"))))
        self._stop = Event()
        self._thread = None
        self._channel = None

    def __enter__(self):
        if not self.config.web_enabled:
            return self
        context = multiprocessing.get_context("spawn")
        reader, writer = context.Pipe(duplex=False)
        self._channel, worker_channel = context.Pipe()
        self.process = context.Process(target=_serve, args=(self.path, self.config.to_dict(), writer, worker_channel),
                                       name="phos-admin", daemon=True)
        try:
            self.process.start()
            worker_channel.close()
            self._thread = Thread(target=serve_lifecycle, args=(self._channel, self.lifecycle, self._stop),
                                  name="phos-lifecycle", daemon=True)
            self._thread.start()
            writer.close()
            if not reader.poll(30):
                raise RuntimeError("Web administration startup timed out")
            error = reader.recv()
            if error is not None:
                raise RuntimeError(f"Web administration failed ({error}); check web dependencies, port and .phos-admin permissions")
        except BaseException:
            self.__exit__(None, None, None)
            raise
        finally:
            reader.close()
            writer.close()
            worker_channel.close()
        return self

    def __exit__(self, *_):
        self._stop.set()
        if self.process is not None and self.process.pid is not None:
            if self.process.is_alive():
                self.process.terminate()
            self.process.join(timeout=5)
            if self.process.is_alive():
                self.process.kill()
                self.process.join(timeout=5)
            self.process.close()
            self.process = None
        if self._thread is not None:
            self._thread.join(timeout=2)
        if self._channel is not None and (self._thread is None or not self._thread.is_alive()):
            self._channel.close()
