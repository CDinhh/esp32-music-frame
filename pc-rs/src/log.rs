//! One log file beside the exe; started over when it passes 1 MB.
use std::io::Write;
use std::path::Path;
use std::sync::Mutex;
use windows::Win32::System::SystemInformation::GetLocalTime;

pub const FILE: &str = "musicframe.log";
const LIMIT: u64 = 1 << 20;
static LOCK: Mutex<()> = Mutex::new(());

fn append(path: &Path, line: &str) {
    let full = std::fs::metadata(path).is_ok_and(|m| m.len() > LIMIT);
    let mut options = std::fs::OpenOptions::new();
    options.create(true);
    if full {
        options.write(true).truncate(true);
    } else {
        options.append(true);
    }
    if let Ok(mut file) = options.open(path) {
        let _ = writeln!(file, "{line}");
    }
}

pub fn line(message: &str) {
    // SAFETY: no arguments, returns a plain struct
    let t = unsafe { GetLocalTime() };
    let text = format!("{:02}-{:02} {:02}:{:02}:{:02} {message}", t.wMonth, t.wDay, t.wHour, t.wMinute, t.wSecond);
    let _guard = LOCK.lock().unwrap_or_else(|e| e.into_inner());
    if cfg!(test) {
        println!("{text}");
    } else {
        append(&crate::config::exe_dir().join(FILE), &text);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn starts_over_past_the_limit() {
        let path = std::env::temp_dir().join(format!("musicframe-log-{}", std::process::id()));
        std::fs::write(&path, vec![b'x'; LIMIT as usize + 1]).unwrap();
        append(&path, "new");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "new\n");
        append(&path, "more");
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "new\nmore\n");
        std::fs::remove_file(path).unwrap();
    }
}
