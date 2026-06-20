from __future__ import annotations

import time

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.config import settings
from app.database import SessionLocal
from app.models import Lesson, Transcript, TranscriptEmbedding, TranscriptSegment, Unit, Video
from app.services.embeddings import (
    EMBEDDING_DIMENSIONS,
    EMBEDDING_MODEL,
    chunk_text,
    embed_text,
)
from app.services.pgvector_search import sync_pgvector_embeddings
from app.services.youtube_client import (
    YouTubeCaptionClient,
    YouTubeCaptionUnavailable,
)


def _store_youtube_transcript(db, video, result) -> None:
    transcript = video.transcript
    if transcript is None:
        transcript = Transcript(video=video, plain_text=result.plain_text)
        db.add(transcript)
    else:
        transcript.plain_text = result.plain_text
        transcript.segments.clear()
        transcript.embeddings.clear()
    transcript.source = "youtube_captions"
    transcript.language_code = result.language_code
    db.flush()
    for segment in result.segments:
        transcript.segments.append(TranscriptSegment(**segment))
    for chunk_index, chunk in enumerate(chunk_text(result.plain_text)):
        transcript.embeddings.append(
            TranscriptEmbedding(
                chunk_index=chunk_index,
                text=chunk,
                model=EMBEDDING_MODEL,
                dimensions=EMBEDDING_DIMENSIONS,
                vector=embed_text(chunk),
            )
        )
    db.flush()
    sync_pgvector_embeddings(db, transcript.embeddings)


def backfill_missing_youtube_captions(
    course_id: str,
    limit: int | None = None,
    client: YouTubeCaptionClient | None = None,
) -> dict[str, int]:
    if client is None:
        languages = [
            language.strip()
            for language in settings.youtube_languages.split(",")
            if language.strip()
        ]
        client = YouTubeCaptionClient(languages)

    fetched = 0
    unavailable = 0
    skipped = 0

    with SessionLocal() as db:
        query = (
            select(Video)
            .join(Lesson)
            .join(Unit)
            .where(
                Unit.course_id == course_id,
                Video.transcript == None,  # noqa: E711
            )
            .options(selectinload(Video.transcript))
            .order_by(Unit.unit_index, Lesson.lesson_index, Video.video_index)
        )
        if limit is not None:
            query = query.limit(limit)
        video_ids = list(db.scalars(query))

    for video_stub in video_ids:
        with SessionLocal() as db:
            video = db.get(Video, video_stub.id)
            if video is None or video.transcript is not None:
                skipped += 1
                continue
            if not video.youtube_id:
                video.scrape_status = "no_transcript"
                video.scrape_error = "No YouTube video ID is available."
                unavailable += 1
                db.commit()
                continue
            try:
                result = client.fetch(video.youtube_id)
                _store_youtube_transcript(db, video, result)
                video.scrape_status = "completed"
                video.scrape_error = None
                fetched += 1
            except YouTubeCaptionUnavailable as exc:
                video.scrape_status = "no_transcript"
                video.scrape_error = f"YouTube captions unavailable: {exc}"
                unavailable += 1
            db.commit()
        if settings.request_delay_seconds:
            time.sleep(settings.request_delay_seconds)

    return {
        "fetched": fetched,
        "unavailable": unavailable,
        "skipped": skipped,
        "attempted": fetched + unavailable,
    }
