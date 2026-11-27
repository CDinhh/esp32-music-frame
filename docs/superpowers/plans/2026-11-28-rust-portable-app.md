# Rust Portable PC App Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans (native, in this session). The user delegated design and plan approval: do not stop to ask.

**Goal:** `MusicFrame.exe`, a tray app that does what `pc/` does, with no runtime to install.

**Architecture:** One crate in `pc-rs/`, a straight port of `pc/musicframe` module by module. Pure logic first (unit tested), then drawing (compared by PNG), then Windows and network.

**Tech Stack:** Rust 1.99 GNU + `rust-lld`; `windows`, `ureq` + `native-tls`, `serde_json`, `flate2`, `image` (jpeg/png/bmp), `ab_glyph` + `memmap2`, `roxmltree`, `base64`, `regex-lite`.

**Spec:** `docs/superpowers/specs/2026-11-28-rust-portable-app-design.md`

## Global Constraints

- Python `pc/` is the reference behaviour and stays untouched.
- Firmware untouched: TCP :81, `M6` header `<2sHHHHI`, zlib RGB565 high byte first, one JSON line back; `GET /api/settings`.
- exe ≤ 8 MB, RAM ≤ 30 MB while playing, ≥ 30 fps while lyrics move.
- Spec deviation: `ab_glyph` + memory-mapped fonts instead of `fontdue` (fontdue parses every glyph of a CJK font up front and would break the RAM target).

## Review Focus

- Song title with only emoji or unknown script: draws without panic.
- Very long single word / CJK line: wraps, no out-of-bounds drawing.
- Zero or unknown duration: timeline draws, no division by zero.
- Screen unreachable at start or mid-song: logs once, retries, never exits.
- A source returning malformed JSON/XML: that source is skipped, others still answer.

## Tasks

### Task 1: pure logic (`model`, `config`, `clean`, `parse`, `motion`)
- Tests: the pytest cases for clean title (incl. covers), LRC, YRC, KRC (+ decode roundtrip), richsync, TTML, env parsing, monotone, timed units, row drift.
- Run: `cargo test` → all pass. Commit.

### Task 2: drawing (`img`, `text`, `render`)
- Produces: `render::Renderer::screen(&mut self, &NowPlaying, pos_ms, Option<&Lyrics>, karaoke, Option<&mut SheetAnimator>, now_s) -> Rgb`; `render::dirty_boxes(prev, frame)`; `render::rgb565(&Rgb, box) -> Vec<u8>`.
- Tests: 240×240; fit/wrap widths; centred line and even spacing; first line near top; timeline wave stops at the knob; RGB565 bytes `F8 00 07 E0 08 7F`; unchanged frame → one 1×1 box; Review Focus 1–3.
- Compare PNGs with Python for Vietnamese, Japanese, Korean, Chinese, karaoke, no lyrics. Commit.

### Task 3: `media`, `screen`, `app`
- Produces: `screen::Client::{new(host), settings() -> Option<Reply>, send(payload, box) -> Option<Reply>}`; `media::Reader::read(stick_to) -> Option<NowPlaying>`.
- Tests: TCP protocol against a fake server; unreachable → None; session picking. Commit.

### Task 4: `sources`
- Six sources, parallel ask, `fits`, `rank` as in Python.
- Tests: fits table, rank order, parsing of canned YouTube Music search/lyrics JSON, bad payload → None. Live check with `--lyrics "artist" "title"`. Commit.

### Task 5: `tray`, packaging, README
- Tray tooltip/menu (open web, open log, quit), single instance mutex, log file ≤ 1 MB, `--png`, `scripts/build-exe.ps1` → `dist/MusicFrame.exe`, README section. Commit.

### Task 6: verify on the real screen
- fps, KB/frame, CPU, RAM, exe size; run from an empty folder; merge to `main`, push, vault log.
