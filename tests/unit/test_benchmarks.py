"""Unit tests for benchmarks module."""

from __future__ import annotations

import json

# Use direct imports since benchmarks is not in the package
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

# Add benchmarks to path for testing
benchmarks_path = Path(__file__).parent.parent.parent / "benchmarks"
sys.path.insert(0, str(benchmarks_path.parent))

from benchmarks.metrics import (
    BCubedScore,
    BenchmarkResult,
    EntityMetrics,
    ExpectedEntity,
    ExpectedRelation,
    ExtractionMetrics,
    MatchPolicy,
    RelationMetrics,
    aggregate_relation_metrics,
    bcubed,
    calculate_entity_metrics,
    calculate_extraction_metrics,
    calculate_relation_metrics,
    load_resolution_gold,
    normalize_name,
)
from benchmarks.runner import (
    BenchmarkConfig,
    BenchmarkRunner,
    BenchmarkSuite,
    BenchmarkTestCase,
    create_sample_benchmark_suite,
)

DATA_DIR = Path(__file__).parent.parent.parent / "benchmarks" / "data"
POLEO_GOLD = DATA_DIR / "poleo_gold.json"
POLEO_RESOLUTION_GOLD = DATA_DIR / "poleo_resolution_gold.json"

# The closed POLE+O relation vocabulary (schema/models.py _get_poleo_relation_types),
# minus the catch-alls RELATED_TO and MENTIONS, which gold data must not use.
POLEO_RELATION_TYPES = {
    "KNOWS",
    "ALIAS_OF",
    "MEMBER_OF",
    "EMPLOYED_BY",
    "OWNS",
    "USES",
    "LOCATED_AT",
    "RESIDES_AT",
    "HEADQUARTERS_AT",
    "PARTICIPATED_IN",
    "OCCURRED_AT",
    "INVOLVED",
    "SUBSIDIARY_OF",
    "PARTNER_WITH",
}

# Source/target entity types each relation permits.
POLEO_RELATION_CONSTRAINTS = {
    "KNOWS": ({"PERSON"}, {"PERSON"}),
    "ALIAS_OF": ({"PERSON"}, {"PERSON"}),
    "MEMBER_OF": ({"PERSON"}, {"ORGANIZATION"}),
    "EMPLOYED_BY": ({"PERSON"}, {"ORGANIZATION"}),
    "OWNS": ({"PERSON", "ORGANIZATION"}, {"OBJECT"}),
    "USES": ({"PERSON"}, {"OBJECT"}),
    "LOCATED_AT": ({"PERSON", "OBJECT", "ORGANIZATION", "EVENT"}, {"LOCATION"}),
    "RESIDES_AT": ({"PERSON"}, {"LOCATION"}),
    "HEADQUARTERS_AT": ({"ORGANIZATION"}, {"LOCATION"}),
    "PARTICIPATED_IN": ({"PERSON", "ORGANIZATION"}, {"EVENT"}),
    "OCCURRED_AT": ({"EVENT"}, {"LOCATION"}),
    "INVOLVED": ({"EVENT"}, {"OBJECT"}),
    "SUBSIDIARY_OF": ({"ORGANIZATION"}, {"ORGANIZATION"}),
    "PARTNER_WITH": ({"ORGANIZATION"}, {"ORGANIZATION"}),
}


class TestEntityMetrics:
    """Tests for EntityMetrics."""

    def test_precision_calculation(self):
        """Test precision calculation."""
        metrics = EntityMetrics(entity_type="PERSON", true_positives=8, false_positives=2)
        assert metrics.precision == 0.8

    def test_recall_calculation(self):
        """Test recall calculation."""
        metrics = EntityMetrics(entity_type="PERSON", true_positives=8, false_negatives=2)
        assert metrics.recall == 0.8

    def test_f1_calculation(self):
        """Test F1 score calculation."""
        metrics = EntityMetrics(
            entity_type="PERSON", true_positives=8, false_positives=2, false_negatives=2
        )
        # precision = 8/10 = 0.8, recall = 8/10 = 0.8
        # f1 = 2 * 0.8 * 0.8 / (0.8 + 0.8) = 0.8
        assert abs(metrics.f1_score - 0.8) < 0.001

    def test_precision_zero_when_no_predictions(self):
        """Test precision is 0 when no predictions."""
        metrics = EntityMetrics(entity_type="PERSON")
        assert metrics.precision == 0.0

    def test_recall_zero_when_no_expected(self):
        """Test recall is 0 when no expected entities."""
        metrics = EntityMetrics(entity_type="PERSON")
        assert metrics.recall == 0.0

    def test_f1_zero_when_precision_and_recall_zero(self):
        """Test F1 is 0 when precision and recall are both 0."""
        metrics = EntityMetrics(entity_type="PERSON")
        assert metrics.f1_score == 0.0

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metrics = EntityMetrics(
            entity_type="PERSON", true_positives=5, false_positives=1, false_negatives=2
        )
        d = metrics.to_dict()
        assert d["entity_type"] == "PERSON"
        assert d["true_positives"] == 5
        assert d["false_positives"] == 1
        assert d["false_negatives"] == 2
        assert "precision" in d
        assert "recall" in d
        assert "f1_score" in d


class TestExtractionMetrics:
    """Tests for ExtractionMetrics."""

    def test_micro_averages(self):
        """Test micro-averaged metrics."""
        metrics = ExtractionMetrics(
            total_true_positives=10,
            total_false_positives=2,
            total_false_negatives=3,
        )
        assert metrics.micro_precision == 10 / 12
        assert metrics.micro_recall == 10 / 13

    def test_macro_averages(self):
        """Test macro-averaged metrics."""
        metrics = ExtractionMetrics()
        metrics.entity_metrics["PERSON"] = EntityMetrics(
            entity_type="PERSON", true_positives=5, false_positives=1, false_negatives=1
        )
        metrics.entity_metrics["LOCATION"] = EntityMetrics(
            entity_type="LOCATION", true_positives=3, false_positives=1, false_negatives=1
        )

        # Person precision = 5/6, Location precision = 3/4
        # Macro precision = (5/6 + 3/4) / 2
        expected_macro_precision = (5 / 6 + 3 / 4) / 2
        assert abs(metrics.macro_precision - expected_macro_precision) < 0.001

    def test_to_dict(self):
        """Test conversion to dictionary."""
        metrics = ExtractionMetrics(
            total_true_positives=10,
            latency_ms=100.5,
            token_count=500,
        )
        d = metrics.to_dict()
        assert "micro_precision" in d
        assert "micro_recall" in d
        assert "micro_f1" in d
        assert "macro_precision" in d
        assert d["latency_ms"] == 100.5
        assert d["token_count"] == 500


class TestExpectedEntity:
    """Tests for ExpectedEntity."""

    def test_exact_match(self):
        """Test exact name matching."""
        expected = ExpectedEntity(name="John Smith", entity_type="PERSON")
        assert expected.matches("John Smith", "PERSON") is True
        assert expected.matches("john smith", "PERSON") is True  # Case insensitive

    def test_alias_match(self):
        """Test alias matching."""
        expected = ExpectedEntity(
            name="New York City",
            entity_type="LOCATION",
            aliases=["NYC", "New York"],
        )
        assert expected.matches("NYC", "LOCATION") is True
        assert expected.matches("New York", "LOCATION") is True

    def test_type_mismatch(self):
        """Test type mismatch returns False."""
        expected = ExpectedEntity(name="John Smith", entity_type="PERSON")
        assert expected.matches("John Smith", "ORGANIZATION") is False

    def test_name_mismatch(self):
        """Test name mismatch returns False."""
        expected = ExpectedEntity(name="John Smith", entity_type="PERSON")
        assert expected.matches("Jane Doe", "PERSON") is False


class TestCalculateEntityMetrics:
    """Tests for calculate_entity_metrics function."""

    def test_all_correct(self):
        """Test when all extractions are correct."""
        expected = [
            ExpectedEntity(name="John", entity_type="PERSON"),
            ExpectedEntity(name="Jane", entity_type="PERSON"),
        ]
        extracted = [("John", "PERSON"), ("Jane", "PERSON")]

        metrics = calculate_entity_metrics(expected, extracted)
        assert metrics.true_positives == 2
        assert metrics.false_positives == 0
        assert metrics.false_negatives == 0
        assert metrics.f1_score == 1.0

    def test_some_missing(self):
        """Test when some expected entities are missing."""
        expected = [
            ExpectedEntity(name="John", entity_type="PERSON"),
            ExpectedEntity(name="Jane", entity_type="PERSON"),
        ]
        extracted = [("John", "PERSON")]

        metrics = calculate_entity_metrics(expected, extracted)
        assert metrics.true_positives == 1
        assert metrics.false_positives == 0
        assert metrics.false_negatives == 1

    def test_extra_extractions(self):
        """Test when there are extra extractions."""
        expected = [ExpectedEntity(name="John", entity_type="PERSON")]
        extracted = [("John", "PERSON"), ("Bob", "PERSON")]

        metrics = calculate_entity_metrics(expected, extracted)
        assert metrics.true_positives == 1
        assert metrics.false_positives == 1
        assert metrics.false_negatives == 0

    def test_empty_expected(self):
        """Test with no expected entities."""
        metrics = calculate_entity_metrics([], [("John", "PERSON")])
        assert metrics.true_positives == 0
        assert metrics.false_positives == 1

    def test_empty_extracted(self):
        """Test with no extracted entities."""
        expected = [ExpectedEntity(name="John", entity_type="PERSON")]
        metrics = calculate_entity_metrics(expected, [])
        assert metrics.true_positives == 0
        assert metrics.false_negatives == 1


class TestCalculateExtractionMetrics:
    """Tests for calculate_extraction_metrics function."""

    def test_aggregation_across_types(self):
        """Test that metrics are aggregated across entity types."""
        expected = [
            ExpectedEntity(name="John", entity_type="PERSON"),
            ExpectedEntity(name="NYC", entity_type="LOCATION"),
        ]
        extracted = [("John", "PERSON"), ("NYC", "LOCATION")]

        metrics = calculate_extraction_metrics(expected, extracted)
        assert "PERSON" in metrics.entity_metrics
        assert "LOCATION" in metrics.entity_metrics
        assert metrics.total_true_positives == 2

    def test_latency_and_tokens(self):
        """Test that latency and token count are preserved."""
        metrics = calculate_extraction_metrics([], [], latency_ms=150.0, token_count=1000)
        assert metrics.latency_ms == 150.0
        assert metrics.token_count == 1000


class TestBenchmarkTestCase:
    """Tests for BenchmarkTestCase."""

    def test_from_dict(self):
        """Test creating BenchmarkTestCase from dictionary."""
        data = {
            "id": "tc-001",
            "text": "John works at Acme.",
            "expected_entities": [
                {"name": "John", "type": "PERSON", "aliases": []},
                {"name": "Acme", "type": "ORGANIZATION"},
            ],
        }
        tc = BenchmarkTestCase.from_dict(data)
        assert tc.id == "tc-001"
        assert len(tc.expected_entities) == 2
        assert tc.expected_entities[0].name == "John"

    def test_to_dict(self):
        """Test converting BenchmarkTestCase to dictionary."""
        tc = BenchmarkTestCase(
            id="tc-001",
            text="Test text",
            expected_entities=[
                ExpectedEntity(name="John", entity_type="PERSON"),
            ],
        )
        d = tc.to_dict()
        assert d["id"] == "tc-001"
        assert d["text"] == "Test text"
        assert len(d["expected_entities"]) == 1


class TestBenchmarkConfig:
    """Tests for BenchmarkConfig."""

    def test_default_values(self):
        """Test default configuration values."""
        config = BenchmarkConfig()
        assert config.name == "default"
        assert config.warmup_runs == 1
        assert config.num_runs == 3
        assert config.timeout_seconds == 60.0

    def test_relations_extracted_by_default(self):
        """Relations are scored by default (flipped in 0.7)."""
        assert BenchmarkConfig().extract_relations is True

    def test_default_match_policy(self):
        """The default match policy is alias-tolerant and directed."""
        policy = BenchmarkConfig().match_policy
        assert policy == MatchPolicy()
        assert policy.use_aliases is True
        assert policy.directed is True

    def test_custom_values(self):
        """Test custom configuration values."""
        config = BenchmarkConfig(
            name="custom",
            warmup_runs=2,
            num_runs=5,
        )
        assert config.name == "custom"
        assert config.warmup_runs == 2
        assert config.num_runs == 5


class TestBenchmarkSuite:
    """Tests for BenchmarkSuite."""

    def test_create_suite(self):
        """Test creating a benchmark suite."""
        suite = BenchmarkSuite(
            name="test_suite",
            description="Test description",
            test_cases=[],
        )
        assert suite.name == "test_suite"
        assert suite.description == "Test description"

    def test_from_json_file(self, tmp_path):
        """Test loading suite from JSON file."""
        data = {
            "name": "json_suite",
            "description": "From JSON",
            "test_cases": [
                {
                    "id": "tc-001",
                    "text": "Test text",
                    "expected_entities": [],
                }
            ],
            "config": {
                "name": "json_config",
                "num_runs": 5,
            },
        }
        json_file = tmp_path / "suite.json"
        with open(json_file, "w") as f:
            json.dump(data, f)

        suite = BenchmarkSuite.from_json_file(json_file)
        assert suite.name == "json_suite"
        assert len(suite.test_cases) == 1
        assert suite.config.num_runs == 5

    def test_to_json_file(self, tmp_path):
        """Test saving suite to JSON file."""
        suite = BenchmarkSuite(
            name="save_test",
            description="Test saving",
            test_cases=[
                BenchmarkTestCase(
                    id="tc-001",
                    text="Test",
                    expected_entities=[],
                )
            ],
        )
        json_file = tmp_path / "output.json"
        suite.to_json_file(json_file)

        with open(json_file) as f:
            data = json.load(f)
        assert data["name"] == "save_test"


class TestBenchmarkRunner:
    """Tests for BenchmarkRunner."""

    @pytest.fixture
    def mock_extractor(self):
        """Create a mock extractor."""
        extractor = MagicMock()
        extractor.extract = AsyncMock()
        return extractor

    @pytest.mark.asyncio
    async def test_run_test_case(self, mock_extractor):
        """Test running a single test case."""
        # Setup mock result
        mock_result = MagicMock()
        mock_entity = MagicMock()
        mock_entity.name = "John"
        mock_entity.type = "PERSON"
        mock_result.entities = [mock_entity]
        mock_extractor.extract.return_value = mock_result

        runner = BenchmarkRunner(mock_extractor)
        test_case = BenchmarkTestCase(
            id="tc-001",
            text="John works at Acme.",
            expected_entities=[],
        )
        config = BenchmarkConfig(timeout_seconds=30.0)

        extracted, latency = await runner.run_test_case(test_case, config)
        assert len(extracted) == 1
        assert extracted[0] == ("John", "PERSON")
        assert latency > 0

    @pytest.mark.asyncio
    async def test_run_suite(self, mock_extractor):
        """Test running a complete benchmark suite."""
        # Setup mock result
        mock_result = MagicMock()
        mock_entity = MagicMock()
        mock_entity.name = "John"
        mock_entity.type = "PERSON"
        mock_result.entities = [mock_entity]
        mock_extractor.extract.return_value = mock_result

        runner = BenchmarkRunner(mock_extractor)
        suite = BenchmarkSuite(
            name="test_suite",
            description="Test",
            test_cases=[
                BenchmarkTestCase(
                    id="tc-001",
                    text="John works at Acme.",
                    expected_entities=[
                        ExpectedEntity(name="John", entity_type="PERSON"),
                    ],
                ),
            ],
            config=BenchmarkConfig(warmup_runs=0, num_runs=1),
        )

        result = await runner.run_suite(suite)
        assert result.name == "test_suite"
        assert result.test_count == 1
        assert result.metrics.total_true_positives == 1

    @pytest.mark.asyncio
    async def test_run_suite_with_errors(self, mock_extractor):
        """Test handling extraction errors."""
        mock_extractor.extract.side_effect = Exception("Test error")

        runner = BenchmarkRunner(mock_extractor)
        suite = BenchmarkSuite(
            name="error_suite",
            description="Test errors",
            test_cases=[
                BenchmarkTestCase(
                    id="tc-001",
                    text="Test text",
                    expected_entities=[
                        ExpectedEntity(name="John", entity_type="PERSON"),
                    ],
                ),
            ],
            config=BenchmarkConfig(warmup_runs=0, num_runs=1),
        )

        result = await runner.run_suite(suite)
        assert len(result.errors) > 0


class TestCreateSampleBenchmarkSuite:
    """Tests for create_sample_benchmark_suite function."""

    def test_creates_valid_suite(self):
        """Test that sample suite is valid."""
        suite = create_sample_benchmark_suite()
        assert suite.name == "sample_benchmark"
        assert len(suite.test_cases) > 0

    def test_test_cases_have_entities(self):
        """Test that test cases have expected entities."""
        suite = create_sample_benchmark_suite()
        for tc in suite.test_cases:
            assert len(tc.expected_entities) > 0


class TestBenchmarkResult:
    """Tests for BenchmarkResult."""

    def test_to_dict(self):
        """Test conversion to dictionary."""
        result = BenchmarkResult(
            name="test",
            extractor_name="TestExtractor",
            test_count=10,
            metrics=ExtractionMetrics(),
            total_latency_ms=1000.0,
            avg_latency_ms=100.0,
            throughput_docs_per_sec=10.0,
        )
        d = result.to_dict()
        assert d["name"] == "test"
        assert d["extractor_name"] == "TestExtractor"
        assert d["test_count"] == 10
        assert "metrics" in d

    def test_relation_scores_default_to_zero(self):
        """A result without relation metrics reports zeros, not errors."""
        result = BenchmarkResult(
            name="test",
            extractor_name="TestExtractor",
            test_count=1,
            metrics=ExtractionMetrics(),
            total_latency_ms=1.0,
            avg_latency_ms=1.0,
            throughput_docs_per_sec=1.0,
        )
        assert result.relation_f1 == 0.0
        assert result.strict_relation_f1 == 0.0
        assert result.to_dict()["relation_metrics"] is None

    def test_strict_falls_back_to_lenient_numbers(self):
        """With a strict policy the two reported numbers are the same."""
        metrics = RelationMetrics(true_positives=3, false_positives=1, false_negatives=1)
        result = BenchmarkResult(
            name="test",
            extractor_name="TestExtractor",
            test_count=1,
            metrics=ExtractionMetrics(),
            total_latency_ms=1.0,
            avg_latency_ms=1.0,
            throughput_docs_per_sec=1.0,
            relation_metrics=metrics,
        )
        assert result.relation_precision == result.strict_relation_precision == 0.75
        assert result.relation_recall == result.strict_relation_recall == 0.75
        assert result.relation_f1 == result.strict_relation_f1 == 0.75

    def test_strict_numbers_reported_alongside_lenient(self):
        """A lenient run reports both numbers in to_dict."""
        metrics = RelationMetrics(
            true_positives=2,
            false_negatives=0,
            lenient_matches=1,
            strict=RelationMetrics(true_positives=1, false_positives=1, false_negatives=1),
        )
        result = BenchmarkResult(
            name="test",
            extractor_name="TestExtractor",
            test_count=1,
            metrics=ExtractionMetrics(),
            total_latency_ms=1.0,
            avg_latency_ms=1.0,
            throughput_docs_per_sec=1.0,
            relation_metrics=metrics,
        )
        d = result.to_dict()
        assert d["relation_f1"] == 1.0
        assert d["strict_relation_f1"] == 0.5
        assert d["relation_metrics"]["lenient_matches"] == 1
        assert d["relation_metrics"]["strict"]["true_positives"] == 1


class TestNormalizeName:
    """Tests for normalize_name."""

    def test_casefold_and_strip(self):
        """Names are case-folded and trimmed."""
        assert normalize_name("  Acme Corp  ") == "acme corp"

    def test_collapses_whitespace(self):
        """Runs of whitespace collapse to a single space."""
        assert normalize_name("Acme\t\n  Corp") == "acme corp"

    def test_strips_leading_determiner(self):
        """A single leading determiner is stripped."""
        assert normalize_name("The Veneto Review") == "veneto review"
        assert normalize_name("A Coruna") == "coruna"
        assert normalize_name("an Event") == "event"

    def test_keeps_internal_determiners(self):
        """Determiners inside the name are untouched."""
        assert normalize_name("Bank of the West") == "bank of the west"

    def test_unicode_normalization(self):
        """Decomposed and composed forms normalise to the same key."""
        assert normalize_name("São Paulo") == normalize_name("São Paulo")

    def test_used_by_expected_entity(self):
        """ExpectedEntity matching uses the same normalisation."""
        expected = ExpectedEntity(name="Veneto Review", entity_type="ORGANIZATION")
        assert expected.matches("The  Veneto   Review", "ORGANIZATION") is True


class TestMatchPolicy:
    """Tests for MatchPolicy."""

    def test_default_is_lenient(self):
        """The default policy allows aliases, so it is lenient."""
        assert MatchPolicy().is_lenient is True

    def test_strict_policy_is_not_lenient(self):
        """Canonical-only, directed, no fuzzy is strict."""
        assert MatchPolicy(use_aliases=False).is_lenient is False

    def test_undirected_is_lenient(self):
        """Accepting reversed triples is a loosening."""
        assert MatchPolicy(use_aliases=False, directed=False).is_lenient is True

    def test_as_strict_keeps_normalization(self):
        """as_strict drops loosening but keeps name normalisation."""
        strict = MatchPolicy(fuzzy_threshold=0.9, directed=False).as_strict()
        assert strict.use_aliases is False
        assert strict.fuzzy_threshold is None
        assert strict.directed is True
        assert strict.normalize_names is True

    def test_alias_match_is_recorded_as_loosening(self):
        """An alias hit reports that loosening was needed."""
        matched, lenient = MatchPolicy().name_matches("Acme", "Acme Corp", ["Acme"])
        assert (matched, lenient) == (True, True)

    def test_canonical_match_needs_no_loosening(self):
        """A canonical hit reports no loosening."""
        matched, lenient = MatchPolicy().name_matches("acme corp", "Acme Corp", ["Acme"])
        assert (matched, lenient) == (True, False)

    def test_aliases_ignored_when_disabled(self):
        """use_aliases=False rejects alias surface forms."""
        matched, _ = MatchPolicy(use_aliases=False).name_matches("Acme", "Acme Corp", ["Acme"])
        assert matched is False

    def test_no_normalization(self):
        """normalize_names=False compares the trimmed surface forms."""
        policy = MatchPolicy(normalize_names=False)
        assert policy.name_matches("acme corp", "Acme Corp")[0] is False
        assert policy.name_matches(" Acme Corp ", "Acme Corp")[0] is True

    def test_invalid_fuzzy_threshold(self):
        """Out-of-range fuzzy thresholds are rejected."""
        with pytest.raises(ValueError, match="fuzzy_threshold"):
            MatchPolicy(fuzzy_threshold=1.5)

    def test_fuzzy_match(self):
        """A near-miss matches above the fuzzy threshold."""
        pytest.importorskip("rapidfuzz")
        policy = MatchPolicy(use_aliases=False, fuzzy_threshold=0.85)
        matched, lenient = policy.name_matches("Acme Corpp", "Acme Corp")
        assert (matched, lenient) == (True, True)
        assert policy.name_matches("Totally Different", "Acme Corp")[0] is False

    def test_round_trip_dict(self):
        """to_dict/from_dict round trip."""
        policy = MatchPolicy(normalize_names=False, use_aliases=False, directed=False)
        assert MatchPolicy.from_dict(policy.to_dict()) == policy

    def test_from_dict_defaults(self):
        """Missing keys fall back to the defaults."""
        assert MatchPolicy.from_dict(None) == MatchPolicy()
        assert MatchPolicy.from_dict({}) == MatchPolicy()
        assert MatchPolicy.from_dict({"directed": False}).use_aliases is True


class TestRelationMetrics:
    """Tests for RelationMetrics."""

    def test_precision_recall_f1(self):
        """Counts produce the expected scores."""
        metrics = RelationMetrics(true_positives=8, false_positives=2, false_negatives=2)
        assert metrics.precision == 0.8
        assert metrics.recall == 0.8
        assert abs(metrics.f1_score - 0.8) < 0.001

    def test_zero_when_empty(self):
        """Empty counts score zero rather than dividing by zero."""
        metrics = RelationMetrics()
        assert metrics.precision == 0.0
        assert metrics.recall == 0.0
        assert metrics.f1_score == 0.0

    def test_to_dict(self):
        """to_dict carries the counts and the strict counterpart."""
        metrics = RelationMetrics(
            relation_type="EMPLOYED_BY",
            true_positives=2,
            false_positives=1,
            lenient_matches=1,
            strict=RelationMetrics(relation_type="EMPLOYED_BY", true_positives=1),
        )
        d = metrics.to_dict()
        assert d["relation_type"] == "EMPLOYED_BY"
        assert d["lenient_matches"] == 1
        assert d["strict"]["true_positives"] == 1

    def test_to_dict_without_strict(self):
        """The strict key is present and null when there is no counterpart."""
        assert RelationMetrics().to_dict()["strict"] is None


class TestExpectedRelation:
    """Tests for ExpectedRelation."""

    def test_exact_match(self):
        """Exact triples match under the default policy."""
        relation = ExpectedRelation("John Smith", "EMPLOYED_BY", "Acme Corp")
        assert relation.matches("John Smith", "EMPLOYED_BY", "Acme Corp") is True

    def test_relation_name_must_match_exactly(self):
        """The relation vocabulary is closed: near-misses are misses."""
        relation = ExpectedRelation("John Smith", "EMPLOYED_BY", "Acme Corp")
        assert relation.matches("John Smith", "WORKS_AT", "Acme Corp") is False
        assert relation.matches("John Smith", "employed_by", "Acme Corp") is True

    def test_endpoint_aliases(self):
        """Alias surface forms match and are flagged as loosened."""
        relation = ExpectedRelation(
            source="John Smith",
            relation_type="EMPLOYED_BY",
            target="Acme Corp",
            target_aliases=["Acme", "ACME Corporation"],
        )
        matched, lenient = relation.match_detail("John Smith", "EMPLOYED_BY", "ACME Corporation")
        assert (matched, lenient) == (True, True)

    def test_aliases_rejected_under_strict_policy(self):
        """A strict policy rejects alias endpoints."""
        relation = ExpectedRelation(
            source="John Smith",
            relation_type="EMPLOYED_BY",
            target="Acme Corp",
            target_aliases=["Acme"],
        )
        strict = MatchPolicy(use_aliases=False)
        assert relation.matches("John Smith", "EMPLOYED_BY", "Acme", strict) is False

    def test_direction_matters_by_default(self):
        """A reversed triple is a miss when directed=True."""
        relation = ExpectedRelation("Vesper", "SUBSIDIARY_OF", "Acme")
        assert relation.matches("Acme", "SUBSIDIARY_OF", "Vesper") is False

    def test_undirected_policy_accepts_reverse(self):
        """directed=False accepts the reversed triple as a lenient match."""
        relation = ExpectedRelation("Nadia Osei", "KNOWS", "Tomas Leal")
        policy = MatchPolicy(directed=False)
        matched, lenient = relation.match_detail("Tomas Leal", "KNOWS", "Nadia Osei", policy)
        assert (matched, lenient) == (True, True)
        forward, forward_lenient = relation.match_detail(
            "Nadia Osei", "KNOWS", "Tomas Leal", policy
        )
        assert (forward, forward_lenient) == (True, False)

    def test_from_value_tuple(self):
        """Plain triples coerce into ExpectedRelation."""
        relation = ExpectedRelation.from_value(("A", "KNOWS", "B"))
        assert relation.as_triple() == ("A", "KNOWS", "B")

    def test_from_value_dict(self):
        """Mappings coerce, including alias lists."""
        relation = ExpectedRelation.from_value(
            {
                "source": "A",
                "relation_type": "EMPLOYED_BY",
                "target": "B",
                "target_aliases": ["B Corp"],
            }
        )
        assert relation.target_aliases == ["B Corp"]

    def test_from_value_passthrough(self):
        """An ExpectedRelation is returned unchanged."""
        relation = ExpectedRelation("A", "KNOWS", "B")
        assert ExpectedRelation.from_value(relation) is relation

    def test_from_value_rejects_wrong_arity(self):
        """A non-triple sequence is an error."""
        with pytest.raises(ValueError, match="triple"):
            ExpectedRelation.from_value(("A", "KNOWS"))

    def test_to_dict_round_trip(self):
        """to_dict/from_value round trip."""
        relation = ExpectedRelation("A", "KNOWS", "B", source_aliases=["Al"])
        assert ExpectedRelation.from_value(relation.to_dict()) == relation


class TestCalculateRelationMetrics:
    """Tests for calculate_relation_metrics."""

    def test_all_correct(self):
        """Perfect extraction scores 1.0."""
        expected = [
            ExpectedRelation("John", "EMPLOYED_BY", "Acme"),
            ExpectedRelation("Jane", "KNOWS", "John"),
        ]
        extracted = [("John", "EMPLOYED_BY", "Acme"), ("Jane", "KNOWS", "John")]
        metrics = calculate_relation_metrics(expected, extracted)
        assert (metrics.true_positives, metrics.false_positives, metrics.false_negatives) == (
            2,
            0,
            0,
        )
        assert metrics.f1_score == 1.0

    def test_missing_relation_is_false_negative(self):
        """An unextracted gold triple is a false negative."""
        expected = [
            ExpectedRelation("John", "EMPLOYED_BY", "Acme"),
            ExpectedRelation("Jane", "KNOWS", "John"),
        ]
        metrics = calculate_relation_metrics(expected, [("John", "EMPLOYED_BY", "Acme")])
        assert (metrics.true_positives, metrics.false_negatives) == (1, 1)

    def test_extra_relation_is_false_positive(self):
        """A hallucinated triple is a false positive."""
        expected = [ExpectedRelation("John", "EMPLOYED_BY", "Acme")]
        extracted = [("John", "EMPLOYED_BY", "Acme"), ("John", "OWNS", "Acme")]
        metrics = calculate_relation_metrics(expected, extracted)
        assert (metrics.true_positives, metrics.false_positives) == (1, 1)

    def test_duplicate_extraction_does_not_double_count(self):
        """Matching is one-to-one: the second copy is a false positive."""
        expected = [ExpectedRelation("John", "EMPLOYED_BY", "Acme")]
        extracted = [("John", "EMPLOYED_BY", "Acme")] * 3
        metrics = calculate_relation_metrics(expected, extracted)
        assert (metrics.true_positives, metrics.false_positives, metrics.false_negatives) == (
            1,
            2,
            0,
        )
        assert metrics.precision == pytest.approx(1 / 3)
        assert metrics.recall == 1.0

    def test_duplicate_gold_can_be_matched_twice(self):
        """Two identical gold triples need two extractions to both match."""
        expected = [
            ExpectedRelation("John", "EMPLOYED_BY", "Acme"),
            ExpectedRelation("John", "EMPLOYED_BY", "Acme"),
        ]
        metrics = calculate_relation_metrics(expected, [("John", "EMPLOYED_BY", "Acme")] * 2)
        assert (metrics.true_positives, metrics.false_positives, metrics.false_negatives) == (
            2,
            0,
            0,
        )

    def test_strict_counterpart_attached_for_lenient_policy(self):
        """A lenient run reports the strict numbers too."""
        expected = [
            ExpectedRelation(
                "John", "EMPLOYED_BY", "Acme Corp", target_aliases=["ACME Corporation"]
            )
        ]
        metrics = calculate_relation_metrics(
            expected, [("John", "EMPLOYED_BY", "ACME Corporation")]
        )
        assert metrics.true_positives == 1
        assert metrics.lenient_matches == 1
        assert metrics.strict is not None
        assert metrics.strict.true_positives == 0
        assert metrics.strict.false_positives == 1
        assert metrics.strict.false_negatives == 1

    def test_no_strict_counterpart_for_strict_policy(self):
        """A strict run has nothing extra to report."""
        metrics = calculate_relation_metrics(
            [ExpectedRelation("John", "EMPLOYED_BY", "Acme")],
            [("John", "EMPLOYED_BY", "Acme")],
            MatchPolicy(use_aliases=False),
        )
        assert metrics.strict is None

    def test_exact_match_preferred_over_alias_match(self):
        """Greedy matching does not burn a gold triple on a lenient hit."""
        expected = [
            ExpectedRelation("John", "EMPLOYED_BY", "Acme Corp", target_aliases=["Acme"]),
            ExpectedRelation("John", "EMPLOYED_BY", "Acme"),
        ]
        metrics = calculate_relation_metrics(expected, [("John", "EMPLOYED_BY", "Acme")])
        assert metrics.true_positives == 1
        assert metrics.lenient_matches == 0

    def test_empty_inputs(self):
        """No gold and no extraction scores zero without raising."""
        metrics = calculate_relation_metrics([], [])
        assert metrics.f1_score == 0.0
        assert metrics.relation_type == "ALL"

    def test_extraction_metrics_carry_relations(self):
        """calculate_extraction_metrics can score relations too."""
        metrics = calculate_extraction_metrics(
            [ExpectedEntity(name="John", entity_type="PERSON")],
            [("John", "PERSON")],
            expected_relations=[ExpectedRelation("John", "EMPLOYED_BY", "Acme")],
            extracted_relations=[("John", "EMPLOYED_BY", "Acme")],
        )
        assert metrics.relation_metrics is not None
        assert metrics.relation_metrics.f1_score == 1.0
        assert metrics.to_dict()["relation_metrics"]["true_positives"] == 1

    def test_extraction_metrics_relation_key_is_none_by_default(self):
        """Without gold relations the key is present but null."""
        metrics = calculate_extraction_metrics([], [])
        assert metrics.relation_metrics is None
        assert metrics.to_dict()["relation_metrics"] is None


class TestAggregateRelationMetrics:
    """Tests for aggregate_relation_metrics."""

    def test_sums_counts(self):
        """Counts are summed rather than re-matched."""
        total = aggregate_relation_metrics(
            [
                RelationMetrics(true_positives=1, false_positives=1),
                RelationMetrics(true_positives=2, false_negatives=3),
            ]
        )
        assert (total.true_positives, total.false_positives, total.false_negatives) == (3, 1, 3)

    def test_sums_strict_counterparts(self):
        """Strict counts aggregate alongside the lenient ones."""
        total = aggregate_relation_metrics(
            [
                RelationMetrics(
                    true_positives=1,
                    lenient_matches=1,
                    strict=RelationMetrics(false_positives=1, false_negatives=1),
                ),
                RelationMetrics(true_positives=1),
            ]
        )
        assert total.true_positives == 2
        assert total.lenient_matches == 1
        assert total.strict is not None
        # The second case had no strict counterpart, so its own counts are used.
        assert total.strict.true_positives == 1
        assert total.strict.false_positives == 1

    def test_empty(self):
        """Aggregating nothing yields zeroed metrics."""
        total = aggregate_relation_metrics([])
        assert total.true_positives == 0
        assert total.strict is None


class TestBCubed:
    """Tests for the B-cubed entity-resolution score."""

    def test_perfect_clustering(self):
        """Identical clusterings score 1.0 on both sides."""
        gold = {"a": "g1", "b": "g1", "c": "g2"}
        predicted = {"a": "p1", "b": "p1", "c": "p2"}
        score = bcubed(predicted, gold)
        assert (score.precision, score.recall, score.f1_score) == (1.0, 1.0, 1.0)
        assert score.evaluated_mentions == 3
        assert score.ignored_mentions == 0

    def test_merged_clusters_lose_precision(self):
        """Merging two gold clusters halves precision, recall stays 1.0."""
        gold = {"a": "g1", "b": "g1", "c": "g2", "d": "g2"}
        predicted = dict.fromkeys(["a", "b", "c", "d"], "p1")
        precision, recall, f1 = bcubed(predicted, gold)
        assert precision == pytest.approx(0.5)
        assert recall == pytest.approx(1.0)
        assert f1 == pytest.approx(2 / 3)

    def test_split_clusters_lose_recall(self):
        """Splitting one gold cluster halves recall, precision stays 1.0."""
        gold = dict.fromkeys(["a", "b", "c", "d"], "g1")
        predicted = {"a": "p1", "b": "p1", "c": "p2", "d": "p2"}
        precision, recall, f1 = bcubed(predicted, gold)
        assert precision == pytest.approx(1.0)
        assert recall == pytest.approx(0.5)
        assert f1 == pytest.approx(2 / 3)

    def test_partial_overlap(self):
        """A hand-computed asymmetric case."""
        # Gold: {a,b,c} and {d}. Predicted: {a,b} and {c,d}.
        gold = {"a": "g1", "b": "g1", "c": "g1", "d": "g2"}
        predicted = {"a": "p1", "b": "p1", "c": "p2", "d": "p2"}
        precision, recall, _ = bcubed(predicted, gold)
        # precision: a,b -> 2/2; c -> 1/2; d -> 1/2  => (1 + 1 + 0.5 + 0.5) / 4
        assert precision == pytest.approx(0.75)
        # recall: a,b,c -> 2/3, 2/3, 1/3; d -> 1/1
        assert recall == pytest.approx((2 / 3 + 2 / 3 + 1 / 3 + 1) / 4)

    def test_ignores_mentions_missing_from_either_side(self):
        """Unmatched mention ids are ignored and counted."""
        gold = {"a": "g1", "b": "g1", "missing_from_prediction": "g2"}
        predicted = {"a": "p1", "b": "p1", "spurious": "p2"}
        score = bcubed(predicted, gold)
        assert score.evaluated_mentions == 2
        assert score.ignored_mentions == 2
        assert score.f1_score == 1.0

    def test_no_overlap(self):
        """Disjoint mention sets score zero."""
        score = bcubed({"a": "p1"}, {"b": "g1"})
        assert (score.precision, score.recall, score.f1_score) == (0.0, 0.0, 0.0)
        assert score.evaluated_mentions == 0
        assert score.ignored_mentions == 2

    def test_unpacks_as_triple(self):
        """BCubedScore unpacks as (precision, recall, f1)."""
        score = bcubed({"a": "p1"}, {"a": "g1"})
        precision, recall, f1 = score
        assert isinstance(score, BCubedScore)
        assert (precision, recall, f1) == (1.0, 1.0, 1.0)
        assert score.to_dict()["evaluated_mentions"] == 1


class TestResolutionGold:
    """Tests for the resolution gold loader."""

    def test_load(self):
        """The gold file parses into mentions and clusters."""
        gold = load_resolution_gold(POLEO_RESOLUTION_GOLD)
        assert len(gold.mentions) > 0
        assert gold.mentions[0].id.startswith("poleo-")
        assert gold.mentions[0].entity_type

    def test_cluster_map_drops_unknown_mentions(self, tmp_path):
        """Cluster entries without a mention are not scored."""
        path = tmp_path / "gold.json"
        path.write_text(
            json.dumps(
                {
                    "mentions": [
                        {"id": "d1:m1", "text": "Acme", "type": "ORGANIZATION", "doc": "d1"}
                    ],
                    "clusters": {"d1:m1": "acme", "d1:m2": "ghost"},
                }
            )
        )
        gold = load_resolution_gold(path)
        assert gold.cluster_map() == {"d1:m1": "acme"}

    def test_by_doc(self):
        """Mentions group by document id."""
        gold = load_resolution_gold(POLEO_RESOLUTION_GOLD)
        grouped = gold.by_doc()
        assert "poleo-01" in grouped
        assert all(m.doc == "poleo-01" for m in grouped["poleo-01"])

    def test_score_against_itself(self):
        """Scoring the gold clustering against itself is perfect."""
        gold = load_resolution_gold(POLEO_RESOLUTION_GOLD)
        score = gold.score(gold.cluster_map())
        assert score.f1_score == 1.0
        assert score.ignored_mentions == 0


class _StubEntity:
    """Minimal stand-in for ExtractedEntity."""

    def __init__(self, name: str, entity_type: str) -> None:
        self.name = name
        self.type = entity_type


class _StubRelation:
    """Minimal stand-in for ExtractedRelation."""

    def __init__(self, source: str, relation_type: str, target: str) -> None:
        self.source = source
        self.relation_type = relation_type
        self.target = target


class _StubResult:
    """Minimal stand-in for ExtractionResult."""

    def __init__(self, entities, relations) -> None:
        self.entities = entities
        self.relations = relations


class _StubExtractor:
    """Extractor returning a fixed result and recording its kwargs."""

    def __init__(self, entities, relations) -> None:
        self._entities = entities
        self._relations = relations
        self.calls: list[dict] = []

    async def extract(self, text, **kwargs):
        self.calls.append(kwargs)
        return _StubResult(self._entities, self._relations)


class TestRunnerRelationScoring:
    """End-to-end relation scoring through BenchmarkRunner."""

    def _suite(self, **config_kwargs):
        return BenchmarkSuite(
            name="relation_suite",
            description="Relations end to end",
            test_cases=[
                BenchmarkTestCase(
                    id="tc-001",
                    text="Priya Raghavan joined Acme Corp.",
                    expected_entities=[
                        ExpectedEntity(name="Priya Raghavan", entity_type="PERSON"),
                    ],
                    expected_relations=[
                        ExpectedRelation(
                            source="Priya Raghavan",
                            relation_type="EMPLOYED_BY",
                            target="Acme Corp",
                            target_aliases=["Acme"],
                        ),
                    ],
                ),
            ],
            config=BenchmarkConfig(warmup_runs=0, num_runs=1, **config_kwargs),
        )

    @pytest.mark.asyncio
    async def test_run_case_returns_relations(self):
        """run_case keeps the extracted triples."""
        extractor = _StubExtractor(
            [_StubEntity("Priya Raghavan", "PERSON")],
            [_StubRelation("Priya Raghavan", "EMPLOYED_BY", "Acme Corp")],
        )
        suite = self._suite()
        run = await BenchmarkRunner(extractor).run_case(suite.test_cases[0], suite.config)
        assert run.entities == [("Priya Raghavan", "PERSON")]
        assert run.relations == [("Priya Raghavan", "EMPLOYED_BY", "Acme Corp")]
        assert run.error is None
        assert extractor.calls == [{"extract_relations": True}]

    @pytest.mark.asyncio
    async def test_run_case_records_errors(self):
        """A failing extraction is recorded, not raised."""
        extractor = _StubExtractor([], [])
        extractor.extract = AsyncMock(side_effect=RuntimeError("boom"))
        suite = self._suite()
        run = await BenchmarkRunner(extractor).run_case(suite.test_cases[0], suite.config)
        assert run.error == "boom"
        assert run.entities == []

    @pytest.mark.asyncio
    async def test_perfect_relation_extraction(self):
        """Exact triples score 1.0 strict and lenient."""
        extractor = _StubExtractor(
            [_StubEntity("Priya Raghavan", "PERSON")],
            [_StubRelation("Priya Raghavan", "EMPLOYED_BY", "Acme Corp")],
        )
        result = await BenchmarkRunner(extractor).run_suite(self._suite())
        assert result.relation_f1 == 1.0
        assert result.strict_relation_f1 == 1.0
        assert result.metrics.relation_metrics is result.relation_metrics

    @pytest.mark.asyncio
    async def test_alias_endpoint_splits_strict_and_lenient(self):
        """An alias endpoint scores 1.0 lenient and 0.0 strict."""
        extractor = _StubExtractor(
            [_StubEntity("Priya Raghavan", "PERSON")],
            [_StubRelation("Priya Raghavan", "EMPLOYED_BY", "Acme")],
        )
        result = await BenchmarkRunner(extractor).run_suite(self._suite())
        assert result.relation_f1 == 1.0
        assert result.strict_relation_f1 == 0.0
        assert result.relation_metrics.lenient_matches == 1

    @pytest.mark.asyncio
    async def test_strict_policy_scores_alias_as_miss(self):
        """A strict suite policy rejects the alias endpoint outright."""
        extractor = _StubExtractor(
            [_StubEntity("Priya Raghavan", "PERSON")],
            [_StubRelation("Priya Raghavan", "EMPLOYED_BY", "Acme")],
        )
        suite = self._suite(match_policy=MatchPolicy(use_aliases=False))
        result = await BenchmarkRunner(extractor).run_suite(suite)
        assert result.relation_f1 == 0.0
        assert result.relation_metrics.false_positives == 1
        assert result.relation_metrics.false_negatives == 1

    @pytest.mark.asyncio
    async def test_missing_relations_score_zero(self):
        """An extractor that returns no relations still scores recall 0."""
        extractor = _StubExtractor([_StubEntity("Priya Raghavan", "PERSON")], [])
        result = await BenchmarkRunner(extractor).run_suite(self._suite())
        assert result.relation_recall == 0.0
        assert result.relation_metrics.false_negatives == 1

    @pytest.mark.asyncio
    async def test_no_gold_relations_leaves_metrics_none(self):
        """Cases without gold relations produce no relation metrics."""
        extractor = _StubExtractor([_StubEntity("Priya Raghavan", "PERSON")], [])
        suite = self._suite()
        suite.test_cases[0].expected_relations = []
        result = await BenchmarkRunner(extractor).run_suite(suite)
        assert result.relation_metrics is None
        assert result.to_dict()["relation_f1"] == 0.0

    @pytest.mark.asyncio
    async def test_relations_aggregate_per_case(self):
        """A triple from one case cannot match gold from another."""
        suite = self._suite()
        suite.test_cases.append(
            BenchmarkTestCase(
                id="tc-002",
                text="Elena Duarte manages the depot.",
                expected_entities=[ExpectedEntity(name="Elena Duarte", entity_type="PERSON")],
                expected_relations=[
                    ExpectedRelation("Elena Duarte", "EMPLOYED_BY", "Northwind Logistics")
                ],
            )
        )
        # The extractor always returns the tc-001 triple, so tc-002 scores a miss.
        extractor = _StubExtractor(
            [_StubEntity("Priya Raghavan", "PERSON")],
            [_StubRelation("Priya Raghavan", "EMPLOYED_BY", "Acme Corp")],
        )
        result = await BenchmarkRunner(extractor).run_suite(suite)
        assert result.relation_metrics.true_positives == 1
        assert result.relation_metrics.false_positives == 1
        assert result.relation_metrics.false_negatives == 1


class TestBenchmarkTestCaseRelations:
    """Tests for expected_relations handling on BenchmarkTestCase."""

    def test_tuples_are_coerced(self):
        """Plain triples become ExpectedRelation objects."""
        tc = BenchmarkTestCase(
            id="tc-001",
            text="t",
            expected_entities=[],
            expected_relations=[("A", "KNOWS", "B")],
        )
        assert tc.expected_relations == [ExpectedRelation("A", "KNOWS", "B")]
        assert tc.expected_relation_objects()[0].as_triple() == ("A", "KNOWS", "B")

    def test_from_dict_accepts_both_shapes(self):
        """JSON may hold 3-element lists or relation mappings."""
        tc = BenchmarkTestCase.from_dict(
            {
                "id": "tc-001",
                "text": "t",
                "expected_entities": [],
                "expected_relations": [
                    ["A", "KNOWS", "B"],
                    {"source": "A", "relation_type": "EMPLOYED_BY", "target": "C"},
                ],
            }
        )
        assert [r.relation_type for r in tc.expected_relation_objects()] == [
            "KNOWS",
            "EMPLOYED_BY",
        ]

    def test_to_dict_round_trip(self):
        """to_dict output reloads into the same relations."""
        tc = BenchmarkTestCase(
            id="tc-001",
            text="t",
            expected_entities=[ExpectedEntity(name="A", entity_type="PERSON")],
            expected_relations=[ExpectedRelation("A", "EMPLOYED_BY", "B", target_aliases=["b"])],
        )
        reloaded = BenchmarkTestCase.from_dict(tc.to_dict())
        assert reloaded.expected_relation_objects() == tc.expected_relation_objects()

    def test_suite_json_round_trip_keeps_policy(self, tmp_path):
        """The match policy survives a save/load cycle."""
        suite = BenchmarkSuite(
            name="s",
            description="d",
            test_cases=[
                BenchmarkTestCase(
                    id="tc-001",
                    text="t",
                    expected_entities=[],
                    expected_relations=[("A", "KNOWS", "B")],
                )
            ],
            config=BenchmarkConfig(
                match_policy=MatchPolicy(directed=False), extract_relations=True
            ),
        )
        path = tmp_path / "suite.json"
        suite.to_json_file(path)
        reloaded = BenchmarkSuite.from_json_file(path)
        assert reloaded.config.match_policy == MatchPolicy(directed=False)
        assert reloaded.config.extract_relations is True
        assert reloaded.test_cases[0].expected_relation_objects()[0].as_triple() == (
            "A",
            "KNOWS",
            "B",
        )


class TestSampleSuiteRelations:
    """The sample suite exercises relations too."""

    def test_has_a_case_with_relations(self):
        """At least one sample case carries gold relations."""
        suite = create_sample_benchmark_suite()
        with_relations = [tc for tc in suite.test_cases if tc.expected_relation_objects()]
        assert with_relations
        assert all(
            r.relation_type == r.relation_type.upper()
            for tc in with_relations
            for r in tc.expected_relation_objects()
        )


class TestGoldData:
    """The shipped gold data loads and is internally consistent."""

    @pytest.fixture(scope="class")
    def suite(self):
        """Load the POLE+O gold suite."""
        return BenchmarkSuite.from_json_file(POLEO_GOLD)

    @pytest.fixture(scope="class")
    def resolution_gold(self):
        """Load the POLE+O resolution gold clusters."""
        return load_resolution_gold(POLEO_RESOLUTION_GOLD)

    def test_suite_loads(self, suite):
        """The suite has the documented size."""
        assert 8 <= len(suite.test_cases) <= 12
        assert suite.config.extract_relations is True
        assert all(tc.text.strip() for tc in suite.test_cases)

    def test_case_ids_are_unique(self, suite):
        """Case ids are unique."""
        ids = [tc.id for tc in suite.test_cases]
        assert len(ids) == len(set(ids))

    def test_every_case_has_entities_and_most_have_relations(self, suite):
        """Every case is annotated; every case carries at least one triple."""
        assert all(tc.expected_entities for tc in suite.test_cases)
        assert all(tc.expected_relation_objects() for tc in suite.test_cases)

    def test_canonical_names_appear_in_text(self, suite):
        """Each gold entity's canonical name is a literal substring of the text."""
        for tc in suite.test_cases:
            for entity in tc.expected_entities:
                assert entity.name in tc.text, f"{tc.id}: {entity.name!r} missing from text"

    def test_relation_endpoints_name_expected_entities(self, suite):
        """Every relation endpoint names an expected entity or one of its aliases."""
        for tc in suite.test_cases:
            index = {
                normalize_name(surface): entity
                for entity in tc.expected_entities
                for surface in entity.surface_forms()
            }
            for relation in tc.expected_relation_objects():
                assert normalize_name(relation.source) in index, (
                    f"{tc.id}: unknown source {relation.source!r}"
                )
                assert normalize_name(relation.target) in index, (
                    f"{tc.id}: unknown target {relation.target!r}"
                )

    def test_relation_endpoint_aliases_match_the_entities(self, suite):
        """Endpoint alias lists are a subset of the entity's aliases."""
        for tc in suite.test_cases:
            index = {
                normalize_name(surface): entity
                for entity in tc.expected_entities
                for surface in entity.surface_forms()
            }
            for relation in tc.expected_relation_objects():
                source = index[normalize_name(relation.source)]
                target = index[normalize_name(relation.target)]
                assert set(relation.source_aliases) <= set(source.aliases)
                assert set(relation.target_aliases) <= set(target.aliases)

    def test_relation_vocabulary_is_closed(self, suite):
        """Only constrained POLE+O relation names are used."""
        used = {
            relation.relation_type
            for tc in suite.test_cases
            for relation in tc.expected_relation_objects()
        }
        assert used <= POLEO_RELATION_TYPES
        # The gold set is meant to cover the whole vocabulary.
        assert used == POLEO_RELATION_TYPES

    def test_relation_endpoint_types_are_permitted(self, suite):
        """Endpoint entity types satisfy the POLE+O source/target constraints."""
        for tc in suite.test_cases:
            index = {
                normalize_name(surface): entity
                for entity in tc.expected_entities
                for surface in entity.surface_forms()
            }
            for relation in tc.expected_relation_objects():
                source_types, target_types = POLEO_RELATION_CONSTRAINTS[relation.relation_type]
                source = index[normalize_name(relation.source)]
                target = index[normalize_name(relation.target)]
                assert source.entity_type in source_types, f"{tc.id}: {relation.relation_type}"
                assert target.entity_type in target_types, f"{tc.id}: {relation.relation_type}"

    def test_gold_scores_itself_perfectly(self, suite):
        """Scoring the gold triples against themselves is a sanity check."""
        for tc in suite.test_cases:
            expected = tc.expected_relation_objects()
            metrics = calculate_relation_metrics(
                expected, [r.as_triple() for r in expected], suite.config.match_policy
            )
            assert metrics.f1_score == 1.0
            assert metrics.lenient_matches == 0

    def test_every_mention_has_a_cluster(self, resolution_gold):
        """Every resolution mention appears in clusters."""
        for mention in resolution_gold.mentions:
            assert mention.id in resolution_gold.clusters, f"{mention.id} has no cluster"

    def test_every_cluster_key_has_a_mention(self, resolution_gold):
        """No cluster entry dangles."""
        ids = {mention.id for mention in resolution_gold.mentions}
        assert set(resolution_gold.clusters) <= ids

    def test_mention_ids_are_unique(self, resolution_gold):
        """Mention ids are unique."""
        ids = [mention.id for mention in resolution_gold.mentions]
        assert len(ids) == len(set(ids))

    def test_mentions_point_at_known_documents(self, suite, resolution_gold):
        """Mention docs are case ids and mention ids carry the doc prefix."""
        cases = {tc.id: tc for tc in suite.test_cases}
        for mention in resolution_gold.mentions:
            assert mention.doc in cases, f"{mention.id}: unknown doc {mention.doc!r}"
            assert mention.id.startswith(f"{mention.doc}:")

    def test_mention_text_occurs_in_its_document(self, suite, resolution_gold):
        """Each mention surface form is a literal substring of its document."""
        cases = {tc.id: tc for tc in suite.test_cases}
        for mention in resolution_gold.mentions:
            text = cases[mention.doc].text
            assert mention.text in text, f"{mention.id}: {mention.text!r} not in {mention.doc}"

    def test_clusters_span_documents(self, resolution_gold):
        """At least one gold cluster is cross-document (the point of the set)."""
        docs_by_cluster: dict[str, set[str]] = {}
        for mention in resolution_gold.mentions:
            cluster = resolution_gold.clusters[mention.id]
            docs_by_cluster.setdefault(cluster, set()).add(mention.doc or "")
        assert any(len(docs) > 1 for docs in docs_by_cluster.values())
