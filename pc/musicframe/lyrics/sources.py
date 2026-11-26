"""Lyric sources. LRCLIB is a public API; NetEase, Kugou and Musixmatch are unofficial and may break."""
import logging
import time
from typing import Callable

import requests

from ..model import Line, Lyrics
from .lrc import parse_lrc
from .wordsync import decode_krc, parse_krc, parse_richsync, parse_yrc

log = logging.getLogger("musicframe")
UA = {"User-Agent": "esp32-music-frame/0.1"}
TIMEOUT = 4
Source = Callable[[str, str, int], Lyrics | None]


def _get(url: str, **kw) -> requests.Response:
    r = requests.get(url, headers={**UA, **kw.pop("headers", {})}, timeout=TIMEOUT, **kw)
    r.raise_for_status()
    return r


def fits(candidate_ms: float, track_ms: int) -> bool:
    """Can timed lyrics of a recording this long follow the track that is playing?
    Equal length (+-3 s), or a shorter recording when the track only adds an ending (MV outros, up to ~15 %)."""
    if track_ms <= 0 or not candidate_ms:
        return True
    extra = track_ms - candidate_ms
    return -3000 <= extra <= max(3000, 0.15 * track_ms)


def _closest(items, key_ms, track_ms):
    """Items whose length fits, closest first."""
    ok = [i for i in items if fits(key_ms(i), track_ms)]
    return sorted(ok, key=lambda i: abs(track_ms - key_ms(i))) if track_ms > 0 else ok


def lrclib(title: str, artist: str, duration_ms: int) -> Lyrics | None:
    params = {"track_name": title, "artist_name": artist}
    if duration_ms > 0:
        params["duration"] = round(duration_ms / 1000)
    try:
        hit = _get("https://lrclib.net/api/get", params=params).json()
    except requests.HTTPError:
        hit = None
    if not hit or not hit.get("syncedLyrics"):  # exact match missing or plain-text only: try other uploads
        hits = _get("https://lrclib.net/api/search", params={"q": f"{artist} {title}".strip()}).json()
        synced = [h for h in hits if h.get("syncedLyrics")]
        hit = next(iter(_closest(synced, lambda h: float(h.get("duration") or 0) * 1000, duration_ms)), None)
    if not hit:
        return None
    return Lyrics(parse_lrc(hit["syncedLyrics"]), False, "lrclib")


def netease(title: str, artist: str, duration_ms: int) -> Lyrics | None:
    songs = _get("https://music.163.com/api/search/get", params={"s": f"{title} {artist}", "type": 1, "limit": 5}).json()
    songs = (songs.get("result") or {}).get("songs") or []
    if duration_ms > 0:
        songs = _closest(songs, lambda s: s.get("duration", 0), duration_ms)
    if not songs:
        return None
    data = _get("https://music.163.com/api/song/lyric", params={"id": songs[0]["id"], "lv": 1, "yv": 1}).json()
    yrc = (data.get("yrc") or {}).get("lyric")
    if yrc and (lines := parse_yrc(yrc)):
        return Lyrics(lines, True, "netease")
    lrc = (data.get("lrc") or {}).get("lyric")
    lines = parse_lrc(lrc) if lrc else []
    return Lyrics(lines, False, "netease") if lines else None


# Kugou's lyric host times out on https; lyrics are public text, so plain http is acceptable here.
def kugou(title: str, artist: str, duration_ms: int) -> Lyrics | None:
    found = _get("http://lyrics.kugou.com/search", params={"ver": 1, "man": "yes", "client": "pc",
                                                             "keyword": f"{artist} - {title}", "duration": duration_ms}).json()
    cands = found.get("candidates") or []
    if not cands:
        return None
    c = cands[0]
    dl = _get("http://lyrics.kugou.com/download", params={"ver": 1, "client": "pc", "id": c["id"],
                                                           "accesskey": c["accesskey"], "fmt": "krc", "charset": "utf8"}).json()
    if not dl.get("content"):
        return None
    lines = parse_krc(decode_krc(dl["content"]))
    return Lyrics(lines, True, "kugou") if lines else None


class _Mxm:
    BASE = "https://apic-desktop.musixmatch.com/ws/1.1/"
    token: str | None = None
    blocked_until = 0.0

    def call(self, method: str, **params) -> dict:
        if time.time() < self.blocked_until:
            raise RuntimeError("musixmatch backing off")
        if not self.token:
            r = self._raw("token.get", user_language="en")
            token = r["message"]["body"]["user_token"]
            if not token or set(token) <= {"0"}:  # all-zero token = refused / captcha
                self.blocked_until = time.time() + 600
                raise RuntimeError("musixmatch refused a token")
            self.token = token
        body = self._raw(method, usertoken=self.token, **params)
        status = body["message"]["header"]["status_code"]
        if status in (401, 402):  # token expired or captcha
            self.token = None
            self.blocked_until = time.time() + 600
            raise RuntimeError("musixmatch refused")
        return body["message"]["body"]

    def _raw(self, method: str, **params) -> dict:
        params = {"app_id": "web-desktop-app-v1.0", "format": "json", **params}
        return _get(self.BASE + method, params=params, headers={"Cookie": "x-mxm-token-guid="}).json()


_mxm = _Mxm()


def musixmatch(title: str, artist: str, duration_ms: int) -> Lyrics | None:
    body = _mxm.call("matcher.track.get", q_track=title, q_artist=artist)
    track = (body or {}).get("track") if isinstance(body, dict) else None
    if not track:
        return None
    tid = track["track_id"]
    if track.get("has_richsync"):
        rs = _mxm.call("track.richsync.get", track_id=tid)
        if isinstance(rs, dict) and rs.get("richsync"):
            return Lyrics(parse_richsync(rs["richsync"]["richsync_body"]), True, "musixmatch")
    if track.get("has_subtitles"):
        st = _mxm.call("track.subtitle.get", track_id=tid, subtitle_format="lrc")
        if isinstance(st, dict) and st.get("subtitle"):
            return Lyrics(parse_lrc(st["subtitle"]["subtitle_body"]), False, "musixmatch")
    return None


_yt = None


def _same_song(a: str, b: str) -> bool:
    a, b = a.casefold().strip(), b.casefold().strip()
    return bool(a) and (a in b or b in a)


def ytmusic(title: str, artist: str, duration_ms: int) -> Lyrics | None:
    """YouTube Music's own timed lyrics (LyricFind / Musixmatch behind it); no sign-in needed."""
    global _yt
    from ytmusicapi import YTMusic

    _yt = _yt or YTMusic()
    found = [r for r in _yt.search(f"{title} {artist}", filter="songs", limit=8)
             if r.get("videoId") and _same_song(title, r.get("title", ""))]
    for r in _closest(found, lambda r: (r.get("duration_seconds") or 0) * 1000, duration_ms)[:2]:
        lid = _yt.get_watch_playlist(r["videoId"]).get("lyrics")
        ly = _yt.get_lyrics(lid, timestamps=True) if lid else None
        if ly and ly.get("hasTimestamps") and ly.get("lyrics"):
            return Lyrics([Line(l.start_time, l.text) for l in ly["lyrics"]], False, "ytmusic")
    return None


SOURCES: dict[str, Source] = {"ytmusic": ytmusic, "lrclib": lrclib, "netease": netease, "kugou": kugou, "musixmatch": musixmatch}


def fetch_lyrics(title: str, artist: str, duration_ms: int, order: list[str], prefer_word: bool,
                 sources: dict[str, Source] | None = None) -> Lyrics | None:
    """Try sources in order. With prefer_word, a word-timed hit beats an earlier line-timed one."""
    sources = SOURCES if sources is None else sources
    fallback = None
    for name in order:
        fn = sources.get(name)
        if not fn:
            continue
        try:
            got = fn(title, artist, duration_ms)
        except Exception as e:  # unofficial APIs fail in many ways; never kill the app
            log.info("lyrics: %s failed (%s)", name, e.__class__.__name__)
            continue
        if not got or not got.lines:
            continue
        if not prefer_word or got.word_synced:
            return got
        fallback = fallback or got
    return fallback
