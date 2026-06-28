from app.config import settings
from app.services.yt_dlp_options import add_cookiefile


def test_add_cookiefile_uses_existing_cookie_file(tmp_path):
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    original = settings.yt_dlp_cookies_file
    settings.yt_dlp_cookies_file = str(cookie_file)
    try:
        options = add_cookiefile({"quiet": True})
    finally:
        settings.yt_dlp_cookies_file = original

    assert options["cookiefile"] == str(cookie_file)


def test_add_cookiefile_ignores_missing_cookie_file(tmp_path):
    original = settings.yt_dlp_cookies_file
    settings.yt_dlp_cookies_file = str(tmp_path / "missing.txt")
    try:
        options = add_cookiefile({"quiet": True})
    finally:
        settings.yt_dlp_cookies_file = original

    assert "cookiefile" not in options
