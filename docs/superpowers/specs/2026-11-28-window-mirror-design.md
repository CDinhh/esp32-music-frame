# Chiếu một cửa sổ lên màn (đã duyệt 2026-11-28)

## Vì sao
Định chỉ có một màn hình, muốn liếc video trên màn ESP32 trong lúc chơi game. Bản thử bằng script đã chạy (~10-12 hình/s, firmware giữ nguyên).

## Làm gì
- Menu ở khay: **Chiếu cửa sổ ▸** liệt kê các cửa sổ đang mở, có mục **Tắt**. Chọn cửa sổ nào thì màn chiếu đúng cửa sổ đó (mọi app, không riêng trình duyệt), thay cho lời nhạc.
- Cửa sổ bị che vẫn chiếu. Thu nhỏ thì giữ nguyên hình cuối. Đóng cửa sổ thì tự quay về lời nhạc.
- Giữ tỉ lệ, viền đen. Màn phải ở chế độ **Nhạc** trên trang web (như trước).
- Chỉ chiếu cửa sổ đã chọn, không bao giờ cả màn hình. Không nhớ lựa chọn sau khi thoát app.
- Mục tiêu từ 20 hình/s (phải đo), CPU thấp nhất có thể vì đang chơi game.

## Cách làm
- `PrintWindow(PW_RENDERFULLCONTENT)` rồi tự thu nhỏ (trung bình ô); chụp ở một luồng, gửi ở luồng khác. Cách đã chứng minh được ở bản thử, ít code.
- Đã cân nhắc: Windows Graphics Capture (nhanh hơn nhưng cần D3D11, nhiều code, có viền vàng trên Win10) chỉ làm nếu không đạt 20 hình/s; tự tải video theo link bị loại (cần yt-dlp/ffmpeg, trái điều khoản YouTube).
- Gửi một ô thay đổi mỗi hình (một lượt đi-về), RGB565 + zlib như cũ.
- App khai báo DPI per-monitor để chụp đúng cỡ thật.
- `MusicFrame.exe --mirror "một phần tên cửa sổ"` để thử không cần bấm menu.
- Trình duyệt Chromium phải mở kèm `--disable-backgrounding-occluded-windows --disable-renderer-backgrounding --disable-features=CalculateNativeWinOcclusion`, nếu không nó ngừng vẽ khi bị che. Thêm vào shortcut Helium của Định.

## Không làm
Tiếng (vẫn phát ở máy tính), bấm F hộ, cắt vùng video, vượt chống sao chép (Netflix có thể ra đen), sửa firmware, bản Python.

## Đo được (2026-11-28, màn thật, cửa sổ thử ngoài màn hình)
Hình phẳng ~28 hình/s; ảnh chụp lướt toàn khung ~19 (zlib mức mặc định, mức nhanh chỉ ~16); nhiễu màu (xấu nhất) ~12. Nghẽn ở đường WiFi + vẽ trên màn, không ở khâu chụp, nên không làm Windows Graphics Capture.

## Thêm sau khi dùng thử (2026-11-28)
- Định thấy viền đen khó chịu với video 16:9: thêm mục **Phóng đầy màn (cắt hai bên)**, bật sẵn, lấy phần vuông giữa cửa sổ (không bóp méo). Thay cho dòng "không cắt vùng video" ở trên.
- Phóng đầy gửi nhiều điểm ảnh hơn, ảnh lướt thử tụt ~19 → ~12 hình/s. Bỏ 1 bit màu mỗi kênh (chỉ khi chiếu) đưa lại ~15 hình/s, 22 KB/hình.
