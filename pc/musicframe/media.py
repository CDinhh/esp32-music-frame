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


async def read_now_playing(with_cover: bool = True) -> NowPlaying | None:
    global _manager
    if _manager is None:
        _manager = await Manager.request_async()
    s = _manager.get_current_session()
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
