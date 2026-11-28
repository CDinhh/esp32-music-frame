//! Reads one window's own pixels, also while other windows cover it, and shrinks them to the screen.
use crate::img::Rgb;
use crate::render::{Rect, H, W};
use windows::core::BOOL;
use windows::Win32::Foundation::{HWND, LPARAM, RECT};
use windows::Win32::Graphics::Dwm::{DwmGetWindowAttribute, DWMWA_CLOAKED};
use windows::Win32::Graphics::Gdi::*;
use windows::Win32::Storage::Xps::{PrintWindow, PRINT_WINDOW_FLAGS};
use windows::Win32::UI::WindowsAndMessaging::*;

/// PW_CLIENTONLY | PW_RENDERFULLCONTENT (the second one includes what the GPU composed, e.g. video).
const FLAGS: PRINT_WINDOW_FLAGS = PRINT_WINDOW_FLAGS(1 | 2);

fn handle(window: isize) -> HWND {
    HWND(window as *mut _)
}

pub fn title(window: isize) -> String {
    let mut text = [0u16; 256];
    // SAFETY: the buffer and its length go together; a dead handle just gives 0
    let used = unsafe { GetWindowTextW(handle(window), &mut text) };
    String::from_utf16_lossy(&text[..used.max(0) as usize])
}

/// The windows a person would see in Alt+Tab, front to back, without this app's own.
pub fn list_windows() -> Vec<(isize, String)> {
    unsafe extern "system" fn each(hwnd: HWND, out: LPARAM) -> BOOL {
        // SAFETY: `out` is the Vec below, alive for the whole enumeration; the calls only read
        unsafe {
            let out = &mut *(out.0 as *mut Vec<(isize, String)>);
            let mut cloaked = 0u32; // hidden by Windows: another virtual desktop, suspended store app
            let _ = DwmGetWindowAttribute(hwnd, DWMWA_CLOAKED, (&raw mut cloaked).cast(), 4);
            let mut process = 0;
            GetWindowThreadProcessId(hwnd, Some(&mut process));
            let listed = IsWindowVisible(hwnd).as_bool()
                && cloaked == 0
                && process != std::process::id()
                && GetWindowLongW(hwnd, GWL_EXSTYLE) as u32 & WS_EX_TOOLWINDOW.0 == 0
                && GetWindow(hwnd, GW_OWNER).map_or(true, |owner| owner.is_invalid());
            if listed {
                let name = title(hwnd.0 as isize);
                if !name.is_empty() && name != "Program Manager" {
                    out.push((hwnd.0 as isize, name));
                }
            }
        }
        true.into()
    }
    let mut out: Vec<(isize, String)> = Vec::new();
    // SAFETY: the callback gets a pointer to `out`, which outlives the call
    let _ = unsafe { EnumWindows(Some(each), LPARAM(&raw mut out as isize)) };
    out
}

pub enum Shot {
    Frame(Rgb),
    /// minimised or not drawable right now: keep the last picture
    Minimized,
    Gone,
}

/// The off-screen bitmap the window draws itself into, reused while its size stays the same.
struct Surface {
    dc: HDC,
    bitmap: HBITMAP,
    old: HGDIOBJ,
    bits: *const u8,
    w: usize,
    h: usize,
}

impl Surface {
    fn new(w: usize, h: usize) -> Option<Self> {
        let info = BITMAPINFO {
            bmiHeader: BITMAPINFOHEADER {
                biSize: std::mem::size_of::<BITMAPINFOHEADER>() as u32,
                biWidth: w as i32,
                biHeight: -(h as i32), // top row first
                biPlanes: 1,
                biBitCount: 32,
                ..Default::default()
            },
            ..Default::default()
        };
        // SAFETY: a valid 32-bit header; the DC and bitmap are freed in Drop
        unsafe {
            let dc = CreateCompatibleDC(None);
            let mut bits = std::ptr::null_mut();
            let Ok(bitmap) = CreateDIBSection(Some(dc), &info, DIB_RGB_COLORS, &mut bits, None, 0) else {
                let _ = DeleteDC(dc);
                return None;
            };
            let old = SelectObject(dc, bitmap.into());
            Some(Self { dc, bitmap, old, bits: bits as *const u8, w, h })
        }
    }
}

impl Drop for Surface {
    fn drop(&mut self) {
        // SAFETY: created in `new`, released once
        unsafe {
            SelectObject(self.dc, self.old);
            let _ = DeleteObject(self.bitmap.into());
            let _ = DeleteDC(self.dc);
        }
    }
}

#[derive(Default)]
pub struct Camera {
    surface: Option<Surface>,
}

impl Camera {
    pub fn shoot(&mut self, window: isize) -> Shot {
        let hwnd = handle(window);
        let mut area = RECT::default();
        // SAFETY: plain queries; PrintWindow draws into our own DC, and the bits are read only
        // after GdiFlush, within the size the bitmap was made with
        unsafe {
            if !IsWindow(Some(hwnd)).as_bool() {
                return Shot::Gone;
            }
            if IsIconic(hwnd).as_bool() || GetClientRect(hwnd, &mut area).is_err() {
                return Shot::Minimized;
            }
            let (w, h) = (area.right.max(0) as usize, area.bottom.max(0) as usize);
            if w == 0 || h == 0 {
                return Shot::Minimized;
            }
            if self.surface.as_ref().is_none_or(|s| (s.w, s.h) != (w, h)) {
                self.surface = Surface::new(w, h);
            }
            let Some(surface) = &self.surface else { return Shot::Minimized };
            if !PrintWindow(hwnd, surface.dc, FLAGS).as_bool() {
                return Shot::Minimized;
            }
            let _ = GdiFlush();
            Shot::Frame(letterbox(std::slice::from_raw_parts(surface.bits, w * h * 4), w, h))
        }
    }
}

/// Fit a BGRX picture into the screen, keeping its shape (black around it), each output pixel the
/// average of the source pixels under it.
pub fn letterbox(bgrx: &[u8], w: usize, h: usize) -> Rgb {
    let (ow, oh) = if w >= h { (W, (h * W / w).max(1)) } else { ((w * H / h).max(1), H) };
    let (left, top) = ((W - ow) / 2, (H - oh) / 2);
    let span = |i: usize, from: usize, to: usize| (i * from / to, ((i + 1) * from / to).max(i * from / to + 1));
    let columns: Vec<(usize, usize)> = (0..ow).map(|x| span(x, w, ow)).collect();
    let mut out = Rgb::new(W, H, [0; 3]);
    let mut sums = vec![[0u32; 3]; ow];
    for y in 0..oh {
        let (y0, y1) = span(y, h, oh);
        sums.fill([0; 3]);
        for row in bgrx[y0 * w * 4..y1 * w * 4].chunks_exact(w * 4) {
            for (sum, &(x0, x1)) in sums.iter_mut().zip(&columns) {
                for p in row[x0 * 4..x1 * 4].chunks_exact(4) {
                    sum[0] += u32::from(p[2]);
                    sum[1] += u32::from(p[1]);
                    sum[2] += u32::from(p[0]);
                }
            }
        }
        let line = &mut out.px[((top + y) * W + left) * 3..][..ow * 3];
        for ((pixel, sum), &(x0, x1)) in line.chunks_exact_mut(3).zip(&sums).zip(&columns) {
            let n = ((x1 - x0) * (y1 - y0)) as u32;
            pixel.copy_from_slice(&sum.map(|v| (v / n) as u8));
        }
    }
    out
}

/// One rectangle around all of them: a video frame then costs one round trip, not two.
pub fn union(rects: &[Rect]) -> Rect {
    let x = rects.iter().map(|r| r.x).min().unwrap_or(0);
    let y = rects.iter().map(|r| r.y).min().unwrap_or(0);
    let right = rects.iter().map(|r| r.x + r.w).max().unwrap_or(W);
    let bottom = rects.iter().map(|r| r.y + r.h).max().unwrap_or(H);
    Rect { x, y, w: right - x, h: bottom - y }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_wide_picture_keeps_its_shape_with_black_above_and_below() {
        let (red, blue) = ([0, 0, 255, 0], [255, 0, 0, 0]); // BGRX
        let source: Vec<u8> = [red, red, blue, blue, red, red, blue, blue].concat();
        let out = letterbox(&source, 4, 2);
        assert_eq!(out.get(0, 59), [0, 0, 0]);
        assert_eq!(out.get(0, 60), [255, 0, 0]);
        assert_eq!(out.get(239, 179), [0, 0, 255]);
        assert_eq!(out.get(239, 180), [0, 0, 0]);
    }

    #[test]
    fn shrinking_averages_and_a_tall_picture_gets_side_bars() {
        // 480 wide, 960 high, black and white columns: halves to 120 x 240 grey in the middle
        let row: Vec<u8> = (0..480).flat_map(|x| if x % 2 == 0 { [0u8; 4] } else { [200; 4] }).collect();
        let out = letterbox(&row.repeat(960), 480, 960);
        assert_eq!(out.get(59, 0), [0, 0, 0]);
        assert_eq!(out.get(60, 0), [100, 100, 100]);
        assert_eq!(out.get(179, 239), [100, 100, 100]);
        assert_eq!(out.get(180, 239), [0, 0, 0]);
    }

    #[test]
    fn union_covers_every_rectangle() {
        let rects = [Rect { x: 10, y: 5, w: 20, h: 20 }, Rect { x: 0, y: 212, w: 240, h: 28 }];
        assert_eq!(union(&rects), Rect { x: 0, y: 5, w: 240, h: 235 });
    }
}
