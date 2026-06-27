from pathlib import Path

from app.database import SessionLocal
from app.importer import import_transcript_csv
from app.services.pgvector_search import hybrid_search_transcripts, vector_literal


def test_vector_literal_uses_pgvector_format():
    assert vector_literal([0.5, -1, 0]) == "[0.500000,-1.000000,0.000000]"


def test_hybrid_search_uses_local_vectors_without_pgvector():
    path = Path(__file__).parents[1] / "sample_transcripts.csv"
    with SessionLocal() as db:
        course, _ = import_transcript_csv(db, path)
        result = hybrid_search_transcripts(
            db,
            course_id=course.id,
            category_id=None,
            query="grammar voiceover",
            unit_id=None,
            lesson_id=None,
            transcript_source=None,
            limit=10,
        )

    assert result
    assert result[0]["video_title"] == "Introduction to grammar"
    assert result[0]["search_mode"] == "local_hybrid"
    assert "Voiceover" in result[0]["excerpt"]
    assert result[0]["source_url"].startswith("https://www.khanacademy.org/")
    assert result[0]["relevance_score"] > 0


def test_hybrid_search_handles_spelling_variation():
    path = Path(__file__).parents[1] / "sample_transcripts.csv"
    with SessionLocal() as db:
        course, _ = import_transcript_csv(db, path)
        result = hybrid_search_transcripts(
            db,
            course_id=course.id,
            category_id=None,
            query="grammer rules",
            unit_id=None,
            lesson_id=None,
            transcript_source=None,
            limit=10,
        )

    assert result
    assert result[0]["video_title"] == "Introduction to grammar"


def test_hybrid_search_returns_no_result_for_unrelated_query():
    path = Path(__file__).parents[1] / "sample_transcripts.csv"
    with SessionLocal() as db:
        course, _ = import_transcript_csv(db, path)
        result = hybrid_search_transcripts(
            db,
            course_id=course.id,
            category_id=None,
            query="xqzv blorfzz nomatch",
            unit_id=None,
            lesson_id=None,
            transcript_source=None,
            limit=10,
        )

    assert result == []
