"""Reads what Windows is playing, renders the music screen and pushes it to the ESP32.

python -m musicframe.main [--host musicframe.local] [--png out.png]
"""
import argparse
import asyncio
import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor

from .clean import clean_title
from .client import FrameClient
from .lyrics.lrc import line_index
from .lyrics.sources import SOURCES, fetch_lyrics
from .media import interpolate, read_now_playing
from .render import render_lyrics

log = logging.getLogger("musicframe")
LINE_FPS_S, WORD_FPS_S = 1.0, 0.125
RETRY_S = 120  # look for lyrics again after a miss


def frame_for(np, now, reply, lyrics) -> tuple[bytes, float]:
    """JPEG for this moment and how long until the next frame is worth sending."""
    pos = interpolate(np, np.updated_at, now) + int(reply.get("offset_ms", 0))
    karaoke = bool(reply.get("word_sync")) and bool(lyrics and lyrics.word_synced)
    jpeg = render_lyrics(np, pos, lyrics, karaoke)
    if karaoke:
        return jpeg, WORD_FPS_S
    wait = LINE_FPS_S
    if lyrics and lyrics.lines:  # wake right when the next line starts
        nxt = line_index(lyrics.lines, pos) + 1
        if nxt < len(lyrics.lines):
            wait = min(wait, max((lyrics.lines[nxt].start_ms - pos) / 1000, 0.05))
    return jpeg, wait


async def run(host: str):
    client = FrameClient(host)
    reply = client.settings() or {"offset_ms": 0, "sources": list(SOURCES), "word_sync": False, "music_mode": True}
    app = ""  # media app being shown; stay with it while it plays
    pool = ThreadPoolExecutor(max_workers=2)
    cache: dict[tuple, object] = {}
    pending: dict[tuple, Future] = {}
    retry_at: dict[tuple, float] = {}
    np, read_at = None, 0.0
    lag = 0.1  # seconds from send to pixels on screen, smoothed
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
        # draw the moment the frame will actually appear: one send (upload + ESP32 decode) ahead
        jpeg, wait = frame_for(np, now + lag, reply, cache.get(key))
        sent_at = time.time()
        got = client.send(jpeg)
        took = time.time() - sent_at
        if got is None:
            await asyncio.sleep(2)
            continue
        lag = 0.7 * lag + 0.3 * min(took, 1.0)
        reply = got
        if not reply.get("music_mode", True):
            wait = 2.0  # screen ignores frames; keep checking in case it is turned back on
        await asyncio.sleep(max(wait - took, 0.02))  # the send already used part of the wait


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
    ap.add_argument("--host", default="musicframe.local", help="screen address, e.g. 192.168.1.50")
    ap.add_argument("--png", help="write one frame of what is playing now and exit")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s", datefmt="%H:%M:%S")
    try:
        asyncio.run(one_png(a.png) if a.png else run(a.host))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
