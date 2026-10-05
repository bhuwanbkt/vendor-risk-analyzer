import asyncio

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


async def ingest_document(
    *,
    document: Document,
    db: AsyncSession,
) -> Document:

    document.status = "processing"

    await db.commit()

    try:
        parser = get_parser(
            document.file_type
        )

        raw_content = await asyncio.to_thread(
            download_object,
            document.object_key,
        )

        parsed_elements = parser.parse(
            raw_content
        )

        if not parsed_elements:
            raise ValueError(
                "Document contained no parseable content"
            )

        # Makes re-processing idempotent.
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
                bounding_box=None,
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

        parsed_chunks = create_chunks(
            parsed_elements
        )

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

        document.status = "parsed"

        document.parser_version = "1.2"

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
        await db.refresh(document)

        return document

    except Exception as exc:
        await db.rollback()

        document.status = "failed"

        document.extra_data = {
            **document.extra_data,
            "ingestion_error": str(exc)[:500],
        }

        await db.commit()

        raise