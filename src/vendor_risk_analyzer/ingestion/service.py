import asyncio
import hashlib
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
from vendor_risk_analyzer.schemas.document import (
    MAX_FILE_SIZE,
)
from vendor_risk_analyzer.storage.client import (
    download_object,
    get_object_metadata,
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

    # Captured up front: after a rollback every loaded ORM
    # attribute is expired, including the primary key.
    document_id = document.id

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

        # Check the stored object's size before downloading it.
        # The browser uploads directly to storage, so the stored
        # object is not trusted until it matches the record.
        metadata = await asyncio.to_thread(
            get_object_metadata,
            document.object_key,
        )

        if metadata is None:
            raise ValueError(
                "Uploaded file was not found in storage."
            )

        stored_size = metadata.get("ContentLength")

        if (
            not isinstance(stored_size, int)
            or stored_size > MAX_FILE_SIZE
            or (
                document.size_bytes is not None
                and stored_size != document.size_bytes
            )
        ):
            raise ValueError(
                "Stored file size does not match "
                "the uploaded document."
            )

        raw_content = await asyncio.to_thread(
            download_object,
            document.object_key,
        )

        # Size can change between the check and the download,
        # and equal sizes do not prove equal content.
        if (
            document.size_bytes is not None
            and len(raw_content) != document.size_bytes
        ) or (
            hashlib.sha256(raw_content).hexdigest()
            != document.sha256
        ):
            raise ValueError(
                "Stored file does not match the "
                "uploaded document's SHA-256 hash."
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

        # CPU-bound parsing runs in a worker thread so a large
        # document does not block the event loop (other requests
        # and the embedding worker share this loop).
        parsed_elements = await asyncio.to_thread(
            parser.parse,
            raw_content,
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

        parsed_chunks = await asyncio.to_thread(
            create_chunks,
            parsed_elements,
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

        try:
            await db.rollback()

            # Rollback expires all loaded ORM objects, even with
            # expire_on_commit=False. Reading an expired attribute
            # through AsyncSession triggers an implicit load and
            # raises MissingGreenlet, which previously left the
            # document stuck in "processing". Reload explicitly.
            await db.refresh(document)

            document.status = "failed"

            document.extra_data = {
                **(document.extra_data or {}),
                "ingestion_error": str(exc)[:500],
            }

            await db.commit()

        except Exception:
            # Never mask the original ingestion error. A document
            # left in "processing" can be retried once it is stale.
            logger.exception(
                "Document %s could not be marked failed.",
                document_id,
            )

        else:
            logger.error(
                "Document %s status changed "
                "to failed.",
                document_id,
            )

        raise