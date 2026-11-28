//! The tray icon: the app has no window of its own.
use crate::app::{Mirrored, Status};
use crate::mirror;
use std::sync::atomic::Ordering::Relaxed;
use std::sync::OnceLock;
use windows::core::{w, HSTRING, PCWSTR};
use windows::Win32::Foundation::{GetLastError, ERROR_ALREADY_EXISTS, HWND, LPARAM, LRESULT, POINT, WPARAM};
use windows::Win32::System::LibraryLoader::GetModuleHandleW;
use windows::Win32::System::Threading::CreateMutexW;
use windows::Win32::UI::Shell::{
    ShellExecuteW, Shell_NotifyIconW, NIF_ICON, NIF_MESSAGE, NIF_TIP, NIM_ADD, NIM_DELETE, NIM_MODIFY, NOTIFYICONDATAW,
};
use windows::Win32::UI::WindowsAndMessaging::*;

const FROM_ICON: u32 = WM_APP + 1;
const OPEN_WEB: usize = 1;
const OPEN_LOG: usize = 2;
const QUIT: usize = 3;
const MIRROR_OFF: usize = 4;
const MIRROR_FILL: usize = 5;
const MIRROR_FIRST: usize = 100; // one id per listed window
const SIZE: i32 = 32;

struct Tray {
    host: String,
    status: Status,
    mirrored: Mirrored,
    /// sent by Explorer when the taskbar is recreated: the icon has to be added again
    taskbar_created: u32,
    icon: isize,
}

static TRAY: OnceLock<Tray> = OnceLock::new();

/// False if the app is already running.
pub fn only_instance() -> bool {
    // SAFETY: a named mutex with no security attributes; the handle is kept open until the process ends
    unsafe {
        let made = CreateMutexW(None, true, w!("MusicFrame-single"));
        made.is_ok() && GetLastError() != ERROR_ALREADY_EXISTS
    }
}

/// A white note on a light blue disc, as BGRA rows.
fn pixels() -> Vec<u8> {
    let mut out = Vec::with_capacity((SIZE * SIZE * 4) as usize);
    for y in 0..SIZE {
        for x in 0..SIZE {
            let (px, py) = (x as f64 + 0.5, y as f64 + 0.5);
            let dot = |cx: f64, cy: f64, r: f64| ((px - cx).powi(2) + (py - cy).powi(2)).sqrt() - r;
            let bar = |x0: f64, y0: f64, x1: f64, y1: f64| crate::img::rounded_rect(px, py, [x0, y0, x1, y1], 1.0);
            let disc = (0.5 - dot(16.0, 16.0, 15.5)).clamp(0.0, 1.0);
            let note = dot(13.0, 22.0, 4.5).min(bar(15.0, 7.0, 18.5, 22.0)).min(bar(15.0, 7.0, 24.0, 11.5));
            let white = (0.5 - note).clamp(0.0, 1.0);
            let blend = |under: f64| (under + (255.0 - under) * white).round() as u8;
            out.extend_from_slice(&[blend(235.0), blend(140.0), blend(50.0), (disc * 255.0).round() as u8]);
        }
    }
    out
}

/// Tooltip text, cut to what Windows takes (127 UTF-16 units and the end mark).
fn tip(text: &str) -> [u16; 128] {
    let mut out = [0u16; 128];
    let mut used = 0;
    for c in text.chars() {
        let mut units = [0u16; 2];
        let units = c.encode_utf16(&mut units);
        if used + units.len() > 127 {
            break;
        }
        out[used..used + units.len()].copy_from_slice(units);
        used += units.len();
    }
    out
}

fn data(hwnd: HWND, tray: &Tray) -> NOTIFYICONDATAW {
    let status = tray.status.lock().unwrap_or_else(|e| e.into_inner()).clone();
    NOTIFYICONDATAW {
        cbSize: std::mem::size_of::<NOTIFYICONDATAW>() as u32,
        hWnd: hwnd,
        uID: 1,
        uFlags: NIF_ICON | NIF_MESSAGE | NIF_TIP,
        uCallbackMessage: FROM_ICON,
        hIcon: HICON(tray.icon as *mut _),
        szTip: tip(if status.is_empty() { "MusicFrame" } else { &status }),
        ..Default::default()
    }
}

fn open(file: &str, parameters: &str) {
    // SAFETY: both strings live until the call returns
    unsafe {
        ShellExecuteW(None, w!("open"), &HSTRING::from(file), &HSTRING::from(parameters), PCWSTR::null(), SW_SHOWNORMAL);
    }
}

/// A window title as a menu line: not too long, and "&" is not an underline mark.
fn label(title: &str) -> String {
    let mut short: String = title.chars().take(60).collect();
    if short.len() < title.len() {
        short.push('…');
    }
    short.replace('&', "&&")
}

fn menu(hwnd: HWND, tray: &Tray) {
    let windows = mirror::list_windows(); // as they are when the menu opens
    let current = tray.mirrored.load(Relaxed);
    let check = |on: bool| if on { MF_STRING | MF_CHECKED } else { MF_STRING };
    // SAFETY: the menu is created, shown and destroyed here, on the thread that owns hwnd
    let chosen = unsafe {
        let Ok(menu) = CreatePopupMenu() else { return };
        if let Ok(list) = CreatePopupMenu() {
            for (i, (window, title)) in windows.iter().enumerate() {
                let _ = AppendMenuW(list, check(*window == current), MIRROR_FIRST + i, &HSTRING::from(label(title)));
            }
            let _ = AppendMenuW(list, MF_SEPARATOR, 0, None);
            let _ = AppendMenuW(list, check(mirror::FILL.load(Relaxed)), MIRROR_FILL, w!("Phóng đầy màn (cắt hai bên)"));
            let _ = AppendMenuW(list, check(current == 0), MIRROR_OFF, w!("Tắt"));
            // the submenu is destroyed together with its parent
            let _ = AppendMenuW(menu, MF_POPUP, list.0 as usize, w!("Chiếu cửa sổ"));
        }
        let _ = AppendMenuW(menu, MF_STRING, OPEN_WEB, w!("Mở trang web màn hình"));
        let _ = AppendMenuW(menu, MF_STRING, OPEN_LOG, w!("Xem nhật ký"));
        let _ = AppendMenuW(menu, MF_STRING, QUIT, w!("Thoát"));
        let mut at = POINT::default();
        let _ = GetCursorPos(&mut at);
        let _ = SetForegroundWindow(hwnd); // so the menu closes when clicking elsewhere
        let chosen = TrackPopupMenu(menu, TPM_RETURNCMD | TPM_NONOTIFY, at.x, at.y, None, hwnd, None).0 as usize;
        let _ = DestroyMenu(menu);
        chosen
    };
    match chosen {
        OPEN_WEB => open(&format!("http://{}/", tray.host), ""),
        OPEN_LOG => {
            let path = crate::config::exe_dir().join(crate::log::FILE);
            open("notepad.exe", &format!("\"{}\"", path.display()));
        }
        // SAFETY: plain window calls on the owning thread
        QUIT => unsafe {
            let _ = Shell_NotifyIconW(NIM_DELETE, &data(hwnd, tray));
            PostQuitMessage(0);
        },
        MIRROR_OFF => tray.mirrored.store(0, Relaxed),
        MIRROR_FILL => {
            mirror::FILL.fetch_xor(true, Relaxed);
        }
        id => {
            if let Some((window, _)) = id.checked_sub(MIRROR_FIRST).and_then(|i| windows.get(i)) {
                tray.mirrored.store(*window, Relaxed);
            }
        }
    }
}

extern "system" fn handle(hwnd: HWND, message: u32, wparam: WPARAM, lparam: LPARAM) -> LRESULT {
    if let Some(tray) = TRAY.get() {
        match message {
            FROM_ICON if matches!(lparam.0 as u32, WM_LBUTTONUP | WM_RBUTTONUP) => menu(hwnd, tray),
            // SAFETY: a fully initialised NOTIFYICONDATAW
            WM_TIMER => unsafe {
                let _ = Shell_NotifyIconW(NIM_MODIFY, &data(hwnd, tray));
            },
            // SAFETY: as above
            m if m == tray.taskbar_created => unsafe {
                let _ = Shell_NotifyIconW(NIM_ADD, &data(hwnd, tray));
            },
            _ => {}
        }
    }
    // SAFETY: passing the message on unchanged
    unsafe { DefWindowProcW(hwnd, message, wparam, lparam) }
}

/// Show the icon and stay until "Thoát" is chosen.
pub fn run(host: String, status: Status, mirrored: Mirrored) -> windows::core::Result<()> {
    // SAFETY: standard hidden-window setup; every pointer passed lives for the call
    unsafe {
        let instance = GetModuleHandleW(None)?;
        let class = w!("MusicFrameTray");
        RegisterClassW(&WNDCLASSW { lpfnWndProc: Some(handle), hInstance: instance.into(), lpszClassName: class, ..Default::default() });
        let bits = pixels();
        let mask = [0u8; (SIZE * SIZE / 8) as usize];
        let icon = CreateIcon(Some(instance.into()), SIZE, SIZE, 1, 32, mask.as_ptr(), bits.as_ptr())?;
        let tray = TRAY.get_or_init(|| Tray {
            host,
            status,
            mirrored,
            taskbar_created: RegisterWindowMessageW(w!("TaskbarCreated")),
            icon: icon.0 as isize,
        });
        // a real (never shown) window: message-only windows do not hear "TaskbarCreated"
        let hwnd = CreateWindowExW(
            WINDOW_EX_STYLE(0),
            class,
            w!("MusicFrame"),
            WS_OVERLAPPED,
            0,
            0,
            0,
            0,
            None,
            None,
            Some(instance.into()),
            None,
        )?;
        // at logon the taskbar may not be there yet; "TaskbarCreated" adds the icon then
        let _ = Shell_NotifyIconW(NIM_ADD, &data(hwnd, tray));
        SetTimer(Some(hwnd), 1, 1000, None);
        let mut message = MSG::default();
        while GetMessageW(&mut message, None, 0, 0).0 > 0 {
            let _ = TranslateMessage(&message);
            DispatchMessageW(&message);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tooltip_is_cut_between_characters() {
        let t = tip(&"🐧".repeat(100));
        assert_eq!(t[127], 0);
        assert_eq!(String::from_utf16(&t[..126]).unwrap(), "🐧".repeat(63));
        assert_eq!(pixels().len(), 32 * 32 * 4);
    }

    #[test]
    fn menu_lines_are_short_and_keep_ampersands() {
        assert_eq!(label("Tom & Jerry"), "Tom && Jerry");
        assert_eq!(label(&"ă".repeat(80)), format!("{}…", "ă".repeat(60)));
    }
}
