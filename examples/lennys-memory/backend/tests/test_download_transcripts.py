"""Tests for the download_transcripts script."""

import sys
from pathlib import Path
from unittest.mock import MagicMock, mock_open, patch
from urllib.error import HTTPError, URLError

import pytest

# Add scripts to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "scripts"))

import download_transcripts


class TestDownloadTranscripts:
    """Test the download_transcripts module."""

    def test_transcripts_url_is_set(self):
        """Test that the S3 URL is properly configured."""
        assert download_transcripts.TRANSCRIPTS_URL
        assert download_transcripts.TRANSCRIPTS_URL.startswith("https://")
        assert "lennys_podcast_transcripts_archive.zip" in download_transcripts.TRANSCRIPTS_URL

    def test_min_expected_files(self):
        """Test that minimum expected files is reasonable."""
        assert download_transcripts.MIN_EXPECTED_FILES >= 250
        assert download_transcripts.EXPECTED_EXTENSION == ".txt"

    @patch("download_transcripts.urlopen")
    def test_download_file_success(self, mock_urlopen):
        """Test successful file download."""
        # Mock response
        mock_response = MagicMock()
        mock_response.headers.get.return_value = "1000"  # content-length
        mock_response.read.side_effect = [b"test" * 250, b""]  # 1000 bytes
        mock_response.__enter__ = MagicMock(return_value=mock_response)
        mock_response.__exit__ = MagicMock(return_value=None)
        mock_urlopen.return_value = mock_response

        # Mock file operations
        with patch("builtins.open", mock_open()) as mock_file:
            output_path = Path("/tmp/test.zip")
            download_transcripts.download_file(
                "https://example.com/test.zip", output_path
            )

            # Verify file was written
            mock_file.assert_called_once_with(output_path, "wb")
            handle = mock_file()
            assert handle.write.call_count > 0

    @patch("download_transcripts.urlopen")
    def test_download_file_http_error(self, mock_urlopen):
        """Test download with HTTP error."""
        mock_urlopen.side_effect = HTTPError(
            "https://example.com/test.zip", 404, "Not Found", {}, None
        )

        with pytest.raises(HTTPError):
            download_transcripts.download_file(
                "https://example.com/test.zip", Path("/tmp/test.zip")
            )

    @patch("download_transcripts.urlopen")
    def test_download_file_url_error(self, mock_urlopen):
        """Test download with URL error."""
        mock_urlopen.side_effect = URLError("Connection failed")

        with pytest.raises(URLError):
            download_transcripts.download_file(
                "https://example.com/test.zip", Path("/tmp/test.zip")
            )

    def test_validate_transcripts_directory_not_exists(self):
        """Test validation when directory doesn't exist."""
        result, message = download_transcripts.validate_transcripts(
            Path("/nonexistent/directory")
        )
        assert result is False
        assert "does not exist" in message

    def test_validate_transcripts_too_few_files(self, tmp_path):
        """Test validation with too few transcript files."""
        # Create only a few files
        for i in range(5):
            (tmp_path / f"transcript_{i}.txt").write_text("content")

        result, message = download_transcripts.validate_transcripts(tmp_path)
        assert result is False
        assert "Expected at least" in message

    def test_validate_transcripts_empty_files(self, tmp_path):
        """Test validation with empty files."""
        # Create enough files but make them empty
        for i in range(260):
            (tmp_path / f"transcript_{i}.txt").touch()

        result, message = download_transcripts.validate_transcripts(tmp_path)
        assert result is False
        assert "empty" in message

    def test_validate_transcripts_success(self, tmp_path):
        """Test successful validation."""
        # Create enough valid files
        for i in range(260):
            (tmp_path / f"transcript_{i}.txt").write_text(f"Content {i}")

        result, message = download_transcripts.validate_transcripts(tmp_path)
        assert result is True
        assert "260" in message or "valid" in message.lower()


class TestDownloadTranscriptsIntegration:
    """Integration tests that check actual S3 availability."""

    @pytest.mark.integration
    def test_transcripts_url_is_accessible(self):
        """Test that the S3 URL is actually accessible.
        
        This is an integration test that makes a real HTTP request.
        """
        from urllib.request import Request, urlopen

        url = download_transcripts.TRANSCRIPTS_URL

        try:
            request = Request(url, method="HEAD")
            request.add_header("User-Agent", "neo4j-agent-memory-test/1.0")
            with urlopen(request, timeout=10) as response:
                assert response.status == 200
                size = int(response.headers.get("content-length", 0))
                # Archive should be at least 10MB
                assert size > 10_000_000, f"Archive too small: {size} bytes"
        except Exception as e:
            pytest.fail(f"Failed to access transcripts URL: {e}")

    @pytest.mark.integration
    def test_transcripts_can_be_downloaded(self, tmp_path):
        """Test that transcripts can actually be downloaded and extracted.
        
        This is a full integration test that downloads the real archive.
        Warning: This downloads ~50MB from S3.
        """
        import zipfile

        output_dir = tmp_path / "data"
        zip_path = tmp_path / "transcripts.zip"

        try:
            # Download the file
            download_transcripts.download_file(
                download_transcripts.TRANSCRIPTS_URL, zip_path
            )

            # Verify it's a valid zip
            assert zipfile.is_zipfile(zip_path)

            # Extract it
            num_files = download_transcripts.extract_zip(zip_path, output_dir)
            assert num_files >= download_transcripts.MIN_EXPECTED_FILES

            # Validate the extracted files
            valid, message = download_transcripts.validate_transcripts(output_dir)
            assert valid, f"Validation failed: {message}"

        except Exception as e:
            pytest.fail(f"Download and extraction failed: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
