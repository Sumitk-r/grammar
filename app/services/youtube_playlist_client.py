from __future__ import annotations

from dataclasses import dataclass

from yt_dlp import YoutubeDL

from app.services.yt_dlp_options import add_cookiefile


@dataclass(frozen=True)
class YouTubePlaylistVideo:
    video_index: int
    video_id: str
    title: str
    full_url: str
    duration_seconds: int | None = None


@dataclass(frozen=True)
class YouTubePlaylistData:
    playlist_id: str
    title: str
    source_url: str
    description: str | None
    videos: list[YouTubePlaylistVideo]


class YouTubePlaylistClient:
    def fetch_playlist(self, playlist_id: str) -> YouTubePlaylistData:
        source_url = f"https://www.youtube.com/playlist?list={playlist_id}"
        options = {
            "quiet": True,
            "no_warnings": True,
            "ignoreerrors": True,
            "extract_flat": "in_playlist",
            "skip_download": True,
        }
        with YoutubeDL(add_cookiefile(options)) as ydl:
            info = ydl.extract_info(source_url, download=False)

        entries = []
        for entry in info.get("entries") or []:
            if not entry:
                continue
            video_id = entry.get("id") or entry.get("url")
            if not video_id:
                continue
            video_id = str(video_id)
            full_url = entry.get("webpage_url") or f"https://www.youtube.com/watch?v={video_id}"
            duration = entry.get("duration")
            entries.append(
                YouTubePlaylistVideo(
                    video_index=len(entries) + 1,
                    video_id=video_id,
                    title=entry.get("title") or f"YouTube video {video_id}",
                    full_url=full_url,
                    duration_seconds=int(duration) if duration is not None else None,
                )
            )

        if not entries:
            raise RuntimeError("No videos were found in this YouTube playlist.")

        return YouTubePlaylistData(
            playlist_id=playlist_id,
            title=info.get("title") or f"YouTube playlist {playlist_id}",
            source_url=source_url,
            description=info.get("description"),
            videos=entries,
        )
