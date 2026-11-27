import base64
import io
import zlib

from PIL import Image

from musicframe.clean import clean_title
from musicframe.client import FrameClient
from musicframe.lyrics.lrc import line_index, parse_lrc
from musicframe.lyrics.sources import fetch_lyrics
from musicframe.lyrics.wordsync import _KRC_KEY, decode_krc, parse_krc, parse_richsync, parse_yrc
from musicframe.main import frame_for
from musicframe.media import interpolate
from musicframe.model import Line, Lyrics, NowPlaying, Word
from musicframe.render import fit, pick_font, render_basic, render_lyrics, text_width, wrap


def NP(title="Song", artist="Artist", dur=200000, pos=0, playing=True, cover=None):
    return NowPlaying(title, artist, dur, pos, playing, cover)


def img(jpeg):
    return Image.open(io.BytesIO(jpeg))


# ---- render ----
def test_render_is_240_jpeg():
    im = img(render_basic(NP(), 60000))
    assert im.size == (240, 240) and im.format == "JPEG"


def test_fit_long_title():
    s = fit("A" * 200, 18, 220)
    assert s.endswith("…") and text_width(s, 18) <= 220


def test_pick_font_scripts():
    assert "malgun" in pick_font("사랑해", 14).path.lower()
    assert "yugoth" in pick_font("心做し", 14).path.lower()
    assert "msyh" in pick_font("我爱你", 14).path.lower()
    assert "segoeui" in pick_font("Tiếng Việt", 14).path.lower()


def test_wrap_cjk_and_mixed_fit_width():
    for text in ["あなたのことなんて全然好きじゃないけれどずっとそばにいたい", "Anh yêu em 사랑해 愛してる và mãi mãi về sau nữa"]:
        lines = wrap(text, 19, 212, True)
        assert 1 <= len(lines) <= 2 and all(text_width(l, 19, True) <= 212 for l in lines)


def test_render_with_cover_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (300, 300), (200, 30, 30)).save(buf, "PNG")
    im = img(render_basic(NP(cover=buf.getvalue()), 0)).convert("RGB")
    r, g, b = im.getpixel((120, 80))  # inside the cover
    assert r > 150 and g < 80


def test_render_no_lyrics_falls_back_to_basic():
    assert img(render_lyrics(NP(), 0, None)).size == (240, 240)
    assert img(render_lyrics(NP(), 0, Lyrics([], False, "x"))).size == (240, 240)


def test_karaoke_half_sung():
    words = [Word(1000, 2000, "AAAA"), Word(2000, 3000, "BBBB")]
    ly = Lyrics([Line(1000, "AAAABBBB", words)], True, "t")
    im = img(render_lyrics(NP(), 2000, ly, karaoke=True)).convert("RGB")
    # the first line rests at the top (y~92..112); left half white, right half grey
    row = [im.getpixel((x, y)) for y in range(90, 118) for x in range(20, 220)]
    whites = [p for p in row if min(p) > 235]
    assert whites, "sung part should be white"
    left = max(x for y in range(90, 118) for x in range(20, 220) if min(im.getpixel((x, y))) > 235)
    assert left < 125  # nothing past the middle is white yet


# ---- media ----
def test_interpolate_clamps_and_pauses():
    assert interpolate(NP(dur=1000, pos=900), 0, 5) == 1000
    assert interpolate(NP(pos=900, playing=False), 0, 5) == 900
    assert interpolate(NP(pos=1000), 10.0, 10.5) == 1500


# ---- client ----
def test_client_offline_returns_none():
    assert FrameClient("127.0.0.1:9").send(b"\xff\xd8") is None


def test_client_sends_rect_headers(monkeypatch):
    c = FrameClient("127.0.0.1")
    sent = {}

    class Resp:
        def raise_for_status(self): pass
        def json(self): return {"ok": True}

    def fake_post(url, data, headers, timeout):
        sent["url"], sent["headers"] = url, headers
        return Resp()

    monkeypatch.setattr(c.session, "post", fake_post)
    c.send(b"\xff\xd8", 0, 72)
    assert sent["url"].endswith("/frame")
    assert sent["headers"]["X-Frame-X"] == "0" and sent["headers"]["X-Frame-Y"] == "72"


# ---- lyrics ----
def test_parse_lrc_multi_stamp():
    ls = parse_lrc("[ar:x]\n[00:01.50][00:10.00]la\n[00:05.123]li\n[01:02]x")
    assert [(l.start_ms, l.text) for l in ls] == [(1500, "la"), (5123, "li"), (10000, "la"), (62000, "x")]


def test_clean_title():
    assert clean_title("Song (Official MV) | Label", "Artist") == ("Song", "Artist")
    assert clean_title("Artist - Song [Lyrics]", "ArtistVEVO - Topic") == ("Song", "Artist")
    assert clean_title("Song ft. Other", "A") == ("Song", "A")
    assert clean_title("心做し", "majiko") == ("心做し", "majiko")
    assert clean_title("Tình yêu (Official MV)", "Trang ft. BigDaddy") == ("Tình yêu", "Trang")


def test_line_index_seek_back():
    ls = [Line(0, "a"), Line(5000, "b"), Line(9000, "c")]
    assert line_index(ls, 9500) == 2 and line_index(ls, 4000) == 0 and line_index(ls, -1) == -1


def test_fetch_falls_through_and_prefers_word():
    def boom(*a):
        raise ConnectionError

    line = Lyrics([Line(0, "x")], False, "line")
    word = Lyrics([Line(0, "x", [Word(0, 1, "x")])], True, "word")
    src = {"boom": boom, "none": lambda *a: None, "line": lambda *a: line, "word": lambda *a: word}
    assert fetch_lyrics("s", "a", 1, ["boom", "none", "line", "word"], False, src).source == "line"
    assert fetch_lyrics("s", "a", 1, ["boom", "line", "word"], True, src).source == "word"
    assert fetch_lyrics("s", "a", 1, ["line"], True, src).source == "line"
    assert fetch_lyrics("s", "a", 1, ["boom"], True, src) is None


def test_parse_yrc():
    ls = parse_yrc('{"t":0,"c":[]}\n[1000,2000](1000,500,0)Hel(1500,500,0)lo')
    assert len(ls) == 1
    l = ls[0]
    assert l.start_ms == 1000 and l.text == "Hello" and [(w.start_ms, w.end_ms) for w in l.words] == [(1000, 1500), (1500, 2000)]


def test_parse_krc_relative():
    l = parse_krc("[language:xx]\n[2000,1000]<0,400,0>A<400,600,0>B")[0]
    assert [(w.start_ms, w.text) for w in l.words] == [(2000, "A"), (2400, "B")]


def test_decode_krc_roundtrip():
    plain = "[0,1]<0,1,0>x"
    z = zlib.compress(plain.encode())
    enc = b"krc1" + bytes(b ^ _KRC_KEY[i % 16] for i, b in enumerate(z))
    assert decode_krc(base64.b64encode(enc).decode()) == plain


def test_parse_richsync_words():
    l = parse_richsync('[{"ts":1.0,"te":2.0,"l":[{"c":"H","o":0},{"c":"i","o":0.2},{"c":" ","o":0.4},{"c":"y","o":0.5},{"c":"o","o":0.7}],"x":"Hi yo"}]')[0]
    assert [w.text for w in l.words] == ["H", "i", " ", "y", "o"]
    assert l.words[0].start_ms == 1000 and l.words[0].end_ms == 1200
    assert l.words[3].start_ms == 1500 and l.words[4].end_ms == 2000


# ---- loop timing ----
def test_frame_timing_line_vs_word():
    from musicframe.main import ANIM_FPS_S
    ly = Lyrics([Line(0, "a", [Word(0, 500, "a")]), Line(1300, "b", [Word(1300, 2000, "b")])], True, "t")
    np = NP(pos=1000)
    _, wait = frame_for(np, np.updated_at, {"word_sync": False}, ly)
    assert abs(wait - 0.3) < 0.01  # wakes when the next line starts
    _, wait = frame_for(np, np.updated_at, {"word_sync": True}, ly)
    assert wait == ANIM_FPS_S


def test_line_scroll_animates_even_without_word_sync():
    from musicframe.lyrics.motion import SheetAnimator
    from musicframe.main import ANIM_FPS_S, frame_for
    ly = Lyrics([Line(0, "Line one"), Line(2000, "Line two"), Line(6000, "Line three")], False, "lrc")
    anim = SheetAnimator()
    np = NP(pos=500)
    # First frame snaps to line 0
    _, wait = frame_for(np, np.updated_at, {"word_sync": False}, ly, anim, mono_now=1.0)
    assert wait == 1.0
    # Advancing to line 1 triggers spring scroll + transition animation even with word_sync=False
    np2 = NP(pos=2050)
    _, wait = frame_for(np2, np2.updated_at, {"word_sync": False}, ly, anim, mono_now=1.03)
    assert wait == ANIM_FPS_S and not anim.settled
    # After ~1.0s of stepping, spring settles back to idle wait
    t = 1.03
    for _ in range(35):
        t += 0.03
        _, wait = frame_for(np2, np2.updated_at, {"word_sync": False}, ly, anim, mono_now=t)
    assert anim.settled and wait == 1.0


def test_monotone_pchip_and_timed_units():
    from musicframe.lyrics.motion import monotone, timed_units
    keys = [(0.0, 0.0), (1.0, 50.0), (2.0, 50.0), (3.0, 100.0)]
    assert monotone(keys, -0.5) == 0.0
    assert monotone(keys, 3.5) == 100.0
    assert monotone(keys, 1.5) == 50.0  # flat rest stays flat without overshoot
    mid = monotone(keys, 0.5)
    assert 0.0 < mid < 50.0
    # Unspaced CJK splits into per-character units; Latin stays cohesive in rise groups
    cjk = Line(1000, "愛してる", [Word(1000, 2500, "愛してる")])
    units = timed_units(cjk, 20.0)
    assert len(units) == 4 and units[0].start_ms == 1000 and units[-1].end_ms == 2500
    assert [u.group for u in units] == [0, 1, 2, 3]
    lat = Line(1000, "Hi yo", [Word(1000, 1200, "H"), Word(1200, 1400, "i"), Word(1400, 1500, " "), Word(1500, 1700, "y"), Word(1700, 2000, "o")])
    l_units = timed_units(lat, 20.0)
    assert l_units[0].group == l_units[1].group  # "Hi" rises together
    assert l_units[3].group == l_units[4].group and l_units[3].group > l_units[0].group


def test_render_frame_dirty_rects_and_instrumental_break():
    from musicframe.render import LYRICS_H, LYRICS_Y, render_frame
    ly = Lyrics([Line(6000, "After intro")], False, "lrc")
    jpeg_lyrics, x, y = render_frame(NP(), 3000, ly, region="lyrics")
    assert (x, y) == (0, LYRICS_Y) and img(jpeg_lyrics).size == (240, LYRICS_H)
    jpeg_lower, x2, y2 = render_frame(NP(), 3000, ly, region="lower")
    assert (x2, y2) == (0, LYRICS_Y) and img(jpeg_lower).size == (240, 240 - LYRICS_Y)


def test_haze_blurs_inactive_lines_progressively():
    from musicframe.render import _quantized_blur, _viewport_softness, ANCHOR_Y, AREA_TOP, LYRICS_Y
    anchor = (AREA_TOP - LYRICS_Y) + ANCHOR_Y
    assert _viewport_softness(anchor, anchor) == 0.0
    assert _quantized_blur(17.0, 0.0) == 0.0
    near_soft = _viewport_softness(anchor + 28.0, anchor)
    far_soft = _viewport_softness(anchor + 65.0, anchor)
    assert 0.0 < near_soft < far_soft <= 1.0
    assert 0.0 < _quantized_blur(17.0, near_soft) <= _quantized_blur(17.0, far_soft)


def test_stream_frame_and_tcp_protocol():
    import json
    import socket
    import struct
    import threading
    import zlib
    from musicframe.render import render_stream_frame

    ly = Lyrics([Line(0, "Hello"), Line(2000, "World")], False, "lrc")
    payload, x, y, w, h, shown = render_stream_frame(NP(), 500, ly)
    assert (x, y, w, h) == (0, 0, 240, 240)
    raw = zlib.decompress(payload)
    assert len(raw) == w * h * 3

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)
    port = srv.getsockname()[1]

    def server_once():
        conn, _ = srv.accept()
        hdr = conn.recv(14)
        magic, sx, sy, sw, sh, slen = struct.unpack("<2sHHHHI", hdr)
        assert (magic, sx, sy, sw, sh, slen) == (b"MF", x, y, w, h, len(payload))
        body = b""
        while len(body) < slen:
            body += conn.recv(slen - len(body))
        assert body == payload
        conn.sendall((json.dumps({"offset_ms": 0, "word_sync": False, "music_mode": True, "in_music": True, "partial": True, "stream": port}) + "\n").encode())
        conn.close()
        srv.close()

    t = threading.Thread(target=server_once, daemon=True)
    t.start()
    c = FrameClient("127.0.0.1")
    reply = c.send_stream(payload, x, y, w, h, port=port)
    c.close_stream()
    assert reply and reply["in_music"] is True and reply["stream"] == port


def test_lrclib_falls_back_to_search_with_same_length(monkeypatch):
    from musicframe.lyrics import sources

    class R:
        def __init__(self, data): self.data = data
        def json(self): return self.data

    def fake_get(url, **kw):
        if url.endswith("/get"):  # exact match exists but is plain text only
            return R({"duration": 351, "syncedLyrics": None, "plainLyrics": "x"})
        return R([{"duration": 317, "syncedLyrics": "[00:01.00]short version"},
                  {"duration": 350.2, "syncedLyrics": "[00:01.00]right version"}])

    monkeypatch.setattr(sources, "_get", fake_get)
    ly = sources.lrclib("Em Không", "Vu Thanh Van", 350621)
    assert ly.lines[0].text == "right version"
    assert sources.lrclib("Em Không", "Vu Thanh Van", 500000) is None  # far longer: lyrics would not follow


def test_fits_allows_longer_track_with_outro_only():
    from musicframe.lyrics.sources import fits
    assert fits(317000, 350621)        # MV with an extra ending (the 5:17 vs 5:50 case)
    assert fits(351000, 350621)        # same length
    assert not fits(360000, 350621)    # lyrics recording longer than the track
    assert not fits(200000, 350621)    # a different edit
    assert fits(123, 0)                # unknown track length


def test_ytmusic_picks_matching_title_and_length(monkeypatch):
    from types import SimpleNamespace as NS
    from musicframe.lyrics import sources

    class FakeYT:
        def search(self, q, filter, limit):
            return [{"title": "MISSING YOU", "videoId": "x", "duration_seconds": 244},
                    {"title": "Em Không", "videoId": "song", "duration_seconds": 317}]
        def get_watch_playlist(self, vid):
            return {"lyrics": "L-" + vid}
        def get_lyrics(self, lid, timestamps):
            assert lid == "L-song"
            return {"hasTimestamps": True, "lyrics": [NS(start_time=24280, text="Dường như em đã quên")]}

    monkeypatch.setattr(sources, "_yt", FakeYT())
    ly = sources.ytmusic("Em Không", "Vu Thanh Van", 350621)
    assert ly.source == "ytmusic" and ly.lines[0].start_ms == 24280


def test_kugou_rejects_other_version(monkeypatch):
    from musicframe.lyrics import sources

    class R:
        def __init__(self, data): self.data = data
        def json(self): return self.data

    calls = []

    def fake_get(url, **kw):
        calls.append(url)
        return R({"candidates": [{"id": 1, "accesskey": "k", "duration": 284682}]})  # Sky Tour live, 22 s longer

    monkeypatch.setattr(sources, "_get", fake_get)
    assert sources.kugou("Chắc Ai Đó Sẽ Về", "Sơn Tùng M-TP", 262164) is None
    assert not any("download" in u for u in calls)  # never fetched the wrong lyrics


TTML = ('<tt xmlns="http://www.w3.org/ns/ttml" xmlns:ttm="http://www.w3.org/ns/ttml#metadata"><body><div>'
        '<p begin="11.933" end="16.989"><span begin="11.933" end="12.343">Anh</span> <span begin="12.343" end="12.738">tìm</span>'
        '<span ttm:role="x-bg" begin="12.5" end="13"><span begin="12.5" end="13">(ooh)</span></span></p>'
        '<p begin="1:02.5" end="1:05">Dòng không có chữ</p></div></body></tt>')


def test_parse_ttml_words_and_lines():
    from musicframe.lyrics.wordsync import parse_ttml
    lines, worded = parse_ttml(TTML)
    assert worded and lines[0].start_ms == 11933 and lines[0].text == "Anh tìm"
    assert [(w.start_ms, w.end_ms) for w in lines[0].words] == [(11933, 12343), (12343, 12738)]
    assert lines[1].start_ms == 62500 and lines[1].text == "Dòng không có chữ" and lines[1].words is None


def test_rank_like_sonora():
    from musicframe.lyrics.sources import rank
    full = [Line(i * 10000, "x") for i in range(25)]                   # runs to 240 s
    word = Lyrics(full, True, "kugou", 262000)
    line_exact = Lyrics(full, False, "ytmusic", 262000)
    line_far = Lyrics(full, False, "ytmusic", 250000)
    cut = Lyrics(full[:5], True, "lrcred", 262000)                      # stops at 40 s of a 262 s song
    order = ["ytmusic", "lrcred", "kugou"]
    assert rank([line_exact, word, cut], order, 262000, True)[0] is word          # word-timed wins when preferred
    assert rank([word, line_exact], order, 262000, False)[0] is line_exact        # otherwise the user's order
    assert rank([line_far, line_exact], order, 262000, False)[0] is line_exact    # then the closer length
    assert rank([cut, line_exact], order, 262000, True)[0] is line_exact          # a cut-short sheet ranks last


def test_lrcred_picks_word_timed_same_length(monkeypatch):
    from musicframe.lyrics import sources

    class R:
        def __init__(self, data=None, text=""): self.data, self.text = data, text
        def json(self): return self.data

    def fake_get(url, **kw):
        if "api/v1" in url:
            return R({"results": [
                {"track_name": "Chắc Ai Đó Sẽ Về (Sky Tour 2019)", "duration": 284, "timing_type": "word", "lyricsUrl": "https://lrc.red/s/live.ttml"},
                {"track_name": "Chắc Ai Đó Sẽ Về", "duration": 262, "timing_type": "word", "lyricsUrl": "https://lrc.red/s/studio.ttml"}]})
        assert url.endswith("studio.ttml")
        return R(text=TTML)

    monkeypatch.setattr(sources, "_get", fake_get)
    ly = sources.lrcred("Chắc Ai Đó Sẽ Về", "Sơn Tùng M-TP", 262164)
    assert ly.source == "lrcred" and ly.word_synced and ly.duration_ms == 262000


def test_pick_session_sticks_to_playing_app():
    from types import SimpleNamespace as NS
    from musicframe.media import Status, pick_session

    def sess(app, playing):
        st = Status.PLAYING if playing else Status.PAUSED
        return NS(source_app_user_model_id=app, get_playback_info=lambda: NS(playback_status=st))

    sonora, browser = sess("sonora.exe", True), sess("Helium", True)
    assert pick_session([sonora, browser], browser, "sonora.exe") is sonora   # hover preview grabbed "current"
    assert pick_session([sonora, browser], browser, "") is browser            # nothing chosen yet: Windows' pick
    paused = sess("sonora.exe", False)
    assert pick_session([paused, browser], paused, "sonora.exe") is browser   # chosen app stopped: follow what plays
    assert pick_session([paused], paused, "sonora.exe") is paused             # nothing plays: report the paused one


def test_config_reads_env(tmp_path):
    from musicframe.config import load
    f = tmp_path / ".env"
    f.write_text('# comment\nSCREEN_HOST=192.168.1.5\nWIFI_PASS="p=a#ss"\n\nBROKEN\n', encoding="utf-8")
    c = load(f)
    assert c["SCREEN_HOST"] == "192.168.1.5" and c["WIFI_PASS"] == "p=a#ss" and "BROKEN" not in c
    assert load(tmp_path / "missing.env") == {}


def _bands(mask):
    """(top, bottom) of each run of lit rows in an L image."""
    rows = [mask.crop((0, y, mask.width, y + 1)).getextrema()[1] > 25 for y in range(mask.height)]
    out, start = [], None
    for y, on in enumerate(rows + [False]):
        if on and start is None:
            start = y
        elif not on and start is not None:
            out.append((start, y))
            start = None
    return out


def test_active_line_is_centred_and_lines_are_evenly_spaced():
    from musicframe.render import LYRICS_H, _compose_sheet
    ly = Lyrics([Line(i * 1000, "Hello") for i in range(7)], False, "lrc")
    centres = [(a + b) / 2 for a, b in _bands(_compose_sheet(ly, 3100, False, None, 1.0))]
    mid = min(centres, key=lambda c: abs(c - LYRICS_H / 2))
    assert abs(mid - LYRICS_H / 2) <= 2.5
    i = centres.index(mid)
    assert abs((centres[i] - centres[i - 1]) - (centres[i + 1] - centres[i])) <= 1.5


def test_rows_trail_behind_the_scroll():
    from musicframe.lyrics.motion import SheetAnimator, build_sheet_items
    ly = Lyrics([Line(i * 1000, "x") for i in range(8)], False, "lrc")
    items, anim = build_sheet_items(ly.lines), SheetAnimator()
    layout = lambda a: ([i * 30.0 for i in range(8)], [20.0] * 8)
    anim.step(ly, items, layout, 68, 136, 2100, 1.0)
    anim.step(ly, items, layout, 68, 136, 3100, 1.03)
    anim.step(ly, items, layout, 68, 136, 3100, 1.06)
    assert anim.row_drifts and all(d[0] >= 0 for d in anim.row_drifts.values())  # drawn below their place


def test_stream_sends_only_what_changed():
    from musicframe.render import render_stream_frame
    ly = Lyrics([Line(0, "Hello")], False, "lrc")
    *_, shown = render_stream_frame(NP(), 5000, ly, now_s=1.0)
    _, x, y, w, h, shown = render_stream_frame(NP(), 5000, ly, now_s=2.0, previous=shown)
    assert (x, y, w, h) == (239, 239, 1, 1)  # nothing changed: 1x1 keep-alive
    _, x, y, w, h, _ = render_stream_frame(NP(), 30000, ly, now_s=3.0, previous=shown)
    assert y >= 200 and h < 40  # only the timeline moved


def test_first_line_starts_at_the_top():
    from musicframe.render import _compose_sheet
    ly = Lyrics([Line(i * 1000, "Hello") for i in range(7)], False, "lrc")
    first = _bands(_compose_sheet(ly, 100, False, None, 1.0))[0]
    assert first[0] < 30  # no empty half screen above the opening line


def test_timeline_bars_take_the_cover_colour_up_to_the_playhead():
    buf = io.BytesIO()
    Image.new("RGB", (64, 64), (20, 60, 200)).save(buf, "PNG")
    im = img(render_basic(NP(pos=100000, cover=buf.getvalue()), 100000)).convert("RGB")
    row = [im.getpixel((x, 225)) for x in range(45, 195)]
    blue = [x for x, (r, g, b) in enumerate(row) if b > 180 and b - r > 50]
    assert blue and max(blue) < 85  # coloured only on the played half
