# Thiết kế: app PC viết bằng Rust, một file `MusicFrame.exe`

## Mục đích

Chạy app PC của Music Frame mà **không cần cài Python hay gì khác**, và **nhẹ nhất có thể** (file nhỏ, ít RAM, ít CPU).

## Mục tiêu

- Một file `MusicFrame.exe` (Windows 10/11 x64), chép đi đâu cũng chạy: bấm đúp là xong.
- Làm được đúng những gì app Python (`pc/`) đang làm. Bản Python là chuẩn để so: chỗ nào tài liệu này không nói thì làm y như Python.
- Firmware ESP32 **không đổi**: cùng giao thức (TCP cổng 81, header `M6` + x, y, w, h, độ dài, zlib RGB565 byte cao trước; đáp một dòng JSON; `GET /api/settings`).
- Chỉ tiêu (đo rồi báo số thật): exe ≤ 8 MB, RAM ≤ 30 MB khi đang hát, CPU thấp hơn bản Python (~8% một nhân), ≥ 30 khung/giây khi lời đang chạy.

## Không làm

Tự khởi động cùng Windows, bộ cài đặt, cửa sổ cài đặt riêng (đã có trang web trên mạch), ký số, macOS/Linux, đổi firmware, bảo mật mạng, thêm tính năng mới.

**Hỏng** nghĩa là: chạy được nhưng nhìn xấu hơn bản cũ, hoặc thiếu nguồn lời mà không nói.

## Cách làm đã cân nhắc

| Cách | Được | Mất |
|---|---|---|
| **Viết lại bằng Rust** (đã chọn) | exe vài MB, ít RAM, không cần runtime | chuyển ~2.000 dòng, phải đối chiếu lại |
| Đóng gói Python (PyInstaller) | làm nhanh, giống hệt | exe ~25–35 MB, RAM ~80 MB |

## Người dùng thấy gì

- Bấm đúp `MusicFrame.exe`: không có cửa sổ, chỉ có icon xanh nhạt ở khay hệ thống.
- Rê chuột vào icon: bài đang phát hoặc "Chưa phát nhạc" / "Không thấy màn".
- Chuột phải: **Mở trang web màn hình**, **Xem nhật ký**, **Thoát**.
- Mở lần thứ hai khi đang chạy: bản mới tự thoát (hai app sẽ giành nhau màn hình).
- `.env` đặt cạnh exe là **tuỳ chọn** (`SCREEN_HOST`, `CONTACT_LINK`, `CONTACT_EMAIL`). Không có thì tìm `musicframe.local`.
- Nhật ký ghi vào `musicframe.log` cạnh exe, quá 1 MB thì ghi lại từ đầu.

## Cấu tạo

Thư mục mới `pc-rs/` (một crate). `pc/` giữ nguyên cho tới khi bản Rust được duyệt; xoá hay không là quyết định sau.

| Module | Việc | Tương ứng Python |
|---|---|---|
| `media` | bài đang phát + ảnh bìa từ Windows media session, bám app đang phát | `media.py` |
| `clean`, `config` | làm sạch tên bài/video cover, đọc `.env` | `clean.py`, `config.py` |
| `lyrics/parse` | LRC, YRC, KRC, richsync, TTML | `lrc.py`, `wordsync.py` |
| `lyrics/sources` | 6 nguồn hỏi song song, lọc theo độ dài, xếp hạng | `sources.py` |
| `motion` | lò xo cuộn, quét từng chữ | `motion.py` |
| `text`, `render` | vẽ 240×240: phông Windows, nền mờ, bìa, lời, thanh sóng, ô thay đổi | `render.py` |
| `screen` | đọc settings, gửi ô qua TCP, tự dò lại IP | `client.py` |
| `app`, `tray` | vòng lặp chính, icon khay | `main.py` |

Luồng chạy: luồng chính giữ icon khay; một luồng vẽ và gửi; lời được tải ở luồng riêng. Không dùng async runtime.

Thư viện: `windows` (media session, khay), `ureq` với TLS của Windows, `serde_json`, `flate2`, `image` (chỉ JPEG/PNG), `fontdue`, `roxmltree`, `base64`, `regex-lite`. Thêm thư viện khác phải có lý do về dung lượng.

Bản build: `lto`, `strip`, `panic = "abort"`; ưu tiên tốc độ vẽ hơn vài trăm KB. Script `scripts/build-exe.ps1` tạo `dist/MusicFrame.exe` (`dist/` không đưa vào git). Máy dev đã có Rust 1.99 MSVC.

## Rủi ro

- **YouTube Music**: bản Python dùng thư viện `ytmusicapi`. Rust phải tự gọi API nội bộ `youtubei/v1` (`search` lọc bài hát → `next` lấy mã lời → `browse` bằng client Android để có mốc thời gian). API không công bố, có thể đổi. Nếu không làm được thì báo, không lặng lẽ bỏ.
- **Chữ**: `fontdue` thay cho FreeType của Pillow, nét có thể khác nhẹ. Kiểm bằng ảnh đặt cạnh nhau: tiếng Việt, Nhật, Hàn, Trung, ký hiệu ♪, lời từng chữ.
- **Máy khác**: exe không ký số nên Windows SmartScreen sẽ hỏi "More info → Run anyway"; phần mềm diệt virus có thể cảnh báo nhầm.

## Kiểm tra

1. `cargo test`: chuyển các test pytest về hành vi (parser, làm sạch tên, xếp hạng, độ dài, lò xo, ô thay đổi, RGB565, giao thức TCP với server giả).
2. Ảnh PNG cùng một khung từ Python và Rust đặt cạnh nhau (`MusicFrame.exe --png out.png`).
3. Chạy với mạch thật: đo khung/giây, KB/khung, CPU, RAM, dung lượng exe.
4. Chép exe sang một thư mục trống (không `.env`, không repo) và chạy.
5. Bạn nhìn màn thật và duyệt.

## Thứ tự làm

Parser và logic thuần → vẽ (so ảnh sớm) → gửi màn + đọc media (chạy được trên mạch) → 6 nguồn lời → icon khay, build exe, README.

## Phần để người làm tự quyết

Tên hàm, cách chia file nhỏ, cách cache ảnh, hình icon.
