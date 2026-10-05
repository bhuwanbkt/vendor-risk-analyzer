from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Sequence

from google import genai
from google.genai import types


logger = logging.getLogger(__name__)


DEFAULT_EMBEDDING_MODEL = "gemini-embedding-2"
DEFAULT_EMBEDDING_DIMENSIONS = 768
DEFAULT_MAX_ATTEMPTS = 3


class EmbeddingError(RuntimeError):
    """Raised when an embedding cannot be generated safely."""


class EmbeddingService:
    """
    Generate embeddings using the Gemini API.

    This service handles:

    - API-key validation
    - document formatting
    - query formatting
    - asynchronous API calls
    - retry/backoff
    - embedding dimension validation
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    ) -> None:
        self.api_key = (
            api_key
            or os.getenv("GEMINI_API_KEY")
        )

        self.model = (
            model
            or os.getenv(
                "EMBEDDING_MODEL",
                DEFAULT_EMBEDDING_MODEL,
            )
        )

        configured_dimensions = (
            dimensions
            or int(
                os.getenv(
                    "EMBEDDING_DIMENSIONS",
                    str(DEFAULT_EMBEDDING_DIMENSIONS),
                )
            )
        )

        self.dimensions = configured_dimensions
        self.max_attempts = max_attempts

        if not self.api_key:
            raise EmbeddingError(
                "GEMINI_API_KEY is required."
            )

        if self.dimensions <= 0:
            raise EmbeddingError(
                "EMBEDDING_DIMENSIONS must be greater than zero."
            )

        if self.max_attempts < 1:
            raise EmbeddingError(
                "max_attempts must be at least 1."
            )

        self.client = genai.Client(
            api_key=self.api_key,
        )

    @staticmethod
    def prepare_document(
        content: str,
        title: str | None = None,
    ) -> str:
        """
        Prepare text for document retrieval embeddings.

        Gemini Embedding 2 recommends:

            title: {title} | text: {content}
        """

        cleaned_content = content.strip()

        if not cleaned_content:
            raise EmbeddingError(
                "Cannot embed an empty document."
            )

        cleaned_title = (
            title.strip()
            if title and title.strip()
            else "none"
        )

        return (
            f"title: {cleaned_title} | "
            f"text: {cleaned_content}"
        )

    @staticmethod
    def prepare_query(
        query: str,
    ) -> str:
        """
        Prepare a user query for semantic retrieval.

        Gemini Embedding 2 recommends:

            task: search result | query: {content}
        """

        cleaned_query = query.strip()

        if not cleaned_query:
            raise EmbeddingError(
                "Cannot embed an empty query."
            )

        return (
            "task: search result | "
            f"query: {cleaned_query}"
        )

    def _validate_embedding(
        self,
        values: Sequence[float] | None,
    ) -> list[float]:
        """
        Validate that Gemini returned exactly the vector size
        our PostgreSQL vector(768) column expects.
        """

        if values is None:
            raise EmbeddingError(
                "Embedding provider returned no vector values."
            )

        vector = [
            float(value)
            for value in values
        ]

        if len(vector) != self.dimensions:
            raise EmbeddingError(
                "Embedding dimension mismatch: "
                f"expected {self.dimensions}, "
                f"received {len(vector)}."
            )

        return vector

    async def _embed(
        self,
        prepared_text: str,
    ) -> list[float]:
        """
        Send one prepared string to Gemini.

        Retries transient failures using simple exponential backoff:

            attempt 1 -> immediate
            attempt 2 -> wait 1 second
            attempt 3 -> wait 2 seconds
        """

        last_error: Exception | None = None

        for attempt in range(
            1,
            self.max_attempts + 1,
        ):
            try:
                response = (
                    await self.client.aio.models.embed_content(
                        model=self.model,
                        contents=prepared_text,
                        config=types.EmbedContentConfig(
                            output_dimensionality=self.dimensions,
                        ),
                    )
                )

                if not response.embeddings:
                    raise EmbeddingError(
                        "Embedding provider returned no embeddings."
                    )

                if len(response.embeddings) != 1:
                    raise EmbeddingError(
                        "Expected exactly one embedding, "
                        f"received {len(response.embeddings)}."
                    )

                return self._validate_embedding(
                    response.embeddings[0].values
                )

            except EmbeddingError:
                raise

            except Exception as exc:
                last_error = exc

                logger.warning(
                    "Embedding request failed "
                    "(attempt %s/%s): %s",
                    attempt,
                    self.max_attempts,
                    exc,
                )

                if attempt == self.max_attempts:
                    break

                delay_seconds = 2 ** (attempt - 1)

                await asyncio.sleep(
                    delay_seconds
                )

        raise EmbeddingError(
            "Embedding request failed after "
            f"{self.max_attempts} attempts."
        ) from last_error

    async def embed_document(
        self,
        content: str,
        *,
        title: str | None = None,
    ) -> list[float]:
        """
        Embed one document chunk.
        """

        prepared_text = self.prepare_document(
            content=content,
            title=title,
        )

        return await self._embed(
            prepared_text
        )

    async def embed_query(
        self,
        query: str,
    ) -> list[float]:
        """
        Embed one search query.
        """

        prepared_text = self.prepare_query(
            query
        )

        return await self._embed(
            prepared_text
        )

    async def close(self) -> None:
        """
        Close Gemini's asynchronous HTTP client.
        """

        await self.client.aio.aclose()