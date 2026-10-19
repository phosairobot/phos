"""Ephemeral local-admin recovery, LAN discovery, and QR matrix helpers."""
from __future__ import annotations

from dataclasses import dataclass, field
import ipaddress
import socket
import secrets
import time
import logging

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AdminOverlayState:
    """Ephemeral, local-only content for the display's administrative layer.

    This is deliberately not a lifecycle document or a configuration model.
    In particular, the recovery code is excluded from ``repr`` so routine
    diagnostics cannot disclose it.
    """

    mode: str = "hidden"
    recovery_code: str | None = field(default=None, repr=False)
    recovery_expires_at: float | None = None
    admin_url: str | None = None
    qr_matrix: tuple[tuple[bool, ...], ...] | None = field(default=None, repr=False)

    @classmethod
    def recovery(cls, code: str, expires_at: float) -> "AdminOverlayState":
        if not isinstance(code, str) or not code:
            raise ValueError("Recovery code is invalid.")
        if not isinstance(expires_at, (int, float)):
            raise ValueError("Recovery expiration is invalid.")
        return cls(mode="recovery", recovery_code=code, recovery_expires_at=float(expires_at))

    @classmethod
    def admin_access(cls, admin_url: str, qr_matrix: tuple[tuple[bool, ...], ...]) -> "AdminOverlayState":
        if not isinstance(admin_url, str) or not admin_url.startswith("http://") or not qr_matrix:
            raise ValueError("Admin access overlay is invalid.")
        return cls(mode="admin_access", admin_url=admin_url, qr_matrix=qr_matrix)

    def expired(self, now: float) -> bool:
        return self.mode == "recovery" and (
            self.recovery_expires_at is None or now >= self.recovery_expires_at
        )


@dataclass(frozen=True)
class NetworkInfo:
    hostname: str
    ip_address: str | None
    interface: str | None
    port: int
    connected: bool
    @property
    def admin_url(self):
        return f"http://{self.ip_address}:{self.port}/" if self.ip_address else None
    def document(self):
        return {"hostname": self.hostname, "ip_address": self.ip_address, "interface": self.interface,
                "port": self.port, "admin_url": self.admin_url, "status": "connected" if self.connected else "unavailable"}


def select_lan_ipv4(addresses):
    """Return the first usable private/global IPv4, never loopback/link-local."""
    for interface, address in addresses:
        try: value = ipaddress.ip_address(address)
        except ValueError: continue
        if (value.version == 4 and (value.is_private or value.is_global)
                and not (value.is_loopback or value.is_link_local or value.is_unspecified)):
            return interface, str(value)
    return None, None


class NetworkInfoService:
    def __init__(self, port, *, hostname=socket.gethostname, addresses=None, ttl=30, clock=time.monotonic):
        self._port, self._hostname, self._addresses, self._ttl, self._clock = port, hostname, addresses, ttl, clock
        self._cached = None; self._next = 0.0
    def info(self):
        now = self._clock()
        if self._cached is not None and now < self._next: return self._cached
        items = self._addresses() if self._addresses else _system_ipv4_addresses()
        interface, address = select_lan_ipv4(items)
        previous = self._cached
        self._cached = NetworkInfo(self._hostname(), address, interface, self._port, address is not None)
        self._next = now + self._ttl
        if previous is None or previous.connected != self._cached.connected:
            logger.info("PHOS ADMIN: network status=%s", "connected" if self._cached.connected else "unavailable")
        if self._cached.connected:
            logger.debug("PHOS ADMIN: selected network interface=%s ip=%s", interface, address)
        return self._cached


def _system_ipv4_addresses():
    """Best-effort local IPv4 enumeration; never performs network I/O."""
    addresses = []
    try:
        import fcntl
        import struct
        for _index, interface in socket.if_nameindex():
            request = struct.pack("256s", interface.encode()[:15])
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                    response = fcntl.ioctl(probe.fileno(), 0x8915, request)
            except OSError:
                continue
            addresses.append((interface, socket.inet_ntoa(response[20:24])))
    except (AttributeError, ImportError, OSError):
        pass
    if addresses:
        return addresses
    try:
        return [(None, item[4][0]) for item in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)]
    except socket.gaierror:
        return []


class QRMatrixCache:
    def __init__(self): self._url = self._matrix = None
    def matrix(self, url):
        if not isinstance(url, str) or not url.startswith("http://"): raise ValueError("Admin URL is invalid")
        if url != self._url:
            import qrcode
            code = qrcode.QRCode(border=2)
            code.add_data(url)
            code.make(fit=True)
            self._matrix = tuple(tuple(bool(cell) for cell in row) for row in code.get_matrix())
            self._url = url
            logger.debug("PHOS ADMIN: QR matrix regenerated")
        return self._matrix


class RecoveryState:
    def __init__(self, *, ttl=120, cooldown=30, attempts=5, clock=time.monotonic, token=None):
        self.ttl, self.cooldown, self.attempts, self.clock, self.token = ttl, cooldown, attempts, clock, token or (lambda: f"{secrets.randbelow(1_000_000):06d}")
        self.code = None; self.expires = self.issued = None; self.failed = 0
    def request(self):
        now = self.clock()
        if self.issued is not None and now - self.issued < self.cooldown: raise ValueError("recovery_rate_limited")
        self.code, self.issued, self.expires, self.failed = self.token(), now, now + self.ttl, 0
        return self.code
    def verify(self, code):
        now = self.clock()
        if self.code is None: raise ValueError("recovery_not_active")
        if now >= self.expires: self.clear(); raise ValueError("recovery_code_expired")
        if not secrets.compare_digest(str(code), self.code):
            self.failed += 1
            if self.failed >= self.attempts: self.clear(); raise ValueError("recovery_attempts_exceeded")
            raise ValueError("recovery_code_invalid")
        self.clear(); return True
    def clear(self): self.code = self.expires = None; self.failed = 0
