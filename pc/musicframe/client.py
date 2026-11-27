import ipaddress
import json
import logging
import socket
import struct

import requests

log = logging.getLogger("musicframe")


class FrameClient:
    """Pushes changed parts of the screen over a TCP stream; reads settings over HTTP."""

    def __init__(self, host: str):
        self.host = host
        self.session = requests.Session()
        self._down = False
        self._ip = host.split(":")[0]
        self._sock: socket.socket | None = None
        self._rfile = None
        self.url = ""
        self.resolve()

    def resolve(self) -> None:
        """Turn a name like musicframe.local into its current IP once (a .local lookup on Windows
        can take seconds, too slow per frame). Called again whenever the screen stops answering,
        so a new IP from the router is picked up by itself."""
        ip = self.host
        host_only = self.host.split(":")[0]
        try:
            ipaddress.ip_address(host_only)
            self._ip = host_only
        except ValueError:
            try:
                ip = socket.getaddrinfo(host_only, 80, socket.AF_INET)[0][4][0]
                self._ip = ip
                log.info("screen %s is at %s", self.host, ip)
            except OSError:
                self._ip = host_only  # keep the name; the next failure retries
        self.url = f"http://{ip}"

    def close_stream(self) -> None:
        if self._rfile is not None:
            try:
                self._rfile.close()
            except OSError:
                pass
            self._rfile = None
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None

    def settings(self) -> dict | None:
        """The screen's settings in the same shape a stream reply has, so the first lyric lookup
        already uses the user's source order (no second lookup once the first frame answers)."""
        try:
            s = self.session.get(f"{self.url}/api/settings", timeout=3).json()
            return {"offset_ms": s["offset_ms"], "word_sync": s["word_sync"], "music_mode": s["music_mode"],
                    "in_music": bool(s.get("in_music", False)),
                    "stream": s.get("stream"),
                    "sources": [x["id"] for x in s["sources"] if x["on"]]}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return None

    def send_stream(self, payload: bytes, x: int, y: int, w: int, h: int, port: int = 81) -> dict | None:
        """Send a zlib-compressed RGB565 rect over persistent TCP_NODELAY socket."""
        try:
            if self._sock is None:
                sock = socket.create_connection((self._ip, port), timeout=1.5)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.settimeout(1.5)
                self._sock = sock
                self._rfile = sock.makefile("rb")
            hdr = struct.pack("<2sHHHHI", b"M6", x, y, w, h, len(payload))
            self._sock.sendall(hdr + payload)
            line = self._rfile.readline()
            if not line:
                raise OSError("stream closed")
            reply = json.loads(line)
        except (OSError, ValueError) as e:
            self.close_stream()
            if not self._down:
                log.warning("stream unreachable at %s:%d (%s); retrying", self._ip, port, e.__class__.__name__)
            self._down = True
            self.resolve()
            return None
        if self._down:
            log.info("screen back online")
        self._down = False
        return reply
