from __future__ import annotations

from typing import Any

from app.models import Transcript, TranscriptEmbedding, Video
from app.services.embeddings import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    MAX_CHUNK_CHARACTERS,
    chunk_text,
    embed_text,
)


def transcript_chunk_rows(
    transcript: Transcript,
    video: Video,
    plain_text: str,
    segments: list[dict[str, Any]] | None = None,
    max_characters: int = MAX_CHUNK_CHARACTERS,
) -> list[TranscriptEmbedding]:
    rows = []
    chunks = _timestamped_chunks(plain_text, segments or [], max_characters)
    for chunk_index, chunk in enumerate(chunks):
        rows.append(
            TranscriptEmbedding(
                transcript=transcript,
                video_id=video.id,
                video_title=video.title,
                source_url=video.full_url,
                chunk_index=chunk_index,
                start_time_seconds=chunk["start_time_seconds"],
                end_time_seconds=chunk["end_time_seconds"],
                text=chunk["text"],
                chunk_metadata=chunk["metadata"],
                model=EMBEDDING_MODEL,
                dimensions=EMBEDDING_DIMENSIONS,
                vector=embed_text(chunk["text"]),
            )
        )
    return rows


def rebuild_transcript_chunks(transcript: Transcript, video: Video) -> list[TranscriptEmbedding]:
    segments = [
        {
            "segment_index": segment.segment_index,
            "start_time_seconds": segment.start_time_seconds,
            "end_time_seconds": segment.end_time_seconds,
            "text": segment.text,
        }
        for segment in transcript.segments
    ]
    transcript.embeddings.clear()
    rows = transcript_chunk_rows(transcript, video, transcript.plain_text, segments)
    for row in rows:
        transcript.embeddings.append(row)
    return rows


def _timestamped_chunks(
    plain_text: str,
    segments: list[dict[str, Any]],
    max_characters: int,
) -> list[dict[str, Any]]:
    usable_segments = [
        segment
        for segment in segments
        if str(segment.get("text") or "").strip()
    ]
    if not usable_segments:
        return [
            {
                "text": chunk,
                "start_time_seconds": None,
                "end_time_seconds": None,
                "metadata": {"source": "plain_text"},
            }
            for chunk in chunk_text(plain_text, max_characters=max_characters)
        ]

    chunks = []
    current_text: list[str] = []
    current_start = None
    current_end = None
    current_segment_indexes: list[int] = []
    current_length = 0

    for segment in usable_segments:
        text = str(segment.get("text") or "").strip()
        next_length = current_length + len(text) + (1 if current_text else 0)
        if current_text and next_length > max_characters:
            chunks.append(
                _chunk_payload(
                    current_text,
                    current_start,
                    current_end,
                    current_segment_indexes,
                )
            )
            current_text = []
            current_start = None
            current_end = None
            current_segment_indexes = []
            current_length = 0

        if current_start is None:
            current_start = segment.get("start_time_seconds")
        current_end = segment.get("end_time_seconds")
        current_text.append(text)
        current_segment_indexes.append(int(segment.get("segment_index") or 0))
        current_length += len(text) + (1 if current_length else 0)

    if current_text:
        chunks.append(
            _chunk_payload(
                current_text,
                current_start,
                current_end,
                current_segment_indexes,
            )
        )

    return chunks


def _chunk_payload(
    text_parts: list[str],
    start_time_seconds: float | None,
    end_time_seconds: float | None,
    segment_indexes: list[int],
) -> dict[str, Any]:
    return {
        "text": " ".join(text_parts).strip(),
        "start_time_seconds": start_time_seconds,
        "end_time_seconds": end_time_seconds,
        "metadata": {
            "source": "segments",
            "segment_start_index": min(segment_indexes) if segment_indexes else None,
            "segment_end_index": max(segment_indexes) if segment_indexes else None,
        },
    }
