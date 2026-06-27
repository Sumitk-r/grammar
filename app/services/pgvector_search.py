from __future__ import annotations

from collections.abc import Iterable
from difflib import SequenceMatcher
import math
import re
from types import SimpleNamespace
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import Category, Course, Lesson, Transcript, TranscriptEmbedding, Unit, Video
from app.services.embeddings import embed_text


def vector_literal(vector: Iterable[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in vector) + "]"


def is_postgres(db: Session) -> bool:
    bind = db.get_bind()
    return bind.dialect.name == "postgresql"


def pgvector_available(db: Session) -> bool:
    if not is_postgres(db):
        return False
    try:
        row = db.execute(
            text(
                """
                SELECT
                    EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector') AS has_type,
                    EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = 'transcript_embeddings'
                          AND column_name = 'embedding'
                    ) AS has_column
                """
            )
        ).mappings().one()
        return bool(row["has_type"] and row["has_column"])
    except Exception:
        return False


def _postgres_column_exists(db: Session, table_name: str, column_name: str) -> bool:
    if not is_postgres(db):
        return False
    try:
        return bool(
            db.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_name = :table_name
                          AND column_name = :column_name
                    )
                    """
                ),
                {"table_name": table_name, "column_name": column_name},
            ).scalar()
        )
    except Exception:
        return False


def _tokens(value: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", value.lower())


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    total = sum(a * b for a, b in zip(left, right))
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return total / (left_norm * right_norm)


def _lexical_score(query: str, haystack: str, title_text: str) -> float:
    query_lower = query.lower()
    haystack_lower = haystack.lower()
    title_lower = title_text.lower()
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0

    haystack_tokens = set(_tokens(haystack))
    overlap = 0
    for token in query_tokens:
        if token in haystack_tokens or _has_fuzzy_token_match(token, haystack_tokens):
            overlap += 1
    overlap = overlap / len(query_tokens)
    phrase_bonus = 0.35 if query_lower in haystack_lower else 0.0
    title_bonus = 0.25 if any(token in title_lower for token in query_tokens) else 0.0
    return min(1.0, (overlap * 0.65) + phrase_bonus + title_bonus)


def _has_fuzzy_token_match(token: str, candidates: set[str]) -> bool:
    if len(token) < 5:
        return False
    for candidate in candidates:
        if abs(len(candidate) - len(token)) > 2:
            continue
        if SequenceMatcher(None, token, candidate).ratio() >= 0.84:
            return True
    return False


def _search_result(
    *,
    video: Video,
    lesson: Lesson,
    unit: Unit,
    course: Course,
    category: Category | None,
    transcript: Transcript,
    excerpt: str,
    search_mode: str,
    lexical_score: float,
    semantic_score: float,
    start_time_seconds: float | None = None,
    end_time_seconds: float | None = None,
    source_url: str | None = None,
    highlighted_snippet: str | None = None,
    relevance_score: float | None = None,
) -> dict[str, Any]:
    combined_score = (
        relevance_score
        if relevance_score is not None
        else (float(lexical_score or 0) * 0.60) + (float(semantic_score or 0) * 0.40)
    )
    return {
        "video_id": video.id,
        "video_title": video.title,
        "source_url": source_url,
        "course_id": course.id,
        "course_title": course.title,
        "category_id": category.id if category else None,
        "category_name": category.name if category else None,
        "unit_id": unit.id,
        "unit_title": unit.title,
        "lesson_id": lesson.id,
        "lesson_title": lesson.title,
        "scrape_status": video.scrape_status,
        "transcript_source": transcript.source,
        "excerpt": excerpt or "",
        "highlighted_snippet": highlighted_snippet or excerpt or "",
        "start_time_seconds": start_time_seconds,
        "end_time_seconds": end_time_seconds,
        "search_mode": search_mode,
        "lexical_score": round(float(lexical_score or 0), 6),
        "semantic_score": round(float(semantic_score or 0), 6),
        "relevance_score": round(float(combined_score or 0), 6),
    }


def _json_vector_hybrid_search_transcripts(
    db: Session,
    course_id: str | None,
    category_id: str | None,
    query: str,
    unit_id: str | None,
    lesson_id: str | None,
    transcript_source: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    query_vector = embed_text(query)
    rows_query = (
        select(TranscriptEmbedding, Transcript, Video, Lesson, Unit, Course, Category)
        .join(Transcript, Transcript.id == TranscriptEmbedding.transcript_id)
        .join(Video, Video.id == Transcript.video_id)
        .join(Lesson, Lesson.id == Video.lesson_id)
        .join(Unit, Unit.id == Lesson.unit_id)
        .join(Course, Course.id == Unit.course_id)
        .outerjoin(Category, Category.id == Course.category_id)
    )
    if course_id:
        rows_query = rows_query.where(Unit.course_id == course_id)
    if category_id:
        rows_query = rows_query.where(Course.category_id == category_id)
    if unit_id:
        rows_query = rows_query.where(Unit.id == unit_id)
    if lesson_id:
        rows_query = rows_query.where(Lesson.id == lesson_id)
    if transcript_source:
        rows_query = rows_query.where(Transcript.source == transcript_source)

    ranked: list[tuple[float, dict[str, Any]]] = []
    seen: set[tuple[str, int]] = set()
    for embedding, transcript, video, lesson, unit, course, category in db.execute(rows_query):
        if not embedding.vector:
            continue
        title_text = " ".join(
            part
            for part in (
                category.name if category else "",
                course.title,
                unit.title,
                lesson.title,
                video.title,
            )
            if part
        )
        haystack = f"{title_text} {embedding.text}"
        lexical_score = _lexical_score(query, haystack, title_text)
        semantic_score = max(0.0, _cosine_similarity(query_vector, embedding.vector))
        combined_score = (lexical_score * 0.60) + (semantic_score * 0.40)
        if lexical_score <= 0 and semantic_score < 0.18:
            continue

        key = (video.id, embedding.chunk_index)
        if key in seen:
            continue
        seen.add(key)
        ranked.append(
            (
                combined_score,
                _search_result(
                    video=video,
                    lesson=lesson,
                    unit=unit,
                    course=course,
                    category=category,
                    transcript=transcript,
                    excerpt=embedding.text[:360],
                    search_mode="local_hybrid",
                    lexical_score=lexical_score,
                    semantic_score=semantic_score,
                    start_time_seconds=embedding.start_time_seconds,
                    end_time_seconds=embedding.end_time_seconds,
                    source_url=embedding.source_url or video.full_url,
                    relevance_score=combined_score,
                ),
            )
        )

    ranked.sort(key=lambda item: item[0], reverse=True)
    return [result for _, result in ranked[:limit]]


def _search_result_from_pgvector_row(row: Any) -> dict[str, Any]:
    return _search_result(
        video=SimpleNamespace(
            id=row["video_id"],
            title=row["video_title"],
            scrape_status=row["scrape_status"],
        ),
        lesson=SimpleNamespace(
            id=row["lesson_id"],
            title=row["lesson_title"],
        ),
        unit=SimpleNamespace(
            id=row["unit_id"],
            title=row["unit_title"],
        ),
        course=SimpleNamespace(
            id=row["course_id"],
            title=row["course_title"],
        ),
        category=(
            SimpleNamespace(
                id=row["category_id"],
                name=row["category_name"],
            )
            if row["category_id"]
            else None
        ),
        transcript=SimpleNamespace(source=row["transcript_source"]),
        excerpt=row["excerpt"] or "",
        highlighted_snippet=row["highlighted_snippet"] or row["excerpt"] or "",
        search_mode="pgvector_hybrid",
        lexical_score=float(row["lexical_score"] or 0),
        semantic_score=float(row["semantic_score"] or 0),
        start_time_seconds=row["start_time_seconds"],
        end_time_seconds=row["end_time_seconds"],
        source_url=row["source_url"],
        relevance_score=float(row["relevance_score"] or 0),
    )


def sync_pgvector_embeddings(
    db: Session,
    embeddings: Iterable[TranscriptEmbedding],
) -> None:
    if is_postgres(db) and _postgres_column_exists(db, "transcript_embeddings", "search_vector"):
        for embedding in embeddings:
            if not embedding.id:
                continue
            db.execute(
                text(
                    "UPDATE transcript_embeddings "
                    "SET search_vector = to_tsvector("
                    "'english', concat_ws(' ', video_title, text)"
                    ") "
                    "WHERE id = :id"
                ),
                {"id": embedding.id},
            )

    if not pgvector_available(db):
        return
    for embedding in embeddings:
        if not embedding.id or not embedding.vector:
            continue
        db.execute(
            text(
                "UPDATE transcript_embeddings "
                "SET embedding = CAST(:embedding AS vector) "
                "WHERE id = :id"
            ),
            {"id": embedding.id, "embedding": vector_literal(embedding.vector)},
        )


def backfill_pgvector_embeddings(db: Session) -> None:
    if is_postgres(db) and _postgres_column_exists(db, "transcript_embeddings", "search_vector"):
        db.execute(
            text(
                "UPDATE transcript_embeddings te "
                "SET video_id = v.id, "
                "video_title = COALESCE(te.video_title, v.title), "
                "source_url = COALESCE(te.source_url, v.full_url), "
                "search_vector = to_tsvector("
                "'english', concat_ws(' ', COALESCE(te.video_title, v.title), te.text)"
                ") "
                "FROM transcripts t "
                "JOIN videos v ON v.id = t.video_id "
                "WHERE te.transcript_id = t.id "
                "AND (te.search_vector IS NULL OR te.video_id IS NULL)"
            )
        )

    if not pgvector_available(db):
        return

    embeddings = db.execute(
        text(
            "SELECT id, vector FROM transcript_embeddings "
            "WHERE embedding IS NULL AND vector IS NOT NULL"
        )
    ).mappings()
    for row in embeddings:
        db.execute(
            text(
                "UPDATE transcript_embeddings "
                "SET embedding = CAST(:embedding AS vector) "
                "WHERE id = :id"
            ),
            {"id": row["id"], "embedding": vector_literal(row["vector"])},
        )


def hybrid_search_transcripts(
    db: Session,
    course_id: str | None,
    category_id: str | None,
    query: str,
    unit_id: str | None,
    lesson_id: str | None,
    transcript_source: str | None,
    limit: int,
) -> list[dict[str, Any]] | None:
    if not query.strip():
        return None

    if not pgvector_available(db):
        return _json_vector_hybrid_search_transcripts(
            db,
            course_id=course_id,
            category_id=category_id,
            query=query,
            unit_id=unit_id,
            lesson_id=lesson_id,
            transcript_source=transcript_source,
            limit=limit,
        )

    query_embedding = vector_literal(embed_text(query))
    rows = db.execute(
        text(
            """
            WITH search_query AS (
                SELECT
                    websearch_to_tsquery('english', :query) AS tsq,
                    CAST(:embedding AS vector) AS embedding
            ),
            ranked AS (
                SELECT
                    v.id AS video_id,
                    v.title AS video_title,
                    v.scrape_status AS scrape_status,
                    c.id AS course_id,
                    c.title AS course_title,
                    cat.id AS category_id,
                    cat.name AS category_name,
                    l.id AS lesson_id,
                    l.title AS lesson_title,
                    u.id AS unit_id,
                    u.title AS unit_title,
                    t.source AS transcript_source,
                    te.text AS excerpt,
                    te.source_url AS source_url,
                    te.start_time_seconds AS start_time_seconds,
                    te.end_time_seconds AS end_time_seconds,
                    ts_headline(
                        'english',
                        te.text,
                        search_query.tsq,
                        'StartSel=<mark>, StopSel=</mark>, MaxWords=45, MinWords=12'
                    ) AS highlighted_snippet,
                    ts_rank_cd(
                        COALESCE(
                            te.search_vector,
                            to_tsvector(
                                'english',
                                concat_ws(' ', v.title, l.title, u.title, te.text)
                            )
                        ),
                        search_query.tsq
                    ) AS lexical_score,
                    1 - (te.embedding <=> search_query.embedding) AS semantic_score,
                    (
                        ts_rank_cd(
                            COALESCE(
                                te.search_vector,
                                to_tsvector(
                                    'english',
                                    concat_ws(' ', v.title, l.title, u.title, te.text)
                                )
                            ),
                            search_query.tsq
                        ) * 0.55
                    ) + ((1 - (te.embedding <=> search_query.embedding)) * 0.45)
                    AS relevance_score
                FROM transcript_embeddings te
                JOIN transcripts t ON t.id = te.transcript_id
                JOIN videos v ON v.id = t.video_id
                JOIN lessons l ON l.id = v.lesson_id
                JOIN units u ON u.id = l.unit_id
                JOIN courses c ON c.id = u.course_id
                LEFT JOIN categories cat ON cat.id = c.category_id
                CROSS JOIN search_query
                WHERE (:course_id IS NULL OR u.course_id = :course_id)
                  AND (:category_id IS NULL OR c.category_id = :category_id)
                  AND te.embedding IS NOT NULL
                  AND (:unit_id IS NULL OR u.id = :unit_id)
                  AND (:lesson_id IS NULL OR l.id = :lesson_id)
                  AND (:transcript_source IS NULL OR t.source = :transcript_source)
            )
            SELECT *
            FROM ranked
            WHERE lexical_score > 0 OR semantic_score IS NOT NULL
            ORDER BY relevance_score DESC
            LIMIT :limit
            """
        ),
        {
            "course_id": course_id,
            "category_id": category_id,
            "query": query,
            "embedding": query_embedding,
            "unit_id": unit_id,
            "lesson_id": lesson_id,
            "transcript_source": transcript_source,
            "limit": limit,
        },
    ).mappings()

    return [_search_result_from_pgvector_row(row) for row in rows]
