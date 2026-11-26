import logging

import requests

log = logging.getLogger("musicframe")


class FrameClient:
    """POSTs JPEG frames to the screen; returns its reply (settings) or None when unreachable."""

    def __init__(self, host: str):
        self.url = f"http://{host}/frame"
        self.session = requests.Session()
        self._down = False

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
            return None
        if self._down:
            log.info("screen back online")
        self._down = False
        return reply
