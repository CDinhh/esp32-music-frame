"""Settings from the one .env file (repo root, or next to the .exe): screen address and lrc.red contact."""
import sys
from pathlib import Path


def env_path() -> Path:
    if getattr(sys, "frozen", False):  # packaged .exe: keep .env beside it
        return Path(sys.executable).parent / ".env"
    return Path(__file__).resolve().parents[2] / ".env"


def load(path: Path | None = None) -> dict[str, str]:
    """KEY=VALUE lines; '#' comments and surrounding quotes ignored. Missing file -> {}."""
    out: dict[str, str] = {}
    try:
        text = (path or env_path()).read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out
