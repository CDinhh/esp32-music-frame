import re

_NOISE = r"official|m/?v|music video|video|lyrics?|lyric video|audio|visuali[sz]er|hd|4k|mv|vietsub|engsub|karaoke"
_BRACKETS = re.compile(r"\s*[\(\[【「（][^\)\]】」）]*(?:" + _NOISE + r")[^\)\]】」）]*[\)\]】」）]", re.I)
_FEAT = re.compile(r"\s*[\(\[]?\s*\b(?:ft|feat|featuring)\.?\s.*$", re.I)


def clean_title(title: str, artist: str) -> tuple[str, str]:
    """Strip YouTube-style noise so lyric searches hit: '(Official MV)', '| Label', 'feat. X', 'X - Topic'."""
    t = _BRACKETS.sub("", title)
    t = t.split(" | ")[0].split("｜")[0]
    t = _FEAT.sub("", t).strip()
    a = re.sub(r"\s*-\s*Topic$", "", artist or "", flags=re.I)
    a = re.sub(r"(VEVO|Official)$", "", a, flags=re.I).strip()
    a = _FEAT.sub("", a).strip()
    if " - " in t:
        left, right = (p.strip() for p in t.split(" - ", 1))
        if not a or left.lower() == a.lower() or a.lower() in left.lower():
            a, t = left, right
    return t.strip(), a.strip()
