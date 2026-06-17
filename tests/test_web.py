from datetime import datetime, timezone

from app.routes.web import local_datetime


def test_local_datetime_renders_ist_label():
    rendered = local_datetime(datetime(2026, 6, 17, 9, 57, tzinfo=timezone.utc))

    assert rendered == "Jun 17, 2026 15:27 IST"

