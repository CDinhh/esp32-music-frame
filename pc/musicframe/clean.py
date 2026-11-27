import re

_NOISE = r"official|m/?v|music video|video|lyrics?|lyric video|audio|visuali[sz]er|hd|4k|mv|vietsub|engsub|karaoke"
_BRACKETS = re.compile(r"\s*[\(\[【「（][^\)\]】」）]*(?:" + _NOISE + r")[^\)\]】」）]*[\)\]】」）]", re.I)
_FEAT = re.compile(r"\s*[\(\[]?\s*\b(?:ft|feat|featuring)\.?\s.*$", re.I)
# "(cover)", "/ Acoustic Cover", "- Female Cover by X", and a bare trailing "cover" after "Artist - Song"
_COVER_TAG = re.compile(r"\s*[\(\[【「（][^\)\]】」）]*\bcover(?:ed)?\b[^\)\]】」）]*[\)\]】」）]"
                        r"|\s*(?:/|~|\s[-–—])\s*(?:\w+\s+){0,2}cover(?:\s+by\s.*)?$", re.I)
_COVER_TAIL = re.compile(r"\s+(?:(?:acoustic|piano|guitar)\s+)?cover(?:\s+by\s.*)?$", re.I)


def clean_title(title: str, artist: str) -> tuple[str, str]:
    """Strip YouTube-style noise so lyric searches hit: '(Official MV)', '| Label', 'feat. X', 'X - Topic'."""
    t = _BRACKETS.sub("", title)
    t = t.split(" | ")[0].split("｜")[0]
    t = _FEAT.sub("", t).strip()
    a = re.sub(r"\s*-\s*Topic$", "", artist or "", flags=re.I)
    a = re.sub(r"(VEVO|Official)$", "", a, flags=re.I).strip()
    a = _FEAT.sub("", a).strip()
    original = _COVER_TAG.sub("", t).strip()
    if " - " in original:
        original = _COVER_TAIL.sub("", original).strip()
    covered = bool(original) and original != t
    if covered:
        t = original
    if " - " in t:
        left, right = (p.strip() for p in t.split(" - ", 1))
        # on a cover the channel is the singer, not the song's artist the lyrics are filed under
        if covered or not a or left.lower() == a.lower() or a.lower() in left.lower():
            a, t = left, right
    return t.strip(), a.strip()
