"""Query embeddings via an explicitly configured OpenAI-compatible HTTP API.

No MCP or database dependency. Local endpoints may omit an API key.
"""

from __future__ import annotations

import asyncio
import math
import os
import struct
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

MAX_QUERY_CHARS = 8000
MAX_DIMENSIONS = 16000
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}


class EmbeddingError(RuntimeError):
    """Embedding configuration, transport or response failure."""


class EmbeddingConfigError(EmbeddingError):
    """Missing or incompatible embedding configuration."""


@dataclass(frozen=True)
class EmbeddingSettings:
    base_url: str = field(repr=False)
    model: str
    revision: str
    dimensions: int
    api_key: str = field(default="", repr=False)
    provider: str = "openai-compatible"
    timeout: float = 15.0
    max_retries: int = 2
    retry_backoff: float = 0.5

    @classmethod
    def from_env(cls) -> EmbeddingSettings:
        values = {}
        for name in ("BASE_URL", "MODEL", "REVISION", "DIMENSIONS"):
            value = os.environ.get(f"EMBEDDING_{name}", "").strip()
            if not value:
                raise EmbeddingConfigError(f"Set EMBEDDING_{name} to enable memory search.")
            values[name] = value
        try:
            dimensions = int(values["DIMENSIONS"])
            timeout = float(os.environ.get("EMBEDDING_TIMEOUT", "15"))
        except ValueError:
            raise EmbeddingConfigError("EMBEDDING_DIMENSIONS must be an integer and EMBEDDING_TIMEOUT a number.") from None
        provider = os.environ.get("EMBEDDING_PROVIDER", "openai-compatible").strip()
        return cls(
            base_url=values["BASE_URL"].rstrip("/"), model=values["MODEL"],
            revision=values["REVISION"], dimensions=dimensions,
            api_key=os.environ.get("EMBEDDING_API_KEY", "").strip(), provider=provider, timeout=timeout,
        )

    def __post_init__(self) -> None:
        """Frozen settings are checked once when constructed."""
        url = urlsplit(self.base_url)
        if url.scheme not in {"http", "https"} or not url.hostname or url.username or url.password or url.query or url.fragment:
            raise EmbeddingConfigError("EMBEDDING_BASE_URL must be an HTTP(S) base URL without credentials, query or fragment.")
        if self.provider != "openai-compatible":
            raise EmbeddingConfigError("Only EMBEDDING_PROVIDER=openai-compatible is supported in this version.")
        if not self.model.strip() or not self.revision.strip():
            raise EmbeddingConfigError("Embedding model and revision must not be empty.")
        if isinstance(self.dimensions, bool) or not isinstance(self.dimensions, int) or not 1 <= self.dimensions <= MAX_DIMENSIONS:
            raise EmbeddingConfigError(f"EMBEDDING_DIMENSIONS must be between 1 and {MAX_DIMENSIONS}.")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise EmbeddingConfigError("EMBEDDING_TIMEOUT must be finite and greater than 0.")


def validate_query(query: str) -> str:
    if not isinstance(query, str) or not query.strip():
        raise ValueError("query must be a non-empty string.")
    query = query.strip()
    if len(query) > MAX_QUERY_CHARS or "\x00" in query:
        raise ValueError(f"query must contain at most {MAX_QUERY_CHARS} characters and no NUL characters.")
    return query


def validate_vector(value: Any, dimensions: int) -> list[float]:
    """Check the actual float32 representation used by pgvector, including underflow."""
    if not isinstance(value, list) or len(value) != dimensions:
        raise EmbeddingError("Embedding response dimensions do not match EMBEDDING_DIMENSIONS.")
    vector = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, (int, float)):
            raise EmbeddingError("Embedding response must contain only finite numbers.")
        try:
            number = struct.unpack("f", struct.pack("f", item))[0]
        except (OverflowError, struct.error):
            raise EmbeddingError("Embedding value is outside the float32 range.") from None
        if not math.isfinite(number):
            raise EmbeddingError("Embedding response must contain only finite numbers.")
        vector.append(number)
    if not any(vector):
        raise EmbeddingError("A zero embedding cannot be used for cosine search.")
    return vector


async def embed_query(
    query: str, *, settings: EmbeddingSettings | None = None, client: httpx.AsyncClient | None = None,
) -> list[float]:
    query = validate_query(query)
    settings = settings or EmbeddingSettings.from_env()
    if client is None:
        async with httpx.AsyncClient(timeout=settings.timeout) as own_client:
            return await _request_embedding(own_client, query, settings)
    return await _request_embedding(client, query, settings)


async def _request_embedding(client: httpx.AsyncClient, query: str, settings: EmbeddingSettings) -> list[float]:
    headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
    # Do not send dimensions: many compatible models only support their native size.
    # Validate the returned size rather than silently truncating or padding vectors.
    payload = {"input": query, "model": settings.model, "encoding_format": "float"}
    for attempt in range(settings.max_retries + 1):
        if attempt:
            await asyncio.sleep(settings.retry_backoff * 2 ** (attempt - 1))
        try:
            response = await client.post(
                f"{settings.base_url.rstrip('/')}/embeddings", json=payload,
                headers=headers, timeout=settings.timeout,
            )
        except httpx.TransportError:
            if attempt < settings.max_retries:
                continue
            raise EmbeddingError("Could not reach the embedding service or the request timed out.") from None
        if response.status_code in _RETRYABLE_STATUS and attempt < settings.max_retries:
            continue
        if response.status_code in {401, 403}:
            raise EmbeddingConfigError("Embedding service rejected authentication. Check EMBEDDING_API_KEY.")
        if response.status_code != 200:
            raise EmbeddingError(f"Embedding service rejected the request (HTTP {response.status_code}).")
        try:
            data = response.json()
            items = data["data"]
            if not isinstance(items, list) or len(items) != 1 or items[0]["index"] != 0:
                raise ValueError
            value = items[0]["embedding"]
        except (ValueError, KeyError, TypeError, IndexError):
            raise EmbeddingError("Embedding service returned an unexpected response format.") from None
        return validate_vector(value, settings.dimensions)
    raise EmbeddingError("Embedding service retries exhausted.")
