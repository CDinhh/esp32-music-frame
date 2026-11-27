//! Now playing from the Windows media session (the box next to the volume flyout).
use crate::model::NowPlaying;
use std::sync::Arc;
use windows::Media::Control::{
    GlobalSystemMediaTransportControlsSession as Session, GlobalSystemMediaTransportControlsSessionManager as Manager,
    GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status,
};
use windows::Storage::Streams::{DataReader, IRandomAccessStreamReference};

const MAX_COVER: u64 = 8 << 20;

/// Index of the session to show, from (app id, is playing) pairs. Windows' "current" session jumps to
/// whichever app touched media last (a browser previewing a video on hover), so stay with `stick_to`
/// while it plays, else take a playing one.
fn pick(sessions: &[(String, bool)], current: Option<usize>, stick_to: &str) -> Option<usize> {
    sessions
        .iter()
        .position(|s| !stick_to.is_empty() && s.0 == stick_to && s.1)
        .or_else(|| current.filter(|&i| sessions.get(i).is_some_and(|s| s.1)))
        .or_else(|| sessions.iter().position(|s| s.1))
        .or(current)
}

fn read_cover(reference: IRandomAccessStreamReference) -> windows::core::Result<Option<Vec<u8>>> {
    let stream = reference.OpenReadAsync()?.join()?;
    let size = stream.Size()?;
    if size == 0 || size > MAX_COVER {
        return Ok(None);
    }
    let reader = DataReader::CreateDataReader(&stream)?;
    reader.LoadAsync(size as u32)?.join()?;
    let mut data = vec![0; size as usize];
    reader.ReadBytes(&mut data)?;
    Ok(Some(data))
}

fn describe(session: &Session) -> (String, bool) {
    let app = session.SourceAppUserModelId().map(|s| s.to_string()).unwrap_or_default();
    let playing = session.GetPlaybackInfo().and_then(|i| i.PlaybackStatus()).is_ok_and(|s| s == Status::Playing);
    (app, playing)
}

/// 100 ns ticks since 1601 to unix seconds.
fn unix_seconds(ticks: i64) -> f64 {
    ticks as f64 / 1e7 - 11_644_473_600.0
}

#[derive(Default)]
pub struct Reader {
    manager: Option<Manager>,
}

impl Reader {
    /// What is playing, None if nothing is. `stick_to` is the app already being shown.
    pub fn read(&mut self, stick_to: &str) -> windows::core::Result<Option<NowPlaying>> {
        if self.manager.is_none() {
            self.manager = Some(Manager::RequestAsync()?.join()?);
        }
        let manager = self.manager.as_ref().expect("just set");
        let sessions: Vec<Session> = manager.GetSessions()?.into_iter().collect();
        let described: Vec<(String, bool)> = sessions.iter().map(describe).collect();
        // no current session is reported as an error
        let current = manager.GetCurrentSession().ok().map(|s| describe(&s).0);
        let current = current.and_then(|app| described.iter().position(|d| d.0 == app));
        let Some(i) = pick(&described, current, stick_to) else { return Ok(None) };
        let session = &sessions[i];
        let props = session.TryGetMediaPropertiesAsync()?.join()?;
        let title = props.Title()?.to_string();
        if title.is_empty() {
            return Ok(None);
        }
        let mut artist = props.Artist()?.to_string();
        if artist.is_empty() {
            artist = props.AlbumArtist()?.to_string();
        }
        let timeline = session.GetTimelineProperties()?;
        let ms = |ticks: i64| ticks / 10_000;
        Ok(Some(NowPlaying {
            title,
            artist,
            duration_ms: ms(timeline.EndTime()?.Duration - timeline.StartTime()?.Duration),
            position_ms: ms(timeline.Position()?.Duration),
            playing: described[i].1,
            // no thumbnail, or one that cannot be read, is simply no cover
            cover: props.Thumbnail().ok().and_then(|t| read_cover(t).ok().flatten()).map(Arc::new),
            updated_at: unix_seconds(timeline.LastUpdatedTime()?.UniversalTime),
            app: described[i].0.clone(),
        }))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn stays_with_the_shown_app_while_it_plays() {
        let s = |list: &[(&str, bool)]| list.iter().map(|&(a, p)| (a.to_string(), p)).collect::<Vec<_>>();
        let both = s(&[("browser", true), ("spotify", true)]);
        assert_eq!(pick(&both, Some(0), "spotify"), Some(1));
        assert_eq!(pick(&both, Some(0), ""), Some(0));
        let paused = s(&[("browser", false), ("spotify", false), ("other", true)]);
        assert_eq!(pick(&paused, Some(0), "spotify"), Some(2));
        assert_eq!(pick(&s(&[("browser", false)]), Some(0), "spotify"), Some(0));
        assert_eq!(pick(&[], None, "spotify"), None);
    }

    #[test]
    fn windows_ticks_become_unix_time() {
        assert_eq!(unix_seconds(116_444_736_000_000_000), 0.0);
        assert_eq!(unix_seconds(116_444_736_015_000_000), 1.5);
    }
}
