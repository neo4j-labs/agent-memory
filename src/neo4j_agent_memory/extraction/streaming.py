"""Streaming extraction for long documents.

This module provides streaming extraction capabilities for processing very long
documents (>100K tokens) efficiently with:
- Chunking by token or character count
- Async generators for streaming results
- Memory-efficient processing
- Overlap handling for entity continuity across chunks
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from neo4j_agent_memory.extraction.base import (
    ExtractedEntity,
    ExtractedRelation,
    ExtractionResult,
)

if TYPE_CHECKING:
    from neo4j_agent_memory.extraction.base import EntityExtractor

logger = logging.getLogger(__name__)


# Default chunk sizes
DEFAULT_CHUNK_SIZE = 4000  # characters
DEFAULT_OVERLAP = 200  # characters for overlap
DEFAULT_TOKEN_CHUNK_SIZE = 1000  # tokens (approx 4 chars per token)
DEFAULT_TOKEN_OVERLAP = 50  # tokens

# Simple word-based tokenizer pattern (for approximate token counting)
TOKEN_PATTERN = re.compile(r"\S+")


@dataclass
class ChunkInfo:
    """Information about a document chunk."""

    index: int
    start_char: int
    end_char: int
    text: str
    is_first: bool = False
    is_last: bool = False

    @property
    def char_count(self) -> int:
        """Return character count of chunk."""
        return len(self.text)

    @property
    def approx_token_count(self) -> int:
        """Return approximate token count (word-based)."""
        return len(TOKEN_PATTERN.findall(self.text))


@dataclass
class StreamingChunkResult:
    """Result from extracting a single chunk in streaming mode."""

    chunk: ChunkInfo
    result: ExtractionResult
    success: bool = True
    error: str | None = None
    duration_ms: float = 0.0

    @property
    def entity_count(self) -> int:
        """Number of entities in this chunk."""
        return self.result.entity_count

    @property
    def relation_count(self) -> int:
        """Number of relations in this chunk."""
        return self.result.relation_count


@dataclass
class StreamingExtractionStats:
    """Statistics from a complete streaming extraction."""

    total_chunks: int = 0
    successful_chunks: int = 0
    failed_chunks: int = 0
    total_entities: int = 0
    total_relations: int = 0
    deduplicated_entities: int = 0
    total_duration_ms: float = 0.0
    total_characters: int = 0
    total_tokens_approx: int = 0


@dataclass
class StreamingExtractionResult:
    """Complete result from streaming extraction.

    Contains deduplicated entities and relations from all chunks,
    plus detailed statistics.
    """

    entities: list[ExtractedEntity] = field(default_factory=list)
    relations: list[ExtractedRelation] = field(default_factory=list)
    chunk_results: list[StreamingChunkResult] = field(default_factory=list)
    stats: StreamingExtractionStats = field(default_factory=StreamingExtractionStats)

    def to_extraction_result(self, source_text: str | None = None) -> ExtractionResult:
        """Convert to standard ExtractionResult."""
        return ExtractionResult(
            entities=self.entities,
            relations=self.relations,
            preferences=[],  # Preferences collected separately if needed
            source_text=source_text,
        )


def chunk_text_by_chars(
    text: str,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    overlap: int = DEFAULT_OVERLAP,
    split_on_sentences: bool = True,
) -> list[ChunkInfo]:
    """Split text into chunks by character count.

    Args:
        text: Text to chunk
        chunk_size: Target chunk size in characters
        overlap: Number of overlapping characters between chunks
        split_on_sentences: Try to split on sentence boundaries

    Returns:
        List of ChunkInfo objects
    """
    if not text:
        return []

    if len(text) <= chunk_size:
        return [
            ChunkInfo(
                index=0,
                start_char=0,
                end_char=len(text),
                text=text,
                is_first=True,
                is_last=True,
            )
        ]

    chunks: list[ChunkInfo] = []
    start = 0
    chunk_index = 0

    while start < len(text):
        # Calculate end position
        end = min(start + chunk_size, len(text))

        # If not at end and split_on_sentences, try to find sentence boundary
        if end < len(text) and split_on_sentences:
            # Look for sentence-ending punctuation followed by space
            search_region = text[max(end - 100, start) : end]
            sentence_ends = [m.end() for m in re.finditer(r"[.!?]\s+", search_region)]
            if sentence_ends:
                # Use the last sentence boundary
                boundary_offset = sentence_ends[-1]
                end = max(end - 100, start) + boundary_offset

        chunk_text = text[start:end]

        chunks.append(
            ChunkInfo(
                index=chunk_index,
                start_char=start,
                end_char=end,
                text=chunk_text,
                is_first=(chunk_index == 0),
                is_last=(end >= len(text)),
            )
        )

        # Move start position, accounting for overlap
        start = end - overlap if end < len(text) else end
        chunk_index += 1

    # Mark last chunk
    if chunks:
        chunks[-1].is_last = True

    return chunks


def chunk_text_by_tokens(
    text: str,
    chunk_size: int = DEFAULT_TOKEN_CHUNK_SIZE,
    overlap: int = DEFAULT_TOKEN_OVERLAP,
) -> list[ChunkInfo]:
    """Split text into chunks by approximate token count.

    Uses a simple word-based approximation for token counting.
    For exact token counting, use a proper tokenizer.

    Args:
        text: Text to chunk
        chunk_size: Target chunk size in tokens
        overlap: Number of overlapping tokens between chunks

    Returns:
        List of ChunkInfo objects
    """
    if not text:
        return []

    # Find all token positions
    tokens = list(TOKEN_PATTERN.finditer(text))

    if len(tokens) <= chunk_size:
        return [
            ChunkInfo(
                index=0,
                start_char=0,
                end_char=len(text),
                text=text,
                is_first=True,
                is_last=True,
            )
        ]

    chunks: list[ChunkInfo] = []
    token_idx = 0
    chunk_index = 0

    while token_idx < len(tokens):
        # Get start position from first token in chunk
        start_char = tokens[token_idx].start()

        # Calculate end token index
        end_token_idx = min(token_idx + chunk_size, len(tokens))

        # Get end position from last token in chunk
        end_char = tokens[end_token_idx - 1].end()

        # Extend to end of sentence if close
        if end_token_idx < len(tokens):
            next_100_chars = text[end_char : end_char + 100]
            sentence_match = re.search(r"[.!?]\s", next_100_chars)
            if sentence_match:
                end_char = end_char + sentence_match.end()

        chunk_text = text[start_char:end_char]

        chunks.append(
            ChunkInfo(
                index=chunk_index,
                start_char=start_char,
                end_char=end_char,
                text=chunk_text,
                is_first=(chunk_index == 0),
                is_last=(end_token_idx >= len(tokens)),
            )
        )

        # Move to next chunk with overlap
        token_idx = end_token_idx - overlap if end_token_idx < len(tokens) else end_token_idx
        chunk_index += 1

    # Mark last chunk
    if chunks:
        chunks[-1].is_last = True

    return chunks


def _entity_key(entity: ExtractedEntity) -> str:
    """Generate key for entity deduplication."""
    return f"{entity.normalized_name}::{entity.type}"


def _relation_key(relation: ExtractedRelation) -> tuple[str, str, str]:
    """Generate key for relation deduplication."""
    return (relation.source.lower(), relation.relation_type, relation.target.lower())


def deduplicate_entities(entities: list[ExtractedEntity]) -> list[ExtractedEntity]:
    """Deduplicate entities, keeping highest confidence for each.

    Args:
        entities: List of entities to deduplicate

    Returns:
        Deduplicated list of entities
    """
    best_entities: dict[str, ExtractedEntity] = {}

    for entity in entities:
        key = _entity_key(entity)
        if key not in best_entities or entity.confidence > best_entities[key].confidence:
            best_entities[key] = entity

    return list(best_entities.values())


def deduplicate_relations(relations: list[ExtractedRelation]) -> list[ExtractedRelation]:
    """Deduplicate relations, keeping highest confidence for each.

    Args:
        relations: List of relations to deduplicate

    Returns:
        Deduplicated list of relations
    """
    best_relations: dict[tuple[str, str, str], ExtractedRelation] = {}

    for relation in relations:
        key = _relation_key(relation)
        if key not in best_relations or relation.confidence > best_relations[key].confidence:
            best_relations[key] = relation

    return list(best_relations.values())


class StreamingExtractor:
    """Streaming extractor for processing long documents.

    Chunks documents and extracts entities from each chunk, yielding results
    as they become available. Handles entity deduplication across chunks.

    Example:
        ```python
        from neo4j_agent_memory.extraction import GLiNER2Extractor
        from neo4j_agent_memory.extraction.streaming import StreamingExtractor

        extractor = GLiNER2Extractor.for_schema("podcast")
        streamer = StreamingExtractor(extractor, chunk_size=4000)

        # Stream results
        async for chunk_result in streamer.extract_streaming(long_text):
            print(f"Chunk {chunk_result.chunk.index}: {chunk_result.entity_count} entities")

        # Or get complete result with deduplication
        result = await streamer.extract(long_text)
        print(f"Total: {len(result.entities)} unique entities")
        ```
    """

    def __init__(
        self,
        extractor: EntityExtractor,
        *,
        chunk_size: int = DEFAULT_CHUNK_SIZE,
        overlap: int = DEFAULT_OVERLAP,
        chunk_by_tokens: bool = False,
        split_on_sentences: bool = True,
    ):
        """Initialize streaming extractor.

        Args:
            extractor: Base extractor to use for each chunk
            chunk_size: Size of each chunk (chars or tokens)
            overlap: Overlap between chunks (chars or tokens)
            chunk_by_tokens: If True, chunk by tokens instead of characters
            split_on_sentences: Try to split on sentence boundaries
        """
        self._extractor = extractor
        self.chunk_size = chunk_size
        self.overlap = overlap
        self.chunk_by_tokens = chunk_by_tokens
        self.split_on_sentences = split_on_sentences

    def chunk_document(self, text: str) -> list[ChunkInfo]:
        """Chunk a document according to settings.

        Args:
            text: Text to chunk

        Returns:
            List of ChunkInfo objects
        """
        if self.chunk_by_tokens:
            return chunk_text_by_tokens(text, self.chunk_size, self.overlap)
        else:
            return chunk_text_by_chars(text, self.chunk_size, self.overlap, self.split_on_sentences)

    async def extract_streaming(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        on_chunk_complete: Callable[[StreamingChunkResult], None] | None = None,
    ) -> AsyncIterator[StreamingChunkResult]:
        """Stream extraction results chunk by chunk.

        Yields results as each chunk is processed, allowing for real-time
        progress updates and memory-efficient processing.

        Args:
            text: Text to extract from
            entity_types: Optional list of entity types to extract
            extract_relations: Whether to extract relations
            on_chunk_complete: Optional callback for each completed chunk

        Yields:
            StreamingChunkResult for each chunk
        """
        import time

        chunks = self.chunk_document(text)
        logger.info(f"Streaming extraction: {len(chunks)} chunks from {len(text)} characters")

        for chunk in chunks:
            chunk_start = time.time()

            try:
                result = await self._extractor.extract(
                    chunk.text,
                    entity_types=entity_types,
                    extract_relations=extract_relations,
                    extract_preferences=False,  # Preferences typically need full context
                )

                # Adjust entity positions to document-level
                for entity in result.entities:
                    if entity.start_pos is not None:
                        entity.start_pos += chunk.start_char
                    if entity.end_pos is not None:
                        entity.end_pos += chunk.start_char

                duration = (time.time() - chunk_start) * 1000

                chunk_result = StreamingChunkResult(
                    chunk=chunk,
                    result=result,
                    success=True,
                    duration_ms=duration,
                )

            except Exception as e:
                duration = (time.time() - chunk_start) * 1000
                logger.warning(f"Chunk {chunk.index} extraction failed: {e}")

                chunk_result = StreamingChunkResult(
                    chunk=chunk,
                    result=ExtractionResult(source_text=chunk.text),
                    success=False,
                    error=str(e),
                    duration_ms=duration,
                )

            if on_chunk_complete:
                try:
                    on_chunk_complete(chunk_result)
                except Exception as e:
                    logger.warning(f"Chunk complete callback error: {e}")

            yield chunk_result

    async def extract(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        deduplicate: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> StreamingExtractionResult:
        """Extract from a long document with automatic chunking.

        Processes the entire document and returns a complete result with
        deduplicated entities and relations.

        Args:
            text: Text to extract from
            entity_types: Optional list of entity types to extract
            extract_relations: Whether to extract relations
            deduplicate: Whether to deduplicate entities across chunks
            on_progress: Optional progress callback (completed_chunks, total_chunks)

        Returns:
            StreamingExtractionResult with all entities, relations, and stats
        """
        import time

        start_time = time.time()
        chunks = self.chunk_document(text)
        total_chunks = len(chunks)

        all_entities: list[ExtractedEntity] = []
        all_relations: list[ExtractedRelation] = []
        chunk_results: list[StreamingChunkResult] = []

        stats = StreamingExtractionStats(
            total_chunks=total_chunks,
            total_characters=len(text),
            total_tokens_approx=len(TOKEN_PATTERN.findall(text)),
        )

        completed = 0

        async for chunk_result in self.extract_streaming(
            text,
            entity_types=entity_types,
            extract_relations=extract_relations,
        ):
            chunk_results.append(chunk_result)
            completed += 1

            if chunk_result.success:
                stats.successful_chunks += 1
                all_entities.extend(chunk_result.result.entities)
                all_relations.extend(chunk_result.result.relations)
            else:
                stats.failed_chunks += 1

            if on_progress:
                try:
                    on_progress(completed, total_chunks)
                except Exception as e:
                    logger.warning(f"Progress callback error: {e}")

        # Record raw counts
        stats.total_entities = len(all_entities)
        stats.total_relations = len(all_relations)

        # Deduplicate if requested
        if deduplicate:
            all_entities = deduplicate_entities(all_entities)
            all_relations = deduplicate_relations(all_relations)

        stats.deduplicated_entities = len(all_entities)
        stats.total_duration_ms = (time.time() - start_time) * 1000

        logger.info(
            f"Streaming extraction complete: "
            f"{stats.successful_chunks}/{stats.total_chunks} chunks, "
            f"{stats.deduplicated_entities} entities (from {stats.total_entities} raw), "
            f"{stats.total_duration_ms:.1f}ms"
        )

        return StreamingExtractionResult(
            entities=all_entities,
            relations=all_relations,
            chunk_results=chunk_results,
            stats=stats,
        )

    async def extract_to_result(
        self,
        text: str,
        *,
        entity_types: list[str] | None = None,
        extract_relations: bool = True,
        deduplicate: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ExtractionResult:
        """Extract and return standard ExtractionResult.

        Convenience method that returns a standard ExtractionResult
        instead of StreamingExtractionResult.

        Args:
            text: Text to extract from
            entity_types: Optional list of entity types to extract
            extract_relations: Whether to extract relations
            deduplicate: Whether to deduplicate entities
            on_progress: Optional progress callback

        Returns:
            Standard ExtractionResult
        """
        result = await self.extract(
            text,
            entity_types=entity_types,
            extract_relations=extract_relations,
            deduplicate=deduplicate,
            on_progress=on_progress,
        )
        return result.to_extraction_result(source_text=text)


def create_streaming_extractor(
    extractor: EntityExtractor,
    *,
    chunk_size: int | None = None,
    overlap: int | None = None,
    chunk_by_tokens: bool = False,
) -> StreamingExtractor:
    """Create a streaming extractor with sensible defaults.

    Args:
        extractor: Base extractor to use
        chunk_size: Chunk size (default: 4000 chars or 1000 tokens)
        overlap: Overlap size (default: 200 chars or 50 tokens)
        chunk_by_tokens: Whether to chunk by tokens

    Returns:
        Configured StreamingExtractor
    """
    if chunk_size is None:
        chunk_size = DEFAULT_TOKEN_CHUNK_SIZE if chunk_by_tokens else DEFAULT_CHUNK_SIZE
    if overlap is None:
        overlap = DEFAULT_TOKEN_OVERLAP if chunk_by_tokens else DEFAULT_OVERLAP

    return StreamingExtractor(
        extractor,
        chunk_size=chunk_size,
        overlap=overlap,
        chunk_by_tokens=chunk_by_tokens,
    )
