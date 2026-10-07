import asyncio
import logging
import time

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from vendor_risk_analyzer.db.models import (
    Document,
    DocumentChunk,
    DocumentElement,
)
from vendor_risk_analyzer.ingestion.chunker import (
    create_chunks,
)
from vendor_risk_analyzer.ingestion.parsers.registry import (
    get_parser,
)
from vendor_risk_analyzer.storage.client import (
    download_object,
)


# Route ingestion logs through Uvicorn so they are
# visible in Northflank container logs.
logger = logging.getLogger(
    "uvicorn.error"
)


async def ingest_document(
    *,
    document: Document,
    db: AsyncSession,
) -> Document:
    ingestion_started = (
        time.perf_counter()
    )

    logger.info(
        "Document %s ingestion started. "
        "File=%s type=%s",
        document.id,
        document.filename,
        document.file_type,
    )

    document.status = "processing"

    await db.commit()

    logger.info(
        "Document %s status changed "
        "to processing.",
        document.id,
    )

    try:
        # ----------------------------------------------------
        # Select parser
        # ----------------------------------------------------

        parser = get_parser(
            document.file_type
        )

        logger.info(
            "Document %s parser selected. "
            "Parser=%s version=%s",
            document.id,
            parser.__class__.__name__,
            parser.parser_version,
        )

        # ----------------------------------------------------
        # Download original document
        # ----------------------------------------------------

        download_started = (
            time.perf_counter()
        )

        logger.info(
            "Document %s download started "
            "from object storage.",
            document.id,
        )

        raw_content = await asyncio.to_thread(
            download_object,
            document.object_key,
        )

        download_duration = (
            time.perf_counter()
            - download_started
        )

        logger.info(
            "Document %s download completed. "
            "Bytes=%s duration=%.3fs",
            document.id,
            len(raw_content),
            download_duration,
        )

        # ----------------------------------------------------
        # Parse document
        # ----------------------------------------------------

        parsing_started = (
            time.perf_counter()
        )

        logger.info(
            "Document %s parsing started.",
            document.id,
        )

        parsed_elements = parser.parse(
            raw_content
        )

        parsing_duration = (
            time.perf_counter()
            - parsing_started
        )

        logger.info(
            "Document %s parsing completed. "
            "Elements=%s duration=%.3fs",
            document.id,
            len(parsed_elements),
            parsing_duration,
        )

        if not parsed_elements:
            raise ValueError(
                "Document contained no parseable content"
            )

        # ----------------------------------------------------
        # Remove previous ingestion results
        #
        # Makes re-processing idempotent.
        # ----------------------------------------------------

        logger.info(
            "Document %s clearing previous "
            "chunks and elements.",
            document.id,
        )

        await db.execute(
            delete(DocumentChunk).where(
                DocumentChunk.document_id
                == document.id
            )
        )

        await db.execute(
            delete(DocumentElement).where(
                DocumentElement.document_id
                == document.id
            )
        )

        logger.info(
            "Document %s previous ingestion "
            "data cleared.",
            document.id,
        )

        # ----------------------------------------------------
        # Persist parsed elements
        # ----------------------------------------------------

        stored_elements: list[
            DocumentElement
        ] = []

        for sequence, element in enumerate(
            parsed_elements
        ):
            db_element = DocumentElement(
                document_id=document.id,
                sequence=sequence,
                element_type=(
                    element.element_type
                ),
                content=element.content,
                page_number=(
                    element.page_number
                ),
                sheet_name=(
                    element.sheet_name
                ),
                section_title=(
                    element.section_title
                ),
                heading_path=(
                    element.heading_path
                ),
                bounding_box=element.metadata.get(
                    "bbox"
                ),
                extra_data=(
                    element.metadata
                ),
            )

            db.add(db_element)

            stored_elements.append(
                db_element
            )

        # Generate UUIDs before creating chunks.
        await db.flush()

        logger.info(
            "Document %s parsed elements "
            "prepared for persistence. "
            "Elements=%s",
            document.id,
            len(stored_elements),
        )

        # ----------------------------------------------------
        # Create retrieval chunks
        # ----------------------------------------------------

        chunking_started = (
            time.perf_counter()
        )

        logger.info(
            "Document %s chunking started.",
            document.id,
        )

        parsed_chunks = create_chunks(
            parsed_elements
        )

        chunking_duration = (
            time.perf_counter()
            - chunking_started
        )

        logger.info(
            "Document %s chunking completed. "
            "Chunks=%s duration=%.3fs",
            document.id,
            len(parsed_chunks),
            chunking_duration,
        )

        if not parsed_chunks:
            raise ValueError(
                "Document produced no retrieval chunks"
            )

        # ----------------------------------------------------
        # Persist retrieval chunks
        # ----------------------------------------------------

        for sequence, chunk in enumerate(
            parsed_chunks
        ):
            source_element_ids = [
                str(
                    stored_elements[index].id
                )
                for index
                in chunk.source_sequences
                if index
                < len(stored_elements)
            ]

            source_element_id = (
                stored_elements[
                    chunk.source_sequences[0]
                ].id
                if chunk.source_sequences
                else None
            )

            db_chunk = DocumentChunk(
                document_id=document.id,
                source_element_id=(
                    source_element_id
                ),
                sequence=sequence,
                content=chunk.content,
                token_count=None,
                embedding=None,
                extra_data={
                    **chunk.metadata,
                    "source_element_ids":
                        source_element_ids,
                },
            )

            db.add(db_chunk)

        logger.info(
            "Document %s retrieval chunks "
            "prepared for persistence. "
            "Chunks=%s",
            document.id,
            len(parsed_chunks),
        )

        # ----------------------------------------------------
        # Parsing and chunking succeeded.
        #
        # Embeddings are intentionally generated
        # separately by the embedding worker.
        # ----------------------------------------------------

        document.status = (
            "embedding_pending"
        )

        document.parser_version = (
            parser.parser_version
        )

        document.extra_data = {
            **document.extra_data,
            "element_count": len(
                parsed_elements
            ),
            "chunk_count": len(
                parsed_chunks
            ),
        }

        await db.commit()

        await db.refresh(
            document
        )

        total_duration = (
            time.perf_counter()
            - ingestion_started
        )

        logger.info(
            "Document %s ingestion completed. "
            "Elements=%s chunks=%s "
            "status=embedding_pending "
            "duration=%.3fs",
            document.id,
            len(parsed_elements),
            len(parsed_chunks),
            total_duration,
        )

        return document

    except Exception as exc:
        total_duration = (
            time.perf_counter()
            - ingestion_started
        )

        logger.exception(
            "Document %s ingestion failed "
            "after %.3fs. Error=%s",
            document.id,
            total_duration,
            str(exc),
        )

        await db.rollback()

        document.status = "failed"

        document.extra_data = {
            **document.extra_data,
            "ingestion_error": str(exc)[:500],
        }

        await db.commit()

        logger.error(
            "Document %s status changed "
            "to failed.",
            document.id,
        )

        raise