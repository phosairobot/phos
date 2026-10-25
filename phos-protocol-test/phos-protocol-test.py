#!/usr/bin/env python3
"""
PHOS Hardware Controller Interactive Tester

Standalone interactive tester for the controller-neutral `phos-hw` v1 protocol.

Features:
- Lists serial ports
- Connects/disconnects without PHOS
- Reads JSON Lines continuously in a background thread
- Handles ESP32 boot garbage before the first JSON object
- Sends ping and request_capabilities
- Sends arbitrary protocol commands
- Sends raw JSON objects
- Shows sensor_update / heartbeat / capabilities / errors
- Optional automatic heartbeat ping loop

Dependency:
    python -m pip install pyserial

Examples:
    python phos_hw_tester.py
    python phos_hw_tester.py --port /dev/ttyUSB0
    python phos_hw_tester.py --port /dev/cu.usbserial-0001 --baud 115200
"""

from __future__ import annotations

import argparse
import json
import queue
import shlex
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from typing import Any

try:
    import serial
    from serial.tools import list_ports
except ImportError:
    print("ERROR: pyserial is not installed.")
    print("Install it with: python -m pip install pyserial")
    raise SystemExit(2)


PROTOCOL = "phos-hw"
VERSION = 1
DEFAULT_BAUD = 115200
READ_TIMEOUT = 0.20
MAX_FRAME_SIZE = 8 * 1024
DEFAULT_HIDDEN_TYPES = frozenset({"orientation_update", "heartbeat"})
QUIET_HIDDEN_TYPES = frozenset({"orientation_update", "heartbeat", "sensor_update", "motion_update", "controller_status"})


def now_string() -> str:
    return time.strftime("%H:%M:%S")


def compact_json(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False)


def pretty_json(value: Any) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False, sort_keys=False)


def list_serial_ports() -> list[str]:
    ports = list(list_ports.comports())

    if not ports:
        print("No serial ports found.")
        return []

    print("\nAvailable serial ports:")
    for index, port in enumerate(ports, start=1):
        vid_pid = ""
        if port.vid is not None and port.pid is not None:
            vid_pid = f" VID:PID={port.vid:04X}:{port.pid:04X}"

        serial_number = f" serial={port.serial_number}" if port.serial_number else ""

        print(
            f"  {index}. {port.device}"
            f" | {port.description or 'n/a'}"
            f"{vid_pid}{serial_number}"
        )

    print()
    return [port.device for port in ports]


@dataclass
class ReceivedMessage:
    raw: str
    document: dict[str, Any]


class SerialSession:
    def __init__(self, baud_rate: int = DEFAULT_BAUD) -> None:
        self.baud_rate = baud_rate
        self.device: str | None = None

        self._serial: serial.Serial | None = None
        self._reader_thread: threading.Thread | None = None
        self._heartbeat_thread: threading.Thread | None = None

        self._stop_event = threading.Event()
        self._heartbeat_stop = threading.Event()
        self._write_lock = threading.Lock()
        self._messages: queue.Queue[ReceivedMessage] = queue.Queue()

        self._heartbeat_interval = 0.0
        self._rx_count = 0
        self._tx_count = 0
        self._invalid_count = 0
        self._last_rx_monotonic: float | None = None
        self._visibility_lock = threading.Lock()
        self._hidden_types = set(DEFAULT_HIDDEN_TYPES)

    @property
    def connected(self) -> bool:
        return self._serial is not None and self._serial.is_open

    def connect(self, device: str, baud_rate: int | None = None) -> None:
        if self.connected:
            self.disconnect()

        if baud_rate is not None:
            self.baud_rate = baud_rate

        print(f"Opening {device} at {self.baud_rate} baud...")

        ser = serial.Serial(
            port=device,
            baudrate=self.baud_rate,
            timeout=READ_TIMEOUT,
            write_timeout=1.0,
        )

        self.device = device
        self._serial = ser
        self._stop_event.clear()

        self._reader_thread = threading.Thread(
            target=self._reader_loop,
            name="phos-hw-reader",
            daemon=True,
        )
        self._reader_thread.start()

        print(f"Connected to {device}.")
        print("ESP32 may reset when the serial port is opened; boot output is tolerated.")

    def disconnect(self) -> None:
        self.stop_auto_ping()

        self._stop_event.set()

        ser, self._serial = self._serial, None

        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass

        if self._reader_thread is not None:
            self._reader_thread.join(timeout=1.0)
            self._reader_thread = None

        if self.device:
            print(f"Disconnected from {self.device}.")

        self.device = None

    def send_document(self, document: dict[str, Any]) -> None:
        if not self.connected or self._serial is None:
            print("ERROR: not connected.")
            return

        raw = compact_json(document) + "\n"
        data = raw.encode("utf-8")

        if len(data) > MAX_FRAME_SIZE:
            print(
                f"WARNING: outgoing frame is {len(data)} bytes "
                f"(protocol maximum is {MAX_FRAME_SIZE})."
            )

        try:
            with self._write_lock:
                self._serial.write(data)
                self._serial.flush()
            self._tx_count += 1
            print(f"[{now_string()}] TX {raw.rstrip()}")
        except Exception as exc:
            print(f"ERROR while writing serial data: {type(exc).__name__}: {exc}")

    def send_command(
        self,
        kind: str,
        payload: Any | None = None,
        *,
        message_id: str | None = None,
        include_id: bool = True,
    ) -> None:
        document: dict[str, Any] = {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": kind,
            "payload": {} if payload is None else payload,
        }

        if include_id:
            document["id"] = message_id or f"test-{uuid.uuid4().hex[:8]}"

        self.send_document(document)

    def wait_for_type(self, kind: str, timeout: float = 3.0) -> ReceivedMessage | None:
        deadline = time.monotonic() + timeout
        pending: list[ReceivedMessage] = []

        try:
            while time.monotonic() < deadline:
                remaining = max(0.0, deadline - time.monotonic())
                try:
                    message = self._messages.get(timeout=min(0.2, remaining))
                except queue.Empty:
                    continue

                if message.document.get("type") == kind:
                    for item in pending:
                        self._messages.put(item)
                    return message

                pending.append(message)
        finally:
            for item in pending:
                self._messages.put(item)

        return None

    def start_auto_ping(self, interval: float) -> None:
        if interval <= 0:
            print("ERROR: interval must be greater than zero.")
            return

        self.stop_auto_ping()

        self._heartbeat_interval = interval
        self._heartbeat_stop.clear()

        self._heartbeat_thread = threading.Thread(
            target=self._auto_ping_loop,
            name="phos-hw-auto-ping",
            daemon=True,
        )
        self._heartbeat_thread.start()

        print(f"Automatic ping enabled every {interval:g} seconds.")

    def stop_auto_ping(self) -> None:
        self._heartbeat_stop.set()

        if self._heartbeat_thread is not None:
            self._heartbeat_thread.join(timeout=1.0)
            self._heartbeat_thread = None

    def status(self) -> None:
        print()

    def visible_types(self) -> tuple[str, ...]:
        with self._visibility_lock:
            return tuple(sorted(self._hidden_types))

    def show_type(self, kind: str) -> None:
        with self._visibility_lock:
            self._hidden_types.discard(kind)

    def hide_type(self, kind: str) -> None:
        with self._visibility_lock:
            self._hidden_types.add(kind)

    def show_all(self) -> None:
        with self._visibility_lock:
            self._hidden_types.clear()

    def set_quiet(self, enabled: bool) -> None:
        with self._visibility_lock:
            self._hidden_types = set(QUIET_HIDDEN_TYPES if enabled else DEFAULT_HIDDEN_TYPES)

    def is_visible(self, kind: str) -> bool:
        with self._visibility_lock:
            return kind not in self._hidden_types
        print(f"Connected : {self.connected}")
        print(f"Device    : {self.device or '-'}")
        print(f"Baud      : {self.baud_rate}")
        print(f"TX frames : {self._tx_count}")
        print(f"RX frames : {self._rx_count}")
        print(f"Invalid RX: {self._invalid_count}")

        if self._last_rx_monotonic is None:
            print("Last RX   : never")
        else:
            age = time.monotonic() - self._last_rx_monotonic
            print(f"Last RX   : {age:.2f}s ago")

        if self._heartbeat_thread is None:
            print("Auto ping : off")
        else:
            print(f"Auto ping : every {self._heartbeat_interval:g}s")
        print()

    def _auto_ping_loop(self) -> None:
        while not self._heartbeat_stop.wait(self._heartbeat_interval):
            if self.connected:
                self.send_command("ping", {})

    def _reader_loop(self) -> None:
        buffer = bytearray()

        while not self._stop_event.is_set():
            ser = self._serial
            if ser is None:
                return

            try:
                chunk = ser.read(1024)
            except Exception as exc:
                if not self._stop_event.is_set():
                    print(
                        f"\n[{now_string()}] SERIAL READ ERROR "
                        f"{type(exc).__name__}: {exc}"
                    )
                return

            if not chunk:
                continue

            buffer.extend(chunk)

            # Bound memory if a device sends garbage without newlines.
            if len(buffer) > MAX_FRAME_SIZE * 2 and b"\n" not in buffer:
                print(
                    f"\n[{now_string()}] RX ERROR oversized/unframed input; "
                    "discarding buffer"
                )
                self._invalid_count += 1
                buffer.clear()
                continue

            while b"\n" in buffer:
                raw_line, _, remainder = buffer.partition(b"\n")
                buffer = bytearray(remainder)

                if not raw_line.strip():
                    continue

                self._handle_line(bytes(raw_line))

    def _handle_line(self, line: bytes) -> None:
        # ESP32 ROM/bootloader output may contain non-UTF-8 bytes.
        text = line.decode("utf-8", errors="replace").strip()

        # If boot text is prepended to a JSON line, keep only the JSON portion.
        json_start = text.find("{")
        if json_start < 0:
            print(f"\n[{now_string()}] RX NON-JSON {text!r}")
            self._invalid_count += 1
            self._print_prompt_hint()
            return

        if json_start > 0:
            prefix = text[:json_start]
            print(f"\n[{now_string()}] RX BOOT/NOISE {prefix!r}")
            text = text[json_start:]

        try:
            document = json.loads(text)
        except json.JSONDecodeError as exc:
            print(f"\n[{now_string()}] RX INVALID JSON {text!r}")
            print(f"  {exc}")
            self._invalid_count += 1
            self._print_prompt_hint()
            return

        if not isinstance(document, dict):
            print(f"\n[{now_string()}] RX INVALID DOCUMENT {document!r}")
            self._invalid_count += 1
            self._print_prompt_hint()
            return

        self._rx_count += 1
        self._last_rx_monotonic = time.monotonic()

        message = ReceivedMessage(raw=text, document=document)
        self._messages.put(message)

        kind = document.get("type", "?")
        payload = document.get("payload")

        # Hidden frames remain decoded, counted and available to `wait`; only
        # their console rendering is suppressed so serial input stays drained.
        if not self.is_visible(kind):
            return

        print(f"\n[{now_string()}] RX type={kind}")
        print(pretty_json(document))

        if kind == "sensor_update":
            print("  -> pushed sensor event")
        elif kind == "capabilities":
            print("  -> controller capabilities")
        elif kind == "hello":
            print("  -> controller hello")
        elif kind == "error":
            print("  -> protocol error")

        self._print_prompt_hint()

    @staticmethod
    def _print_prompt_hint() -> None:
        # Reader output can interrupt input(); repainting a simple prompt marker
        # makes interactive use easier without requiring curses/readline tricks.
        print("phos-hw> ", end="", flush=True)


HELP = """
Commands:

  help
      Show this help.

  ports
      List serial ports.

  connect <device> [baud]
      Connect to a serial device.
      Example:
        connect /dev/ttyUSB0
        connect /dev/cu.usbserial-0001 115200

  disconnect
      Close the current connection.

  status
      Show connection and protocol statistics.

  ping
      Send a phos-hw v1 ping.

  capabilities
      Send request_capabilities.

  send <type> [JSON-payload]
      Send an arbitrary phos-hw command.
      Examples:
        send ping {}
        send request_capabilities {}
        send configure_sensor_reporting {"enabled":true}
        send set_led_animation {"animation":"solid","color":"#FF0000","brightness":1.0,"duration_ms":0}
        send clear_led {}

  send-no-id <type> [JSON-payload]
      Same as send, but omit the optional test id field.

  raw <JSON-document>
      Send an entire JSON object exactly as supplied.
      Example:
        raw {"protocol":"phos-hw","version":1,"type":"ping","payload":{}}

  malformed
      Send deliberately malformed JSON to verify firmware recovery.

  fragmented <type> [JSON-payload]
      Send one valid JSON-line frame in three fragments.

  multi
      Send ping + request_capabilities in one serial write.

  autopING <seconds>
      Send ping automatically at a fixed interval.
      Example:
        autoping 5

  autoping off
      Stop automatic ping.

  wait <type> [seconds]
      Wait for a received message of the specified type.
      Example:
        wait heartbeat 3
        wait capabilities 5

  filters / show
      Show currently hidden RX message types.

  show all | show <type>
      Show all received message types, or one previously hidden type.

  hide <type>
      Hide one received message type from console rendering.

  quiet on | quiet off
      Quiet mode hides routine telemetry. It still shows acknowledgements,
      errors, capabilities and other command responses.

  quit / exit
      Disconnect and exit.

Notes:
- orientation_update and heartbeat are hidden by default but still consumed.
- ESP32 boot garbage is tolerated.
- Command payloads must match the actual `controller_protocol.py` contract.
"""


class InteractiveTester:
    def __init__(self, session: SerialSession) -> None:
        self.session = session

    def run(self) -> None:
        print("PHOS Hardware Controller Interactive Tester")
        print(f"Protocol: {PROTOCOL} v{VERSION}")
        print("Type 'help' for commands.\n")

        while True:
            try:
                line = input("phos-hw> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break

            if not line:
                continue

            try:
                should_exit = self.handle(line)
            except Exception as exc:
                print(f"ERROR: {type(exc).__name__}: {exc}")
                continue

            if should_exit:
                break

        self.session.disconnect()

    def handle(self, line: str) -> bool:
        command, _, rest = line.partition(" ")
        cmd = command.lower()
        rest = rest.strip()

        if cmd in {"quit", "exit"}:
            return True

        if cmd == "help":
            print(HELP)
            return False

        if cmd == "ports":
            list_serial_ports()
            return False

        if cmd == "connect":
            self._connect(rest)
            return False

        if cmd == "disconnect":
            self.session.disconnect()
            return False

        if cmd == "status":
            self.session.status()
            return False

        if cmd in {"filters", "show", "hide", "quiet"}:
            self._filters(cmd, rest)
            return False

        if cmd == "ping":
            self.session.send_command("ping", {})
            return False

        if cmd == "capabilities":
            self.session.send_command("request_capabilities", {})
            return False

        if cmd == "send":
            self._send(rest, include_id=True)
            return False

        if cmd == "send-no-id":
            self._send(rest, include_id=False)
            return False

        if cmd == "raw":
            self._raw(rest)
            return False

        if cmd == "malformed":
            self._malformed()
            return False

        if cmd == "fragmented":
            self._fragmented(rest)
            return False

        if cmd == "multi":
            self._multi()
            return False

        if cmd == "autoping":
            self._autoping(rest)
            return False

        if cmd == "wait":
            self._wait(rest)
            return False

        print(f"Unknown command: {command!r}. Type 'help'.")
        return False

    def _filters(self, cmd: str, rest: str) -> None:
        if cmd == "filters" or (cmd == "show" and not rest):
            hidden = self.session.visible_types()
            print("Hidden RX types: " + (", ".join(hidden) if hidden else "none"))
            return
        if cmd == "show":
            if rest == "all":
                self.session.show_all()
                print("All RX message types are visible.")
            else:
                self.session.show_type(rest)
                print(f"Showing RX type={rest}.")
            return
        if cmd == "hide":
            if not rest:
                print("Usage: hide <type>")
                return
            self.session.hide_type(rest)
            print(f"Hiding RX type={rest}.")
            return
        if rest not in {"on", "off"}:
            print("Usage: quiet on | quiet off")
            return
        self.session.set_quiet(rest == "on")
        print(f"Quiet mode {rest}.")

    def _connect(self, rest: str) -> None:
        if not rest:
            ports = list_serial_ports()
            if len(ports) == 1:
                self.session.connect(ports[0])
            else:
                print("Usage: connect <device> [baud]")
            return

        args = shlex.split(rest)
        device = args[0]
        baud = int(args[1]) if len(args) > 1 else self.session.baud_rate
        self.session.connect(device, baud)

    def _send(self, rest: str, *, include_id: bool) -> None:
        if not rest:
            print("Usage: send <type> [JSON-payload]")
            return

        kind, _, payload_text = rest.partition(" ")
        payload_text = payload_text.strip()

        if payload_text:
            payload = json.loads(payload_text)
        else:
            payload = {}

        self.session.send_command(kind, payload, include_id=include_id)

    def _raw(self, rest: str) -> None:
        if not rest:
            print("Usage: raw <JSON-document>")
            return

        document = json.loads(rest)
        if not isinstance(document, dict):
            print("ERROR: raw document must be a JSON object.")
            return

        self.session.send_document(document)

    def _malformed(self) -> None:
        if not self.session.connected or self.session._serial is None:
            print("ERROR: not connected.")
            return

        data = b'{"protocol":"phos-hw","version":1,"type":"ping",BROKEN}\n'
        with self.session._write_lock:
            self.session._serial.write(data)
            self.session._serial.flush()

        print(f"[{now_string()}] TX MALFORMED {data!r}")

    def _fragmented(self, rest: str) -> None:
        if not self.session.connected or self.session._serial is None:
            print("ERROR: not connected.")
            return

        if not rest:
            print("Usage: fragmented <type> [JSON-payload]")
            return

        kind, _, payload_text = rest.partition(" ")
        payload = json.loads(payload_text) if payload_text.strip() else {}

        document = {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": kind,
            "id": f"fragment-{uuid.uuid4().hex[:8]}",
            "payload": payload,
        }

        data = (compact_json(document) + "\n").encode("utf-8")

        first = max(1, len(data) // 3)
        second = max(first + 1, (len(data) * 2) // 3)
        chunks = [data[:first], data[first:second], data[second:]]

        print(f"[{now_string()}] TX FRAGMENTED {compact_json(document)}")

        with self.session._write_lock:
            for index, chunk in enumerate(chunks, start=1):
                print(f"  fragment {index}: {chunk!r}")
                self.session._serial.write(chunk)
                self.session._serial.flush()
                time.sleep(0.1)

    def _multi(self) -> None:
        if not self.session.connected or self.session._serial is None:
            print("ERROR: not connected.")
            return

        ping = {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": "ping",
            "id": f"multi-{uuid.uuid4().hex[:8]}",
            "payload": {},
        }
        caps = {
            "protocol": PROTOCOL,
            "version": VERSION,
            "type": "request_capabilities",
            "id": f"multi-{uuid.uuid4().hex[:8]}",
            "payload": {},
        }

        raw = compact_json(ping) + "\n" + compact_json(caps) + "\n"
        data = raw.encode("utf-8")

        with self.session._write_lock:
            self.session._serial.write(data)
            self.session._serial.flush()

        print(f"[{now_string()}] TX MULTI")
        print(raw, end="")

    def _autoping(self, rest: str) -> None:
        if not rest:
            print("Usage: autoping <seconds> | autoping off")
            return

        if rest.lower() == "off":
            self.session.stop_auto_ping()
            print("Automatic ping disabled.")
            return

        self.session.start_auto_ping(float(rest))

    def _wait(self, rest: str) -> None:
        args = shlex.split(rest)

        if not args:
            print("Usage: wait <type> [seconds]")
            return

        kind = args[0]
        timeout = float(args[1]) if len(args) > 1 else 3.0

        print(f"Waiting up to {timeout:g}s for type={kind!r}...")
        message = self.session.wait_for_type(kind, timeout)

        if message is None:
            print("Timeout.")
            return

        print("Matched:")
        print(pretty_json(message.document))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive tester for the PHOS phos-hw v1 serial protocol."
    )
    parser.add_argument(
        "--port",
        help="Serial device, e.g. /dev/ttyUSB0 or /dev/cu.usbserial-0001",
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD,
        help=f"Serial baud rate (default: {DEFAULT_BAUD})",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    session = SerialSession(args.baud)

    if args.port:
        try:
            session.connect(args.port, args.baud)
        except Exception as exc:
            print(f"Could not connect to {args.port}: {type(exc).__name__}: {exc}")

    try:
        InteractiveTester(session).run()
    finally:
        session.disconnect()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
