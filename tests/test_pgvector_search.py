from app.database import SessionLocal
from app.services.pgvector_search import hybrid_search_transcripts, vector_literal


def test_vector_literal_uses_pgvector_format():
    assert vector_literal([0.5, -1, 0]) == "[0.500000,-1.000000,0.000000]"


def test_hybrid_search_returns_none_without_pgvector():
    with SessionLocal() as db:
        result = hybrid_search_transcripts(
            db,
            course_id="course-id",
            query="nouns and verbs",
            unit_id=None,
            lesson_id=None,
            limit=10,
        )

    assert result is None
