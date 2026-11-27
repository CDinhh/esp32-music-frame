"""Send a firmware to the screen over WiFi: python -m musicframe.ota path\\to\\firmware.bin
Address (SCREEN_HOST, default musicframe.local) and OTA_PASSWORD come from .env."""
import sys

import requests

from .config import load


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    c = load()
    host = c.get("SCREEN_HOST") or "musicframe.local"
    if not c.get("OTA_PASSWORD"):
        print("OTA_PASSWORD is missing in .env")
        return 1
    print(f"Sending {sys.argv[1]} to {host} ...")
    try:
        with open(sys.argv[1], "rb") as f:
            r = requests.post(f"http://{host}/update", files={"firmware": ("firmware.bin", f)},
                              headers={"X-OTA-Password": c["OTA_PASSWORD"]}, timeout=180)
    except requests.RequestException as e:
        print(f"Cannot reach the screen ({e.__class__.__name__}). Is it on and on the same WiFi?")
        return 1
    if r.ok:
        print("Done. The screen is restarting with the new firmware.")
        return 0
    print("Wrong OTA password." if r.status_code == 401 else f"Failed ({r.status_code}): {r.text}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
