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
from .render import progress_visual, render_frame, render_stream_frame

log = logging.getLogger("musicframe")
LINE_FPS_S, ANIM_FPS_S = 1.0, 1.0 / 50.0
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


def packet_for(
    np,
    now: float,
    reply: dict,
    lyrics,
    animator: SheetAnimator | None = None,
    mono_now: float | None = None,
    region: str = "full",
) -> tuple[bytes, int, int, float, int]:
    """JPEG (full or dirty rect), (x, y), wait until next frame, and rendered position_ms."""
    if mono_now is None:
        mono_now = time.monotonic()
    pos = interpolate(np, np.updated_at, now) + int(reply.get("offset_ms", 0))
    karaoke = bool(reply.get("word_sync")) and bool(lyrics and lyrics.word_synced)
    jpeg, x, y = render_frame(np, pos, lyrics, karaoke, animator, mono_now, region=region)
    wait = _next_wait(lyrics, pos, karaoke, animator, mono_now)
    return jpeg, x, y, wait, pos


def stream_packet_for(
    np,
    now: float,
    reply: dict,
    lyrics,
    animator: SheetAnimator | None = None,
    mono_now: float | None = None,
    region: str = "full",
) -> tuple[bytes, int, int, int, int, float, int]:
    """Lossless zlib RGB888 packet, (x, y, w, h), wait until next frame, and rendered position_ms."""
    if mono_now is None:
        mono_now = time.monotonic()
    pos = interpolate(np, np.updated_at, now) + int(reply.get("offset_ms", 0))
    karaoke = bool(reply.get("word_sync")) and bool(lyrics and lyrics.word_synced)
    payload, x, y, w, h = render_stream_frame(np, pos, lyrics, karaoke, animator, mono_now, region=region)
    wait = _next_wait(lyrics, pos, karaoke, animator, mono_now)
    return payload, x, y, w, h, wait, pos


def frame_for(np, now, reply, lyrics, animator: SheetAnimator | None = None, mono_now: float | None = None) -> tuple[bytes, float]:
    """Full 240x240 JPEG for this moment and how long until the next frame is worth sending."""
    jpeg, _, _, wait, _ = packet_for(np, now, reply, lyrics, animator, mono_now, region="full")
    return jpeg, wait


async def run(host: str):
    client = FrameClient(host)
    reply = client.settings() or {"offset_ms": 0, "sources": list(SOURCES), "word_sync": False, "music_mode": True}
    app = ""  # media app being shown; stay with it while it plays
    pool = ThreadPoolExecutor(max_workers=2)
    cache: dict[tuple, object] = {}
    pending: dict[tuple, Future] = {}
    retry_at: dict[tuple, float] = {}
    animator = SheetAnimator()
    last_static = None
    last_prog = None
    np, read_at = None, 0.0
    lag = 0.02  # seconds from send to pixels on screen, smoothed
    log.info("sending to http://%s/frame", host)
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
            last_static = None
            last_prog = None
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
        mono_now = time.monotonic()
        est_pos = interpolate(np, np.updated_at, now + lag) + int(reply.get("offset_ms", 0))
        static_key = (title, artist, np.cover, np.duration_ms, ly.source if ly else "")
        prog_key = progress_visual(np, est_pos) if (ly and ly.lines) else None

        if reply.get("partial") and reply.get("in_music") and last_static == static_key and ly and ly.lines:
            region = "lyrics" if last_prog == prog_key else "lower"
        else:
            region = "full"

        sent_at = time.time()
        stream_port = reply.get("stream")
        if stream_port:
            payload, x, y, w, h, wait, rendered_pos = stream_packet_for(np, now + lag, reply, ly, animator, mono_now, region=region)
            got = client.send_stream(payload, x, y, w, h, int(stream_port))
        else:
            jpeg, x, y, wait, rendered_pos = packet_for(np, now + lag, reply, ly, animator, mono_now, region=region)
            got = client.send(jpeg, x, y)
        took = time.time() - sent_at
        if got is None:
            last_static = None
            last_prog = None
            await asyncio.sleep(2)
            continue
        lag = 0.7 * lag + 0.3 * min(took, 1.0)
        reply = got
        if reply.get("music_mode", True) and reply.get("in_music", True):
            last_static = static_key
            last_prog = progress_visual(np, rendered_pos) if (ly and ly.lines) else None
        else:
            last_static = None
            last_prog = None
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
    jpeg, _ = frame_for(np, time.time(), {"word_sync": True}, lyrics)
    with open(path, "wb") as f:
        f.write(jpeg)


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
