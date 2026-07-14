"""Validation tests for domain-schemas example scripts.

These tests validate that the domain schema scripts:
- Exist and have valid Python syntax
- Use the factory pattern for extractor creation
- Demonstrate new features (batch, streaming, GLiREL)
"""

import ast
from pathlib import Path

import pytest

EXAMPLES_DIR = Path(__file__).parent.parent.parent / "examples"
SCHEMAS_DIR = EXAMPLES_DIR / "domain-schemas"

ALL_SCHEMA_SCRIPTS = [
    "podcast_transcripts.py",
    "news_articles.py",
    "scientific_papers.py",
    "business_reports.py",
    "entertainment_content.py",
    "legal_documents.py",
    "medical_records.py",
    "poleo_investigations.py",
]


class TestDomainSchemasStructure:
    """Structure validation for domain-schemas example."""

    def test_directory_exists(self):
        """Verify the domain-schemas directory exists."""
        assert SCHEMAS_DIR.exists(), f"Domain schemas directory not found: {SCHEMAS_DIR}"

    def test_readme_exists(self):
        """Verify README.md exists."""
        readme = SCHEMAS_DIR / "README.md"
        assert readme.exists(), f"README.md not found: {readme}"

    @pytest.mark.parametrize("script", ALL_SCHEMA_SCRIPTS)
    def test_script_exists(self, script):
        """Verify each schema script exists."""
        script_path = SCHEMAS_DIR / script
        assert script_path.exists(), f"Schema script not found: {script_path}"

    @pytest.mark.parametrize("script", ALL_SCHEMA_SCRIPTS)
    def test_script_valid_python(self, script):
        """Verify each schema script has valid Python syntax."""
        script_path = SCHEMAS_DIR / script
        if not script_path.exists():
            pytest.skip(f"Script not found: {script}")
        try:
            ast.parse(script_path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            pytest.fail(f"Syntax error in {script}: {e}")

    @pytest.mark.parametrize("script", ALL_SCHEMA_SCRIPTS)
    def test_script_has_main_function(self, script):
        """Verify each script has an async main function."""
        script_path = SCHEMAS_DIR / script
        if not script_path.exists():
            pytest.skip(f"Script not found: {script}")
        content = script_path.read_text(encoding="utf-8")
        assert "async def main(" in content, f"{script} should have 'async def main()'"
        assert '__name__ == "__main__"' in content, f"{script} should have main entry point"

    @pytest.mark.parametrize("script", ALL_SCHEMA_SCRIPTS)
    def test_script_has_gliner_check(self, script):
        """Verify each script checks for GLiNER availability."""
        script_path = SCHEMAS_DIR / script
        if not script_path.exists():
            pytest.skip(f"Script not found: {script}")
        content = script_path.read_text(encoding="utf-8")
        assert "is_gliner_available" in content, f"{script} should check GLiNER availability"


class TestDomainSchemasFeatures:
    """Feature validation for domain-schemas example."""

    def test_podcast_uses_factory_pattern(self):
        """Verify podcast script uses create_gliner_extractor factory."""
        script = SCHEMAS_DIR / "podcast_transcripts.py"
        if not script.exists():
            pytest.skip("Script not found")
        content = script.read_text(encoding="utf-8")
        assert "create_gliner_extractor" in content, (
            "podcast_transcripts.py should use create_gliner_extractor factory"
        )
        assert "ExtractionConfig" in content, "podcast_transcripts.py should use ExtractionConfig"

    def test_podcast_uses_batch_extraction(self):
        """Verify podcast script demonstrates batch extraction."""
        script = SCHEMAS_DIR / "podcast_transcripts.py"
        if not script.exists():
            pytest.skip("Script not found")
        content = script.read_text(encoding="utf-8")
        assert "extract_batch" in content, (
            "podcast_transcripts.py should demonstrate batch extraction"
        )

    def test_news_uses_glirel(self):
        """Verify news script demonstrates GLiREL relation extraction."""
        script = SCHEMAS_DIR / "news_articles.py"
        if not script.exists():
            pytest.skip("Script not found")
        content = script.read_text(encoding="utf-8")
        assert "GLiNERWithRelationsExtractor" in content or "is_glirel_available" in content, (
            "news_articles.py should demonstrate GLiREL relation extraction"
        )

    def test_scientific_uses_streaming(self):
        """Verify scientific papers script demonstrates streaming extraction."""
        script = SCHEMAS_DIR / "scientific_papers.py"
        if not script.exists():
            pytest.skip("Script not found")
        content = script.read_text(encoding="utf-8")
        assert "StreamingExtractor" in content or "create_streaming_extractor" in content, (
            "scientific_papers.py should demonstrate streaming extraction"
        )

    def test_poleo_uses_glirel(self):
        """Verify POLE+O script demonstrates GLiREL relation extraction."""
        script = SCHEMAS_DIR / "poleo_investigations.py"
        if not script.exists():
            pytest.skip("Script not found")
        content = script.read_text(encoding="utf-8")
        assert "GLiNERWithRelationsExtractor" in content or "is_glirel_available" in content, (
            "poleo_investigations.py should demonstrate GLiREL relation extraction"
        )
