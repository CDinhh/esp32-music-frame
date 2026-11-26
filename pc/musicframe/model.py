from dataclasses import dataclass, field


@dataclass
class Word:
    start_ms: int
    end_ms: int
    text: str


@dataclass
class Line:
    start_ms: int
    text: str
    words: list[Word] | None = None


@dataclass
class Lyrics:
    lines: list[Line]
    word_synced: bool
    source: str
    duration_ms: int = 0  # length of the recording these lyrics were timed to, 0 if unknown


@dataclass
class NowPlaying:
    title: str
    artist: str
    duration_ms: int
    position_ms: int  # position at updated_at
    playing: bool
    cover: bytes | None = None
    updated_at: float = 0.0  # unix seconds when position_ms was true
    app: str = field(default="", compare=False)
