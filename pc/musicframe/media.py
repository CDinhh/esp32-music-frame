"""Now playing from the Windows media session (the box that shows up next to the volume flyout)."""
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionPlaybackStatus as Status
from winrt.windows.storage.streams import DataReader

from .model import NowPlaying

_manager = None


async def _read_cover(ref) -> bytes | None:
    if ref is None:
        return None
    try:
        stream = await ref.open_read_async()
        size = stream.size
        if not size:
            return None
        reader = DataReader(stream)
        await reader.load_async(size)
        buf = bytearray(size)
        reader.read_bytes(buf)
        return bytes(buf)
    except OSError:
        return None


def pick_session(sessions: list, current, stick_to: str):
    """The session to show. Windows' "current" session jumps to whichever app touched media last
    (a browser previewing a video on hover), so stay with `stick_to` while it plays, else take a playing one."""
    def playing(s):
        return s.get_playback_info().playback_status == Status.PLAYING

    for s in sessions:
        if stick_to and s.source_app_user_model_id == stick_to and playing(s):
            return s
    if current is not None and playing(current):
        return current
    return next((s for s in sessions if playing(s)), current)


async def read_now_playing(with_cover: bool = True, stick_to: str = "") -> NowPlaying | None:
    global _manager
    if _manager is None:
        _manager = await Manager.request_async()
    s = pick_session(list(_manager.get_sessions()), _manager.get_current_session(), stick_to)
    if s is None:
        return None
    props = await s.try_get_media_properties_async()
    if not props or not props.title:
        return None
    t = s.get_timeline_properties()
    pb = s.get_playback_info()
    return NowPlaying(
        title=props.title,
        artist=props.artist or props.album_artist or "",
        duration_ms=int((t.end_time - t.start_time).total_seconds() * 1000),
        position_ms=int(t.position.total_seconds() * 1000),
        playing=pb.playback_status == Status.PLAYING,
        cover=await _read_cover(props.thumbnail) if with_cover else None,
        updated_at=t.last_updated_time.timestamp(),
        app=s.source_app_user_model_id or "",
    )


def interpolate(np: NowPlaying, updated_at: float, now: float) -> int:
    """Windows only refreshes the position now and then; add the time played since."""
    pos = np.position_ms + (int((now - updated_at) * 1000) if np.playing else 0)
    if np.duration_ms > 0:
        pos = min(pos, np.duration_ms)
    return max(pos, 0)
