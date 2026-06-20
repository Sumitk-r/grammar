from app.services.youtube_client import YouTubeCaptionClient


class FakeSnippet:
    text = "Fallback transcript text."
    start = 2.0
    duration = 4.0


class FakeFetchedTranscript:
    language_code = "hi"

    def __iter__(self):
        return iter([FakeSnippet()])


class FakeAvailableTranscript:
    def fetch(self):
        return FakeFetchedTranscript()


class FakeTranscriptApi:
    def fetch(self, video_id, languages):
        raise RuntimeError("preferred language unavailable")

    def list(self, video_id):
        return [FakeAvailableTranscript()]


def test_youtube_client_falls_back_to_any_available_transcript():
    client = YouTubeCaptionClient(["en"])
    client.api = FakeTranscriptApi()

    result = client.fetch("abc123")

    assert result.plain_text == "Fallback transcript text."
    assert result.language_code == "hi"
    assert result.segments == [
        {
            "segment_index": 0,
            "start_time_seconds": 2.0,
            "end_time_seconds": 6.0,
            "text": "Fallback transcript text.",
        }
    ]
