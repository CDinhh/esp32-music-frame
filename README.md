# esp32-music-frame

ESP32-S3 (N16R8) + ST7789 240x240 desk screen: GIFs managed from a web page, now-playing track with synced lyrics from the PC.

## Wiring

| TFT | ESP32-S3 |
|---|---|
| GND / VCC | GND / **3V3** (never 5V) |
| SCL / SDA | GPIO 12 / 11 |
| RST / DC | GPIO 8 / 9 |
| CS / BL | GPIO 10 / 7 |

## Build (Windows)

The ESP32 libraries have paths longer than Windows' 260-char limit, so PlatformIO lives in a short virtual drive:

```powershell
subst P: D:\Github\esp32-music-frame\.platformio   # once per boot
$env:PLATFORMIO_CORE_DIR = "P:\"
cd firmware
..\.venv\Scripts\pio run -e esp32s3                # real board, needs include/secrets.h
```

All settings live in one file: copy `.env.example` to `.env` at the repo root and fill WiFi, OTA password and the lrc.red contact. The firmware build and the PC app both read it.

## Simulator (Wokwi)

- `pio run -e wokwi` builds the sim image (ILI9341 stand-in, same driver). `pio run -e wokwi -t buildfs` bakes `firmware/data/` (test GIFs) into it.
- Cursor/VS Code: open `firmware/`, run **Wokwi: Start Simulator**; the web page is at http://localhost:8180.
- Tests: `pio test -e wokwi` (needs `WOKWI_CLI_TOKEN`; wokwi-cli in `.tools/`). The sim env never uploads to a real board.

## Flashing

- First time (USB): Espressif's esptool-js page in Chrome/Edge, file `firmware/.pio/build/esp32s3/firmware-merged.bin` at address `0x0`.
- Later (WiFi): http://musicframe.local → Cập nhật firmware, file `firmware.bin` + OTA password.

## PC app (now playing + lyrics)

Reads what Windows is playing (Spotify, YouTube in a browser, any player that shows in the media flyout), fetches synced lyrics and pushes the rendered screen to the ESP32.

```powershell
cd pc
python -m venv .venv; .venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m musicframe.main                       # or --host 192.168.1.50
.venv\Scripts\python -m musicframe.main --host localhost:8180 # Wokwi in Cursor/VS Code
.venv\Scripts\python -m musicframe.main --png now.png         # one frame, no screen needed
.venv\Scripts\python -m pytest -q
```

Lyric sources (order and on/off set on the web page): LRCLIB (public), NetEase (line only), Kugou (word-timed), Musixmatch (unofficial, often refuses). Song title and artist are sent to these services.
