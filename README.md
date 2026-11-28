# Music Frame

Một màn hình nhỏ để bàn (ESP32-S3 + màn 1.54" 240×240) làm hai việc:

- **Chiếu GIF** bạn chọn.
- **Hiện bài nhạc đang phát trên máy tính**: ảnh bìa, tên bài, thời gian và **lời bài hát chạy theo nhạc** (có kiểu karaoke tô sáng từng chữ).

Mọi thứ điều khiển từ một **trang web** mở trên điện thoại hay máy tính cùng WiFi: thêm/xoá GIF, chuyển Nhạc ↔ GIF, chỉnh độ sáng, xem tình trạng mạch.

---

## Cần chuẩn bị

| Thứ | Ghi chú |
|---|---|
| Bo **ESP32-S3 N16R8** | loại có 2 cổng USB-C |
| Màn **1.54" TFT SPI ST7789** 240×240 | loại 8 chân: BL, CS, DC, RST, SDA, SCL, VCC, GND |
| 8 sợi dây cắm (jumper) | cái–cái |
| Cáp USB-C | để nạp lần đầu; sau đó cắm cục sạc điện thoại nào cũng được |
| Máy tính **Windows 10/11** | để cài đặt và chạy app hiện nhạc |
| WiFi **2.4 GHz** | ESP32 không bắt được WiFi 5 GHz |

---

## Bắt đầu nhanh (5 bước)

### Bước 1: Nối dây

**Rút điện trước khi nối.** Đọc tên chân in trên mạch, nối theo bảng (thứ tự chân không quan trọng, chỉ cần đúng tên):

| Chân trên màn | Chân trên ESP32-S3 |
|---|---|
| VCC | **3V3** ⚠️ không phải 5V |
| GND | G (GND) |
| SCL | 12 |
| SDA | 11 |
| RST | 8 |
| DC | 9 |
| CS | 10 |
| BL | 7 |

Không cần điện trở hay linh kiện nào khác. Điều duy nhất có thể làm hỏng màn là cắm VCC vào 5V, nên kiểm tra lại dây VCC trước khi cắm điện.

### Bước 2: Cài đặt (làm một lần)

1. Cài **Python** từ [python.org](https://www.python.org/downloads/). Lúc cài **nhớ tick "Add python.exe to PATH"**.
2. Tải thư mục dự án này về máy.
3. **Bấm đúp `1-setup.cmd`**. Script tự cài mọi thứ cần thiết, rồi mở Notepad để bạn điền:
   - `WIFI_SSID`: tên WiFi nhà bạn
   - `WIFI_PASS`: mật khẩu WiFi
   - `OTA_PASSWORD`: một mật khẩu bạn tự đặt, dùng khi cập nhật màn qua WiFi sau này

   Điền xong thì lưu (Ctrl+S) rồi đóng Notepad.

> Mọi cài đặt nằm trong một file duy nhất là `.env` ở thư mục gốc. File này chỉ ở trên máy bạn, không bị đưa lên mạng.

### Bước 3: Nạp phần mềm vào màn (lần đầu)

1. Cắm ESP32 vào máy tính bằng cáp USB-C.
2. **Bấm đúp `2-flash-usb.cmd`**. Lần đầu sẽ tải khoảng 1 GB công cụ nên hơi lâu; các lần sau chỉ mất khoảng 1 phút.
3. Xong, màn hiện chữ **`musicframe.local`** kèm một địa chỉ IP trong vài giây.

Nếu báo lỗi không kết nối được mạch: **giữ nút BOOT**, bấm rồi thả nút **RESET**, thả BOOT, rồi chạy lại `2-flash-usb.cmd`.

Từ giờ có thể rút cáp khỏi máy tính và cắm màn vào cục sạc điện thoại bất kỳ.

### Bước 4: Mở trang điều khiển

Trên điện thoại hoặc máy tính **cùng WiFi**, mở trình duyệt và vào:

- **http://musicframe.local**
- hoặc địa chỉ IP màn hiện lúc khởi động (ví dụ `http://192.168.1.50`). Một số điện thoại Android không mở được `.local`, khi đó dùng IP.

Thêm GIF ngay trong mục **Thư viện GIF**: bấm **Thêm GIF** để chọn file, hoặc **dán link** (link file `.gif`, link Giphy, hay mã nhúng `<iframe>`).

### Bước 5: Hiện nhạc đang phát

1. Mở nhạc trên máy tính: Spotify, YouTube trên trình duyệt, Sonora, hay bất kỳ app nào hiện trong ô điều khiển nhạc của Windows (ô hiện ra khi bấm nút tăng giảm âm lượng).
2. Chạy app trên máy tính, chọn **một** trong hai:
   - **`MusicFrame.exe`** (nhẹ, không cần Python): [tải ở trang Releases](https://github.com/CDinhh/esp32-music-frame/releases/latest) rồi bấm đúp là chạy. App không có cửa sổ, chỉ có icon nốt nhạc ở khay hệ thống; bấm vào icon để mở trang web, xem nhật ký hoặc thoát. Windows có thể hỏi "Windows protected your PC" vì file chưa ký: bấm **More info** → **Run anyway**.
   - **`3-run-app.cmd`** (bản Python): một cửa sổ đen nhỏ mở ra, **cứ để nó chạy**, đóng cửa sổ là tắt.
3. Trên trang web, chọn **Nhạc** ở góc trên. Màn chuyển sang ảnh bìa + lời bài hát; dừng nhạc khoảng 5 giây thì màn tự quay về GIF.

---

## Dùng hằng ngày

| Muốn | Làm |
|---|---|
| Đổi GIF đang chiếu | Bấm vào GIF trong thư viện |
| Thêm GIF | **Thêm GIF** (file ≤ 2 MB, cạnh dài ≤ 480 px) hoặc dán link |
| Chỉ chiếu GIF, không hiện nhạc | Chọn **GIF** ở góc trên trang web |
| Lời chạy kiểu karaoke | Bật **Lời chạy theo từng chữ** |
| Lời nhanh hoặc chậm hơn nhạc một chút | Kéo **Lệch lời** |
| Ưu tiên nguồn lời nào trước | Kéo thả trong **Nguồn lời bài hát** (trên cùng được ưu tiên), tắt nguồn không muốn dùng |
| Xem màn có ổn không | Mục **Tình trạng**: nhiệt độ, WiFi, số lần sụt nguồn |
| Xem màn đang làm gì | Mục **Nhật ký** (tự cập nhật mỗi giây) |
| Cập nhật phần mềm cho màn | Bấm đúp **`update-wifi.cmd`** (không cần cắm dây) |
| Xem video trên màn nhỏ trong lúc làm việc khác | Bấm icon `MusicFrame.exe` ở khay → **Chiếu cửa sổ** → chọn cửa sổ (xem bên dưới) |

### Chiếu một cửa sổ lên màn

Chỉ có ở `MusicFrame.exe` (bản mới hơn v0.1.0). Màn phải đang ở chế độ **Nhạc**. Chọn **Tắt** để quay về lời bài hát.

- Cửa sổ bị cửa sổ khác che vẫn chiếu. Thu nhỏ thì màn giữ hình cuối. Đóng cửa sổ thì tự về lời bài hát.
- Chỉ có hình, tiếng vẫn phát ở máy tính. Muốn video đầy màn nhỏ thì bấm **F** trong trình phát video trước.
- Chrome, Edge, Helium ngừng vẽ khi bị che. Đóng hẳn trình duyệt, rồi thêm vào cuối ô **Target** của shortcut dùng để mở nó:
  `--disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-features=CalculateNativeWinOcclusion`
- Game chạy **Fullscreen** độc quyền có thể làm cửa sổ phía sau ngừng vẽ; thử chế độ **Windowed Fullscreen**.
- Nội dung chống sao chép (ví dụ Netflix) có thể chỉ ra màn đen.
- Tốc độ phụ thuộc WiFi và độ phức tạp của hình: khoảng 12–28 hình/giây.

---

## Gặp sự cố

| Hiện tượng | Thử |
|---|---|
| Màn tối hoàn toàn | Kiểm tra dây VCC (3V3), GND và BL. Cắm lại nguồn. |
| Màn sáng nhưng trắng hoặc đen trơn | Kiểm tra lại dây SCL, SDA, CS, DC, RST theo bảng ở Bước 1. |
| Màn hiện "connecting WiFi..." mãi | Sai tên/mật khẩu WiFi trong `.env`, hoặc WiFi là 5 GHz. Sửa `.env` rồi chạy lại `2-flash-usb.cmd`. |
| Không mở được http://musicframe.local | Dùng địa chỉ IP màn hiện lúc khởi động; kiểm tra điện thoại cùng WiFi với màn. |
| Chọn Nhạc mà màn vẫn chiếu GIF | App trên máy tính chưa chạy: mở `MusicFrame.exe` (hoặc `3-run-app.cmd`) và phát nhạc. Trang web cũng hiện cảnh báo vàng khi gặp trường hợp này. |
| Có bài nhưng không có lời | Bài đó không có lời khớp với bản bạn nghe (thường gặp với bản MV có intro dài, hoặc nhạc ít người nghe). Thử bản audio chính thức. |
| Lời lệch nhịp | Kéo **Lệch lời** trên trang web. |
| Mục Tình trạng báo "Sụt nguồn" | Thay cục sạc hoặc dây USB khác (nên từ 5V 1A). |

---

## Dành cho người phát triển

<details>
<summary>Cấu trúc, build, giả lập, test</summary>

```
firmware/   ESP32-S3 (PlatformIO, Arduino): màn hình, GIF, web, OTA, log
  src/        main, display, gif_player, web, settings, health, log, fetch, index.html
  lib/validate/  kiểm tra đầu vào (thuần C++, có test)
  test/       test Unity chạy trên giả lập Wokwi
  wiretest/   firmware kiểm tra dây màn
pc/         app Python: đọc nhạc của Windows, tìm lời, vẽ màn 240×240, gửi sang ESP32
pc-rs/      cùng app đó viết bằng Rust, ra một file MusicFrame.exe
scripts/    setup.ps1, firmware.ps1 (các file .cmd ở thư mục gốc gọi tới)
.env        cấu hình duy nhất (gitignore); mẫu: .env.example
```

**Firmware**

```powershell
.\scripts\firmware.ps1 -Action build   # chỉ build
.\scripts\firmware.ps1 -Action usb     # build + nạp qua USB
.\scripts\firmware.ps1 -Action wifi    # build + nạp qua WiFi (OTA)
```

Thư viện ESP32 có đường dẫn dài quá giới hạn 260 ký tự của Windows, nên script ánh xạ `.platformio` thành một ổ ảo ngắn (P: hoặc chữ kế tiếp còn trống). Ổ này mất khi khởi động lại máy, script tự tạo lại.

Lần nạp đầu cũng có thể làm trên web: trang esptool-js của Espressif (Chrome/Edge), file `firmware/.pio/build/esp32s3/firmware-merged.bin`, địa chỉ `0x0`.

**Giả lập (Wokwi)**: env `wokwi` dùng màn ILI9341 thay ST7789 (cùng driver), không bao giờ nạp vào mạch thật.

```powershell
cd firmware
..\.venv\Scripts\pio run -e wokwi            # build ảnh giả lập; Cursor/VS Code: "Wokwi: Start Simulator"
..\.venv\Scripts\pio test -e wokwi           # test Unity trong giả lập (cần WOKWI_CLI_TOKEN, wokwi-cli trong .tools/)
```

**App PC (Rust)**

```powershell
.\scripts\build-exe.ps1               # ra dist\MusicFrame.exe
.\scripts\build-exe.ps1 -Cargo test   # chạy test
```

Cần Rust (rustup). Lần đầu: `rustup toolchain install stable-x86_64-pc-windows-gnu` rồi `rustup component add llvm-tools --toolchain stable-x86_64-pc-windows-gnu`. Không cần Visual Studio.

Để cạnh exe một file `.env` nếu muốn đặt `SCREEN_HOST`, `CONTACT_LINK`, `CONTACT_EMAIL` (không bắt buộc). Nhật ký nằm ở `musicframe.log` cạnh exe. `MusicFrame.exe --png now.png` vẽ bài đang phát ra file; `--lyrics "ca sĩ" "tên bài" > out.txt` xem từng nguồn lời trả gì.

**App PC (Python)**

```powershell
cd pc
.venv\Scripts\python -m musicframe.main                   # tự tìm musicframe.local
.venv\Scripts\python -m musicframe.main --png now.png     # vẽ một khung ra file, không cần màn
.venv\Scripts\python -m pytest -q
```

Nguồn lời (thứ tự và bật/tắt chỉnh trên web): Apple Music qua lrc.red (lời theo từng chữ, phải ghi nguồn; điền `CONTACT_LINK`/`CONTACT_EMAIL` trong `.env` theo điều khoản của họ), YouTube Music, LRCLIB, NetEase, Kugou, Musixmatch. App hỏi tất cả cùng lúc rồi chọn: lời theo từng chữ > theo câu, rồi theo thứ tự nguồn, rồi bản thu có độ dài sát nhất. Tên bài và ca sĩ được gửi tới các dịch vụ này.

</details>
