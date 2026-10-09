from __future__ import annotations

import asyncio
import math

from vendor_risk_analyzer.embeddings.service import (
    EmbeddingService,
)


async def main() -> None:
    service = EmbeddingService()

    try:
        text = (
            "Customer data is encrypted at rest using AES-256 "
            "and data in transit is protected using TLS 1.3."
        )

        print("Testing document embedding...")
        print()

        print("Input:")
        print(text)
        print()

        prepared = service.prepare_document(
            content=text,
            title="Data Protection",
        )

        print("Prepared Gemini input:")
        print(prepared)
        print()

        embedding = await service.embed_document(
            content=text,
            title="Data Protection",
        )

        magnitude = math.sqrt(
            sum(
                value * value
                for value in embedding
            )
        )

        print("Embedding generated successfully.")
        print(
            f"Model: {service.model}"
        )
        print(
            f"Dimensions: {len(embedding)}"
        )
        print(
            f"Vector magnitude: {magnitude:.6f}"
        )
        print(
            f"First 5 values: {embedding[:5]}"
        )

        assert len(embedding) == 768

        print()
        print("PASS: received a valid 768-dimensional embedding.")

    finally:
        await service.close()


if __name__ == "__main__":
    asyncio.run(main())