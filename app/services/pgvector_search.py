from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models import TranscriptEmbedding
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
        return bool(
            db.execute(
                text("SELECT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'vector')")
            ).scalar()
        )
    except Exception:
        return False


def sync_pgvector_embeddings(
    db: Session,
    embeddings: Iterable[TranscriptEmbedding],
) -> None:
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
    course_id: str,
    query: str,
    unit_id: str | None,
    lesson_id: str | None,
    limit: int,
) -> list[dict[str, Any]] | None:
    if not query.strip() or not pgvector_available(db):
        return None

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
                    l.id AS lesson_id,
                    l.title AS lesson_title,
                    u.id AS unit_id,
                    u.title AS unit_title,
                    t.source AS transcript_source,
                    te.text AS excerpt,
                    ts_rank_cd(
                        to_tsvector(
                            'english',
                            concat_ws(' ', v.title, l.title, u.title, te.text)
                        ),
                        search_query.tsq
                    ) AS lexical_score,
                    1 - (te.embedding <=> search_query.embedding) AS semantic_score
                FROM transcript_embeddings te
                JOIN transcripts t ON t.id = te.transcript_id
                JOIN videos v ON v.id = t.video_id
                JOIN lessons l ON l.id = v.lesson_id
                JOIN units u ON u.id = l.unit_id
                CROSS JOIN search_query
                WHERE u.course_id = :course_id
                  AND te.embedding IS NOT NULL
                  AND (:unit_id IS NULL OR u.id = :unit_id)
                  AND (:lesson_id IS NULL OR l.id = :lesson_id)
            )
            SELECT *
            FROM ranked
            WHERE lexical_score > 0 OR semantic_score IS NOT NULL
            ORDER BY ((lexical_score * 0.55) + (semantic_score * 0.45)) DESC
            LIMIT :limit
            """
        ),
        {
            "course_id": course_id,
            "query": query,
            "embedding": query_embedding,
            "unit_id": unit_id,
            "lesson_id": lesson_id,
            "limit": limit,
        },
    ).mappings()

    return [
        {
            "video_id": row["video_id"],
            "video_title": row["video_title"],
            "unit_id": row["unit_id"],
            "unit_title": row["unit_title"],
            "lesson_id": row["lesson_id"],
            "lesson_title": row["lesson_title"],
            "scrape_status": row["scrape_status"],
            "transcript_source": row["transcript_source"],
            "excerpt": row["excerpt"] or "",
            "search_mode": "hybrid",
            "lexical_score": float(row["lexical_score"] or 0),
            "semantic_score": float(row["semantic_score"] or 0),
        }
        for row in rows
    ]
