from __future__ import annotations

import hashlib
import math

from app.config import settings


EMBEDDING_MODEL = "local-hash-v1"
EMBEDDING_DIMENSIONS = settings.embedding_dimensions
MAX_CHUNK_CHARACTERS = 1200


def chunk_text(text: str, max_characters: int = MAX_CHUNK_CHARACTERS) -> list[str]:
    words = text.split()
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for word in words:
        next_length = current_length + len(word) + (1 if current else 0)
        if current and next_length > max_characters:
            chunks.append(" ".join(current))
            current = [word]
            current_length = len(word)
        else:
            current.append(word)
            current_length = next_length

    if current:
        chunks.append(" ".join(current))
    return chunks


def embed_text(text: str, dimensions: int = EMBEDDING_DIMENSIONS) -> list[float]:
    vector = [0.0] * dimensions
    for token in text.lower().split():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[bucket] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if not norm:
        return vector
    return [round(value / norm, 6) for value in vector]
