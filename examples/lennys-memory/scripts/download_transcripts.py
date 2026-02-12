#!/usr/bin/env python3
"""Download and extract Lenny's Podcast transcript files from S3.

This script downloads a zip archive containing 299 podcast transcript files
from an S3 bucket and extracts them to the data directory.

URL: https://s3.us-west-1.amazonaws.com/data.neo4j.com/lennys_podcast_transcripts_archive.zip
"""

import argparse
import hashlib
import sys
import zipfile
from pathlib import Path
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError


# S3 URL for transcript archive
TRANSCRIPTS_URL = "https://s3.us-west-1.amazonaws.com/data.neo4j.com/lennys_podcast_transcripts_archive.zip"

# Expected characteristics of the archive (for validation)
MIN_EXPECTED_FILES = 250  # Should have at least 250 transcript files
EXPECTED_EXTENSION = ".txt"


def print_progress(message: str, end: str = "\n") -> None:
    """Print progress message to stderr."""
    print(message, file=sys.stderr, flush=True, end=end)


def download_file(url: str, output_path: Path, chunk_size: int = 8192) -> None:
    """Download a file from URL with progress indication.

    Args:
        url: URL to download from
        output_path: Path to save the downloaded file
        chunk_size: Size of chunks to download at a time

    Raises:
        HTTPError: If the download fails with an HTTP error
        URLError: If there's a network/URL error
    """
    print_progress(f"Downloading from {url}...")
    
    try:
        request = Request(url)
        request.add_header('User-Agent', 'neo4j-agent-memory/1.0')
        
        with urlopen(request) as response:
            total_size = int(response.headers.get('content-length', 0))
            downloaded = 0
            
            with open(output_path, 'wb') as f:
                while True:
                    chunk = response.read(chunk_size)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    
                    if total_size > 0:
                        percent = (downloaded / total_size) * 100
                        print_progress(f"  Progress: {downloaded}/{total_size} bytes ({percent:.1f}%)", end="\r")
            
            if total_size > 0:
                print_progress("")  # New line after progress
            
            print_progress(f"✓ Downloaded {downloaded} bytes to {output_path}")
            
    except HTTPError as e:
        raise HTTPError(url, e.code, f"HTTP Error {e.code}: {e.reason}", e.hdrs, e.fp) from e
    except URLError as e:
        raise URLError(f"Failed to download from {url}: {e.reason}") from e


def extract_zip(zip_path: Path, extract_to: Path) -> int:
    """Extract a zip file to a directory.

    Args:
        zip_path: Path to the zip file
        extract_to: Directory to extract files to

    Returns:
        Number of files extracted

    Raises:
        zipfile.BadZipFile: If the file is not a valid zip file
    """
    print_progress(f"Extracting {zip_path.name}...")
    
    extract_to.mkdir(parents=True, exist_ok=True)
    
    with zipfile.ZipFile(zip_path, 'r') as zip_ref:
        # Get list of files to extract
        all_files = zip_ref.namelist()
        transcript_files = [f for f in all_files if f.endswith(EXPECTED_EXTENSION)]
        
        print_progress(f"  Found {len(transcript_files)} transcript files in archive")
        
        # Extract all files
        zip_ref.extractall(extract_to)
        
        print_progress(f"✓ Extracted {len(all_files)} files to {extract_to}")
        
        return len(transcript_files)


def validate_transcripts(data_dir: Path) -> tuple[bool, str]:
    """Validate that transcript files were extracted correctly.

    Args:
        data_dir: Directory containing transcript files

    Returns:
        Tuple of (success, message)
    """
    if not data_dir.exists():
        return False, f"Data directory does not exist: {data_dir}"
    
    transcript_files = list(data_dir.glob(f"*{EXPECTED_EXTENSION}"))
    
    if len(transcript_files) < MIN_EXPECTED_FILES:
        return False, f"Expected at least {MIN_EXPECTED_FILES} transcript files, found {len(transcript_files)}"
    
    # Check that files are not empty
    empty_files = [f for f in transcript_files if f.stat().st_size == 0]
    if empty_files:
        return False, f"Found {len(empty_files)} empty transcript files"
    
    return True, f"Found {len(transcript_files)} valid transcript files"


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description="Download Lenny's Podcast transcript files from S3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download to default location (../data)
  python download_transcripts.py

  # Download to custom directory
  python download_transcripts.py --output-dir /path/to/data

  # Force re-download even if files exist
  python download_transcripts.py --force

  # Check if transcripts are available without downloading
  python download_transcripts.py --check-only
        """,
    )
    
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(__file__).parent.parent / "data",
        help="Directory to extract transcript files (default: ../data)",
    )
    
    parser.add_argument(
        "--url",
        type=str,
        default=TRANSCRIPTS_URL,
        help=f"URL to download transcripts from (default: {TRANSCRIPTS_URL})",
    )
    
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files already exist",
    )
    
    parser.add_argument(
        "--check-only",
        action="store_true",
        help="Check if transcripts are available without downloading",
    )
    
    parser.add_argument(
        "--keep-zip",
        action="store_true",
        help="Keep the downloaded zip file after extraction",
    )
    
    args = parser.parse_args()
    
    # Check-only mode: verify URL is accessible
    if args.check_only:
        print_progress("Checking if transcripts are available...")
        try:
            request = Request(args.url, method='HEAD')
            request.add_header('User-Agent', 'neo4j-agent-memory/1.0')
            with urlopen(request) as response:
                size = int(response.headers.get('content-length', 0))
                print_progress(f"✓ Transcripts archive is available ({size} bytes)")
                print_progress(f"  URL: {args.url}")
                return 0
        except (HTTPError, URLError) as e:
            print_progress(f"✗ Failed to access transcripts archive: {e}")
            return 1
    
    # Check if transcripts already exist
    if not args.force:
        valid, message = validate_transcripts(args.output_dir)
        if valid:
            print_progress(f"✓ Transcripts already exist: {message}")
            print_progress("  Use --force to re-download")
            return 0
        else:
            print_progress(f"Transcripts not found or invalid: {message}")
    
    # Create temp directory for download
    temp_dir = Path(__file__).parent.parent / "tmp"
    temp_dir.mkdir(parents=True, exist_ok=True)
    
    zip_path = temp_dir / "transcripts.zip"
    
    try:
        # Download the archive
        download_file(args.url, zip_path)
        
        # Extract the archive
        num_transcripts = extract_zip(zip_path, args.output_dir)
        
        # Validate extraction
        valid, message = validate_transcripts(args.output_dir)
        if not valid:
            print_progress(f"✗ Validation failed: {message}")
            return 1
        
        print_progress(f"✓ {message}")
        print_progress(f"\nTranscripts ready in: {args.output_dir.resolve()}")
        
        return 0
        
    except (HTTPError, URLError, zipfile.BadZipFile) as e:
        print_progress(f"✗ Error: {e}")
        return 1
        
    finally:
        # Cleanup: remove zip file unless --keep-zip
        if not args.keep_zip and zip_path.exists():
            zip_path.unlink()
            print_progress(f"Cleaned up temporary zip file")


if __name__ == "__main__":
    sys.exit(main())
