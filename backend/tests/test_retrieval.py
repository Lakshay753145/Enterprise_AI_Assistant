"""Tests for retrieval fusion and the chunk-quality filters.

Pure functions only - no database, no models to download.
"""

import pytest

from backend.ingestion.chunker import _classify, _is_useful
from backend.retrieval.hybrid_search import (
    RetrievedChunk,
    _looks_like_identifier,
    _snippet,
    reciprocal_rank_fusion,
)


def chunk(chunk_id: int, department: str = "Production") -> RetrievedChunk:
    return RetrievedChunk(
        chunk_id=chunk_id,
        document_id=1,
        department=department,
        content=f"content {chunk_id}",
        heading=None,
        section_path=None,
        page_number=None,
        document_name="doc.pdf",
        document_title="Doc",
    )


class TestReciprocalRankFusion:
    def test_agreement_beats_a_single_strong_hit(self):
        """The whole point of hybrid search: two retrievers agreeing is
        stronger evidence than one retriever being very confident."""
        dense = [chunk(1), chunk(2), chunk(3)]
        lexical = [chunk(4), chunk(2), chunk(5)]

        fused = reciprocal_rank_fusion([dense, lexical])

        # Chunk 2 is rank 2 in both; chunks 1 and 4 are rank 1 in one each.
        assert fused[0].chunk_id == 2

    def test_deduplicates(self):
        fused = reciprocal_rank_fusion([[chunk(1), chunk(2)], [chunk(1)]])
        assert len(fused) == 2
        assert {c.chunk_id for c in fused} == {1, 2}

    def test_sorted_descending(self):
        fused = reciprocal_rank_fusion([[chunk(i) for i in range(1, 6)]])
        scores = [c.fusion_score for c in fused]
        assert scores == sorted(scores, reverse=True)

    def test_records_ranks_from_each_retriever(self):
        fused = reciprocal_rank_fusion([[chunk(1)], [chunk(1)]])
        assert fused[0].vector_rank == 1
        assert fused[0].keyword_rank == 1

    def test_handles_empty_lists(self):
        assert reciprocal_rank_fusion([[], []]) == []
        assert len(reciprocal_rank_fusion([[chunk(1)], []])) == 1

    def test_weighting_shifts_the_order(self):
        dense = [chunk(1)]
        lexical = [chunk(2)]

        dense_favoured = reciprocal_rank_fusion([dense, lexical], weights=[2.0, 1.0])
        assert dense_favoured[0].chunk_id == 1

        lexical_favoured = reciprocal_rank_fusion(
            [[chunk(1)], [chunk(2)]], weights=[1.0, 2.0]
        )
        assert lexical_favoured[0].chunk_id == 2


class TestIdentifierDetection:
    @pytest.mark.parametrize(
        "query",
        ["AS9100", "IN-718", "ISO 9001", "PO 4500123", "form QA-12"],
    )
    def test_recognises_identifiers(self, query):
        assert _looks_like_identifier(query)

    @pytest.mark.parametrize(
        "query",
        ["how do I apply for leave", "what is the safety policy"],
    )
    def test_ignores_plain_prose(self, query):
        assert not _looks_like_identifier(query)


class TestSnippet:
    def test_short_text_is_untouched(self):
        assert _snippet("Short text.") == "Short text."

    def test_collapses_whitespace(self):
        assert _snippet("a\n\n  b\tc") == "a b c"

    def test_truncates_on_a_word_boundary(self):
        result = _snippet("word " * 200, length=50)
        assert len(result) <= 54
        assert result.endswith("...")


class TestChunkQualityFilter:
    @pytest.mark.parametrize(
        "text",
        [
            "Page 12",
            "page 3 of 47",
            "-----------",
            "42",
            "CONFIDENTIAL",
            "short",
        ],
    )
    def test_rejects_page_furniture(self, text):
        assert not _is_useful(text)

    def test_accepts_real_content(self):
        text = (
            "All incoming material shall be inspected against the applicable "
            "specification before being released to production stores."
        )
        assert _is_useful(text)


class TestContentClassification:
    def test_detects_a_markdown_table(self):
        table = "| Grade | Temp |\n|---|---|\n| IN718 | 980 |\n| IN625 | 1120 |"
        assert _classify(table) == "table"

    def test_detects_a_list(self):
        assert _classify("- first item\n- second item\n- third item") == "list"

    def test_defaults_to_text(self):
        assert _classify("A normal paragraph of prose about a process.") == "text"
