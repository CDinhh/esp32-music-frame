import ipaddress
import logging
import socket

import requests

log = logging.getLogger("musicframe")


class FrameClient:
    """POSTs JPEG frames to the screen; returns its reply (settings) or None when unreachable."""

    def __init__(self, host: str):
        self.host = host
        self.session = requests.Session()
        self._down = False
        self.url = ""
        self.resolve()

    def resolve(self) -> None:
        """Turn a name like musicframe.local into its current IP once (a .local lookup on Windows
        can take seconds, too slow per frame). Called again whenever the screen stops answering,
        so a new IP from the router is picked up by itself."""
        ip = self.host
        try:
            ipaddress.ip_address(self.host.split(":")[0])
        except ValueError:
            try:
                ip = socket.getaddrinfo(self.host, 80, socket.AF_INET)[0][4][0]
                log.info("screen %s is at %s", self.host, ip)
            except OSError:
                pass  # keep the name; the next failure retries
        self.url = f"http://{ip}/frame"

    def settings(self) -> dict | None:
        """The screen's settings in the same shape a /frame reply has, so the first lyric lookup
        already uses the user's source order (no second lookup once the first frame answers)."""
        try:
            s = self.session.get(self.url.replace("/frame", "/api/settings"), timeout=3).json()
            return {"offset_ms": s["offset_ms"], "word_sync": s["word_sync"], "music_mode": s["music_mode"],
                    "sources": [x["id"] for x in s["sources"] if x["on"]]}
        except (requests.RequestException, ValueError, KeyError, TypeError):
            return None

    def send(self, jpeg: bytes) -> dict | None:
        try:
            r = self.session.post(self.url, data=jpeg, headers={"Content-Type": "image/jpeg"}, timeout=1.5)
            r.raise_for_status()
            reply = r.json()
        except (requests.RequestException, ValueError) as e:
            if not self._down:
                log.warning("screen unreachable at %s (%s); retrying", self.url, e.__class__.__name__)
            self._down = True
            self.resolve()  # its IP may have changed
            return None
        if self._down:
            log.info("screen back online")
        self._down = False
        return reply
