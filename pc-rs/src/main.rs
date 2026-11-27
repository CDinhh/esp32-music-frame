#![windows_subsystem = "windows"] // no console window; the app lives in the tray

mod app;
mod clean;
mod config;
mod img;
mod log;
mod media;
mod model;
mod motion;
mod parse;
mod render;
mod screen;
mod sources;
mod text;
mod tray;

/// The tray icon on this thread, the frame loop on another.
fn start() {
    if !tray::only_instance() {
        return;
    }
    // there is no console: a crash would otherwise leave no trace
    std::panic::set_hook(Box::new(|info| log::line(&format!("crashed: {info}"))));
    let status = app::Status::default();
    let (host, shown) = (host(), status.clone());
    let screen = host.clone();
    std::thread::spawn(move || app::run(&screen, shown));
    if let Err(e) = tray::run(host, status) {
        log::line(&format!("tray icon: {e}"));
    }
}

fn host() -> String {
    let set = config::get("SCREEN_HOST");
    if set.is_empty() {
        "musicframe.local".into()
    } else {
        set
    }
}

/// `--png out.png`: one frame of what is playing now.
fn one_png(path: &str) -> Result<String, String> {
    let mut np = media::Reader::default().read("").map_err(|e| e.to_string())?.ok_or("nothing is playing")?;
    app::clean(&mut np);
    let order: Vec<String> = sources::IDS.iter().map(|s| s.to_string()).collect();
    let lyrics = sources::fetch(&sources::internet, &np.title, &np.artist, np.duration_ms, &order, true);
    let source = lyrics.as_ref().map_or("none", |l| l.source.as_str()).to_string();
    let pos = model::interpolate(&np, app::unix_now());
    let frame = render::Renderer::default().screen(&np, pos, lyrics.as_ref(), true, None, 10.0);
    image::save_buffer(path, &frame.px, render::W as u32, render::H as u32, image::ExtendedColorType::Rgb8)
        .map_err(|e| e.to_string())?;
    Ok(format!("{} - {}: lyrics {source}", np.artist, np.title))
}

/// `--lyrics artist title`: what each source answers, to check them against the real services.
fn check_lyrics(artist: &str, title: &str) {
    for id in sources::IDS {
        let began = std::time::Instant::now();
        let found = sources::fetch(&sources::internet, title, artist, 0, &[id.to_string()], true);
        let took = began.elapsed().as_millis();
        match found {
            Some(l) => println!("{id}: {} lines, word={}, {took} ms: {}", l.lines.len(), l.word_synced, l.lines[0].text),
            None => println!("{id}: none, {took} ms"),
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let args: Vec<&str> = args.iter().map(String::as_str).collect();
    match args.as_slice() {
        ["--png", path] => println!("{}", one_png(path).unwrap_or_else(|e| e)),
        ["--lyrics", artist, title] => check_lyrics(artist, title),
        _ => start(),
    }
}
