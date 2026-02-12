"""Tests for config validation, especially OPENAI_API_KEY requirement."""

import os
import sys
from unittest.mock import patch

import pytest
from pydantic import ValidationError

# Add src to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))


class TestConfigValidation:
    """Test configuration validation, especially OPENAI_API_KEY."""

    def test_config_requires_openai_api_key(self):
        """Test that config fails when OPENAI_API_KEY is missing."""
        from src.config import Settings

        # Clear environment
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValidationError) as exc_info:
                Settings()

            # Check that the error is about OPENAI_API_KEY
            error = str(exc_info.value)
            assert "OPENAI_API_KEY" in error or "openai_api_key" in error

    def test_config_accepts_valid_api_key(self):
        """Test that config accepts a valid API key."""
        from src.config import Settings

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test123"}, clear=True):
            settings = Settings()
            assert settings.openai_api_key.get_secret_value() == "sk-test123"

    def test_config_warns_on_invalid_api_key_format(self, capsys):
        """Test that config warns when API key doesn't start with sk-."""
        from src.config import Settings

        with patch.dict(os.environ, {"OPENAI_API_KEY": "invalid-key"}, clear=True):
            settings = Settings()
            captured = capsys.readouterr()

            # Should still work but emit a warning
            assert settings.openai_api_key.get_secret_value() == "invalid-key"
            assert "WARNING" in captured.err or "warning" in captured.err.lower()

    def test_config_rejects_empty_api_key(self):
        """Test that config rejects an empty API key."""
        from src.config import Settings

        with patch.dict(os.environ, {"OPENAI_API_KEY": ""}, clear=True):
            with pytest.raises(ValidationError):
                Settings()

    def test_config_rejects_whitespace_only_api_key(self):
        """Test that config rejects a whitespace-only API key."""
        from src.config import Settings

        with patch.dict(os.environ, {"OPENAI_API_KEY": "   "}, clear=True):
            with pytest.raises(ValidationError):
                Settings()

    def test_config_has_neo4j_defaults(self):
        """Test that Neo4j config has sensible defaults."""
        from src.config import Settings

        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-test"}, clear=True):
            settings = Settings()
            assert settings.neo4j_uri == "bolt://localhost:7687"
            assert settings.neo4j_username == "neo4j"
            assert settings.neo4j_password.get_secret_value() == "password"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
