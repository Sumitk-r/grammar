from __future__ import annotations

from pathlib import Path
from typing import Any

from app.config import settings


def add_cookiefile(options: dict[str, Any]) -> dict[str, Any]:
    cookiefile = (settings.yt_dlp_cookies_file or "").strip()
    if not cookiefile:
        return options

    path = Path(cookiefile).expanduser()
    if path.exists():
        options["cookiefile"] = str(path)
    return options
