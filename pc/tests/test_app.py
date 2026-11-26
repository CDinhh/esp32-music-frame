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
    # current line is centred around y~114; left half white, right half grey
    row = [im.getpixel((x, y)) for y in range(100, 135) for x in range(20, 220)]
    whites = [p for p in row if min(p) > 235]
    assert whites, "sung part should be white"
    left = max(x for y in range(100, 135) for x in range(20, 220) if min(im.getpixel((x, y))) > 235)
    assert left < 125  # nothing past the middle is white yet


# ---- media ----
def test_interpolate_clamps_and_pauses():
    assert interpolate(NP(dur=1000, pos=900), 0, 5) == 1000
    assert interpolate(NP(pos=900, playing=False), 0, 5) == 900
    assert interpolate(NP(pos=1000), 10.0, 10.5) == 1500


# ---- client ----
def test_client_offline_returns_none():
    assert FrameClient("127.0.0.1:9").send(b"\xff\xd8") is None


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
    assert [w.text for w in l.words] == ["Hi", "yo"] and l.words[1].start_ms == 1500 and l.words[1].end_ms == 2000


# ---- loop timing ----
def test_frame_timing_line_vs_word():
    ly = Lyrics([Line(0, "a", [Word(0, 500, "a")]), Line(1300, "b", [Word(1300, 2000, "b")])], True, "t")
    np = NP(pos=1000)
    _, wait = frame_for(np, np.updated_at, {"word_sync": False}, ly)
    assert abs(wait - 0.3) < 0.01  # wakes when the next line starts
    _, wait = frame_for(np, np.updated_at, {"word_sync": True}, ly)
    assert wait == 0.125
