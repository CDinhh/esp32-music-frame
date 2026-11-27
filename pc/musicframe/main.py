"""Reads what Windows is playing, renders the music screen and pushes it to the ESP32.

python -m musicframe.main [--host musicframe.local] [--png out.png]
"""
import argparse
import asyncio
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .clean import clean_title
from .config import load as load_config
from .client import FrameClient
from .lyrics.motion import SheetAnimator, build_sheet_items
from .lyrics.sources import SOURCES, fetch_lyrics
from .media import interpolate, read_now_playing
from .render import render_screen, render_stream_frame

log = logging.getLogger("musicframe")
LINE_FPS_S, ANIM_FPS_S = 1.0, 1.0 / 50.0
WAVE_FPS_S = 1.0 / 20.0  # the timeline wave keeps moving
RETRY_S = 120  # look for lyrics again after a miss


def _next_wait(lyrics, pos: int, karaoke: bool, animator: SheetAnimator | None, mono_now: float) -> float:
    if not lyrics or not lyrics.lines:
        return LINE_FPS_S
    items = build_sheet_items(lyrics.lines)
    if animator is not None:
        if animator.is_animating(items, pos, mono_now, karaoke):
            return ANIM_FPS_S
    elif karaoke:
        return ANIM_FPS_S

    wait = LINE_FPS_S
    for it in items:
        if it.start_ms > pos:
            wait = min(wait, max((it.start_ms - pos) / 1000.0, 0.02))
            break
    return wait


def stream_packet_for(
    np,
    now: float,
    reply: dict,
    lyrics,
    animator: SheetAnimator | None = None,
    mono_now: float | None = None,
    previous=None,
):
    """Changed parts of the screen: ([(payload, x, y, w, h)], wait, rendered position_ms, whole frame)."""
    if mono_now is None:
        mono_now = time.monotonic()
    pos = interpolate(np, np.updated_at, now) + int(reply.get("offset_ms", 0))
    karaoke = bool(reply.get("word_sync")) and bool(lyrics and lyrics.word_synced)
    parts, frame = render_stream_frame(np, pos, lyrics, karaoke, animator, mono_now, previous)
    wait = min(_next_wait(lyrics, pos, karaoke, animator, mono_now), WAVE_FPS_S)
    return parts, wait, pos, frame


async def run(host: str):
    client = FrameClient(host)
    reply = client.settings() or {"offset_ms": 0, "sources": list(SOURCES), "word_sync": False, "music_mode": True}
    app = ""  # media app being shown; stay with it while it plays
    pool = ThreadPoolExecutor(max_workers=2)
    cache: dict[tuple, object] = {}
    pending: dict[tuple, Future] = {}
    retry_at: dict[tuple, float] = {}
    animator = SheetAnimator()
    shown = None  # the frame the screen is showing, so only changes are streamed
    np, read_at = None, 0.0
    lag = 0.02  # seconds from send to pixels on screen, smoothed
    log.info("sending to %s", host)
    while True:
        now = time.time()
        if now - read_at >= 1.0:
            try:
                np = await read_now_playing(stick_to=app)
                if np and np.playing:
                    app = np.app
            except OSError as e:
                log.warning("media session: %s", e)
                np = None
            read_at = now
        if not np or not np.playing:
            shown = None
            await asyncio.sleep(0.5)  # nothing sent: the screen returns to its GIF after 5 s
            continue
        title, artist = clean_title(np.title, np.artist)
        np.title, np.artist = title, artist  # show the cleaned name on screen too
        key = (title, artist, tuple(reply.get("sources", [])), bool(reply.get("word_sync")))
        if key in retry_at and now >= retry_at[key]:  # a source may have timed out: look again
            del cache[key], retry_at[key]
        if key not in cache and key not in pending:
            log.info("now playing: %s - %s", artist, title)
            pending[key] = pool.submit(fetch_lyrics, title, artist, np.duration_ms, list(key[2]), key[3])
        if key in pending and pending[key].done():
            cache[key] = pending.pop(key).result()
            ly = cache[key]
            if ly is None:
                retry_at[key] = now + RETRY_S
            log.info("lyrics: %s", f"{ly.source} ({'word' if ly.word_synced else 'line'})" if ly else "none")

        ly = cache.get(key)
        sent_at = time.time()
        parts, wait, _, frame = stream_packet_for(np, now + lag, reply, ly, animator, time.monotonic(), shown)
        port = int(reply.get("stream") or 81)
        for part in parts:
            got = client.send_stream(*part, port)
            if got is None:
                break
        # only trust it as "on screen" if the screen says it is showing music
        shown = frame if got and got.get("music_mode", True) and got.get("in_music", True) else None
        took = time.time() - sent_at
        if got is None:
            await asyncio.sleep(2)
            continue
        lag = 0.7 * lag + 0.3 * min(took, 1.0)
        reply = got
        if shown is None:
            wait = 2.0  # screen ignores frames; keep checking in case it is turned back on
        await asyncio.sleep(max(wait - took, 0.001))  # the send already used part of the wait


async def one_png(path: str):
    np = await read_now_playing()
    if not np:
        raise SystemExit("nothing is playing")
    title, artist = clean_title(np.title, np.artist)
    np.title, np.artist = title, artist
    lyrics = fetch_lyrics(title, artist, np.duration_ms, list(SOURCES), True)
    print(f"{artist} - {title}: lyrics {lyrics.source if lyrics else 'none'}")
    render_screen(np, interpolate(np, np.updated_at, time.time()), lyrics, True).save(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=load_config().get("SCREEN_HOST") or "musicframe.local",
                    help="screen name or IP (default musicframe.local, found automatically; SCREEN_HOST in .env overrides)")
    ap.add_argument("--png", help="write one frame of what is playing now and exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(one_png(a.png) if a.png else run(a.host))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
