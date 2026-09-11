#!/usr/bin/env python3
"""Load Lenny's Podcast transcripts into neo4j-agent-memory.

Features:
- Real-time progress bars and ETA
- Concurrent transcript processing
- Resume capability (skip already loaded transcripts)
- Detailed statistics and timing
- Retry logic for transient failures
- Buffered warnings/logs to prevent progress bar disruption
"""

# ============================================================================
# IMPORTANT: Warning suppression must happen BEFORE any other imports
# to catch warnings triggered during module initialization.
#
# These filters are deliberately NARROW. Suppressing whole categories
# process-wide (as this script used to do for UserWarning, FutureWarning and
# DeprecationWarning) also hides the library's own migration notices -- e.g. the
# `client.graph.execute_read` removal warning -- in the script that runs most
# often. Keep the message patterns, and scope category filters by module.
# ============================================================================
import os
import warnings

warnings.filterwarnings("ignore", message=".*resume_download.*")
warnings.filterwarnings("ignore", message=".*byte fallback.*")
warnings.filterwarnings("ignore", message=".*truncate to max_length.*")
warnings.filterwarnings("ignore", message=".*no predefined maximum length.*")
warnings.filterwarnings("ignore", message=".*schema.*shadows.*")
for _noisy in ("transformers", "huggingface_hub", "spacy", "thinc", "torch"):
    warnings.filterwarnings("ignore", category=UserWarning, module=_noisy)
    warnings.filterwarnings("ignore", category=FutureWarning, module=_noisy)

# Disable huggingface progress bars and logging noise
os.environ["HF_HUB_DISABLE_PROGRESS_BARS"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["TRANSFORMERS_VERBOSITY"] = "error"

import argparse  # noqa: E402
import asyncio  # noqa: E402
import io  # noqa: E402
import logging  # noqa: E402
import re  # noqa: E402
import sys  # noqa: E402
import time  # noqa: E402
import unicodedata  # noqa: E402
from collections.abc import Callable  # noqa: E402
from contextlib import contextmanager  # noqa: E402
from dataclasses import dataclass, field  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))

from _common import (  # noqa: E402
    Colors,
    add_model_args,
    add_neo4j_args,
    build_memory_settings,
    color,
    format_duration,
    load_backend_env,
)

from neo4j_agent_memory import MemoryClient  # noqa: E402
from neo4j_agent_memory.config.settings import (  # noqa: E402
    ExtractionConfig,
    ExtractorType,
)

load_backend_env()

DEFAULT_DATA_DIR = Path(__file__).parent.parent / "data"
SAMPLE_DATA_DIR = DEFAULT_DATA_DIR / "samples"


class Cursor:
    """ANSI cursor-control sequences used by the progress bar below.

    Colors themselves come from ``scripts/_common.py`` -- one copy for all five
    scripts (they used to carry four slightly diverged copies).
    """

    CLEAR_LINE = "\033[2K"
    CURSOR_UP = "\033[1A"
    SAVE_CURSOR = "\033[s"
    RESTORE_CURSOR = "\033[u"


class BufferedLogHandler(logging.Handler):
    """A logging handler that buffers messages to avoid disrupting progress bars."""

    def __init__(self):
        super().__init__()
        self.buffer: list[logging.LogRecord] = []
        self._buffering = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._buffering:
            self.buffer.append(record)
        else:
            # Print immediately if not buffering
            msg = self.format(record)
            print(msg, file=sys.stderr)

    def start_buffering(self) -> None:
        self._buffering = True

    def stop_buffering(self) -> None:
        self._buffering = False

    def flush_buffer(self, clear_line: bool = True) -> list[str]:
        """Flush buffered messages and return them."""
        messages = []
        for record in self.buffer:
            messages.append(self.format(record))
        self.buffer.clear()
        return messages


# Global buffered handler for capturing logs during progress
_buffered_handler = BufferedLogHandler()
_buffered_handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))


def setup_logging(verbose: bool = False) -> None:
    """Configure logging to use buffered handler."""
    # Set up root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(logging.WARNING if not verbose else logging.INFO)

    # Remove existing handlers
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    # Add our buffered handler
    root_logger.addHandler(_buffered_handler)

    # Also suppress specific noisy loggers
    logging.getLogger("neo4j").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


@contextmanager
def suppress_output_during_progress():
    """Context manager to buffer logs and warnings during progress bar updates."""
    # Start buffering logs
    _buffered_handler.start_buffering()

    # Capture warnings
    old_showwarning = warnings.showwarning
    captured_warnings: list[str] = []

    def capture_warning(message, category, filename, lineno, file=None, line=None):
        captured_warnings.append(f"Warning: {message}")

    warnings.showwarning = capture_warning

    # Also redirect stderr temporarily to catch any direct prints
    old_stderr = sys.stderr
    sys.stderr = io.StringIO()

    try:
        yield
    finally:
        # Restore stderr (its captured content is intentionally discarded --
        # the progress bar owns the terminal while this context is active)
        sys.stderr = old_stderr

        # Stop buffering
        _buffered_handler.stop_buffering()

        # Restore warnings
        warnings.showwarning = old_showwarning


def flush_buffered_output(progress_bar_active: bool = False) -> list[str]:
    """Flush any buffered output and return messages for later display.

    Args:
        progress_bar_active: If True, messages are returned for display after
                           progress bar completes. If False, messages are
                           printed immediately.

    Returns:
        List of buffered messages if progress_bar_active, empty list otherwise.
    """
    messages = _buffered_handler.flush_buffer()
    if not progress_bar_active and messages:
        for msg in messages:
            print(msg, file=sys.stderr)
        return []
    return messages


# ``color`` / ``format_duration`` come from scripts/_common.py.


def format_rate(count: int, seconds: float) -> str:
    """Format rate as items per second or per minute."""
    if seconds == 0:
        return "N/A"
    rate = count / seconds
    if rate >= 1:
        return f"{rate:.1f}/s"
    else:
        return f"{rate * 60:.1f}/min"


class ProgressBar:
    """A simple progress bar for terminal output.

    Handles buffered warnings and logs to prevent disruption.
    """

    def __init__(
        self,
        total: int,
        prefix: str = "",
        width: int = 30,
        show_eta: bool = True,
    ):
        self.total = total
        self.prefix = prefix
        self.width = width
        self.show_eta = show_eta
        self.current = 0
        self.start_time = time.time()
        self._last_update = 0
        self._buffered_messages: list[str] = []
        self._active = False
        self._status: str = ""  # Current operation status

    def __enter__(self):
        """Start progress bar and begin buffering output."""
        self._active = True
        _buffered_handler.start_buffering()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Stop progress bar and flush buffered output."""
        self._active = False
        _buffered_handler.stop_buffering()
        # Collect any buffered messages
        self._buffered_messages.extend(_buffered_handler.flush_buffer())
        return False

    def set_status(self, status: str) -> None:
        """Set the current operation status."""
        self._status = status

    def update(self, current: int | None = None, suffix: str = "") -> None:
        """Update the progress bar."""
        if current is not None:
            self.current = current
        else:
            self.current += 1

        # Throttle updates to avoid flickering (max 10 updates/sec)
        now = time.time()
        if now - self._last_update < 0.1 and self.current < self.total:
            return
        self._last_update = now

        elapsed = now - self.start_time
        percent = self.current / self.total if self.total > 0 else 0
        filled = int(self.width * percent)
        bar = "█" * filled + "░" * (self.width - filled)

        # Calculate ETA
        eta_str = ""
        if self.show_eta and self.current > 0 and self.current < self.total:
            rate = self.current / elapsed
            remaining = (self.total - self.current) / rate if rate > 0 else 0
            eta_str = f" ETA: {format_duration(remaining)}"

        # Build the line
        line = f"\r{self.prefix} [{bar}] {self.current}/{self.total} ({percent:.0%}){eta_str}"
        if suffix:
            line += f" {suffix}"
        if self._status:
            line += f" {color(f'| {self._status}', Colors.DIM)}"

        # Clear to end of line and print
        sys.stdout.write(f"{line}\033[K")
        sys.stdout.flush()

    def finish(self, message: str = "") -> None:
        """Complete the progress bar."""
        self._status = ""  # Clear status
        elapsed = time.time() - self.start_time
        self.update(self.total)
        rate = format_rate(self.total, elapsed)
        final_msg = f" [{format_duration(elapsed)}, {rate}]"
        if message:
            final_msg += f" {message}"
        print(final_msg)

        # Print any buffered messages after the progress bar is done
        if self._buffered_messages:
            print()  # Add spacing
            for msg in self._buffered_messages:
                print(f"  {color('⚠', Colors.YELLOW)} {msg}")
            self._buffered_messages.clear()

    def add_warning(self, message: str) -> None:
        """Add a warning message to be displayed after progress completes."""
        self._buffered_messages.append(message)


@dataclass
class SpeakerTurn:
    """A single speaker turn in a transcript."""

    speaker: str
    timestamp: str
    content: str
    episode_guest: str


@dataclass
class LoadStats:
    """Statistics for a loading operation."""

    files_loaded: int = 0
    files_skipped: int = 0
    files_failed: int = 0
    turns_loaded: int = 0
    entities_extracted: int = 0
    speakers: set = field(default_factory=set)
    errors: list = field(default_factory=list)
    start_time: float = field(default_factory=time.time)

    @property
    def elapsed(self) -> float:
        return time.time() - self.start_time

    @property
    def turns_per_second(self) -> float:
        return self.turns_loaded / self.elapsed if self.elapsed > 0 else 0


def parse_transcript(file_path: Path) -> list[SpeakerTurn]:
    """Parse a transcript file into speaker turns.

    Format:
        Speaker Name (HH:MM:SS):
        [Content...]

        (HH:MM:SS):
        [Content continuation from same speaker...]
    """
    content = file_path.read_text(encoding="utf-8")
    guest_name = file_path.stem  # Filename without .txt

    # Pattern matches "Speaker Name (HH:MM:SS):" or just "(HH:MM:SS):"
    # The speaker name is optional (continuation of previous speaker).
    # ``[^\W\d_]`` is a Unicode-aware "letter": the old ``[A-Za-z]`` class made
    # the whole line fail to match for a speaker such as "Renée Delacroix", so
    # the turn marker was swallowed as content and attributed to the previous
    # speaker. Names with diacritics are common in this corpus.
    pattern = r"^(?:([^\W\d_][^()]*?)\s+)?\((\d{2}:\d{2}:\d{2})\):$"

    turns: list[SpeakerTurn] = []
    current_speaker: str | None = None
    current_timestamp: str | None = None
    current_content: list[str] = []

    for line in content.split("\n"):
        line = line.strip()
        match = re.match(pattern, line)

        if match:
            # Save previous turn if exists
            if current_speaker and current_content:
                turns.append(
                    SpeakerTurn(
                        speaker=current_speaker,
                        timestamp=current_timestamp or "00:00:00",
                        content="\n".join(current_content).strip(),
                        episode_guest=guest_name,
                    )
                )

            # Start new turn
            speaker_name = match.group(1)
            if speaker_name:
                current_speaker = speaker_name.strip()
            # If no speaker name, it's a continuation of previous speaker
            current_timestamp = match.group(2)
            current_content = []
        elif line:
            # Content line
            current_content.append(line)

    # Don't forget last turn
    if current_speaker and current_content:
        turns.append(
            SpeakerTurn(
                speaker=current_speaker,
                timestamp=current_timestamp or "00:00:00",
                content="\n".join(current_content).strip(),
                episode_guest=guest_name,
            )
        )

    return turns


def slugify(name: str) -> str:
    """Convert a guest name to the session-id slug.

    Must stay in lockstep with ``_guest_to_session_id`` in
    ``backend/src/agent/tools.py``: the loader names sessions with this rule and
    the agent's tools look them up by it. Unicode is folded to ASCII first, so
    "Tobi Lütke" becomes "tobi-lutke" and not "tobi-l-tke" -- without the fold
    the two implementations disagree and every guest with a diacritic becomes
    unreachable from the agent. ``tests/examples/test_lennys_memory_example.py``
    asserts the two agree.
    """
    normalized = unicodedata.normalize("NFD", name)
    ascii_name = normalized.encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_name.lower()).strip("-")


async def check_session_exists(memory: MemoryClient, session_id: str) -> bool:
    """Check if a session already exists in the database."""
    try:
        conversation = await memory.short_term.get_conversation(session_id)
        return bool(conversation and conversation.messages)
    except Exception:
        return False


async def check_sessions_exist_batch(memory: MemoryClient, session_ids: list[str]) -> set[str]:
    """Check which sessions already exist in the database (batch operation).

    This is much more efficient than checking sessions one at a time,
    especially when there are many sessions to check.

    Args:
        memory: MemoryClient instance
        session_ids: List of session IDs to check

    Returns:
        Set of session IDs that already exist
    """
    if not session_ids:
        return set()

    query = """
    UNWIND $session_ids AS sid
    OPTIONAL MATCH (c:Conversation {session_id: sid})
    WITH sid, c IS NOT NULL AS exists
    WHERE exists
    RETURN sid
    """
    try:
        results = await memory.query.cypher(query, {"session_ids": session_ids})
        return {row["sid"] for row in results}
    except Exception:
        # Fallback to individual checks if batch query fails
        existing = set()
        for sid in session_ids:
            if await check_session_exists(memory, sid):
                existing.add(sid)
        return existing


async def setup_database_schema(memory: MemoryClient, verbose: bool = False) -> None:
    """Ensure database indexes and constraints exist for optimal performance.

    This creates:
    - Unique constraints on Conversation.id, Message.id, etc.
    - Index on Conversation.session_id for fast lookups
    - Index on Message.timestamp for ordering
    - Vector indexes for semantic search (if Neo4j 5.11+)

    Args:
        memory: MemoryClient instance
        verbose: Whether to print status messages
    """
    # ``connect()`` already ran setup_all(); this makes the step explicit and is
    # idempotent. ``client.schema`` is the public accessor (bolt only).
    await memory.schema.setup_all()


async def load_transcript(
    memory: MemoryClient,
    file_path: Path,
    extract_entities: bool = True,
    generate_embeddings: bool = True,
    batch_size: int = 50,
    max_retries: int = 3,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Load a single transcript into memory.

    Args:
        memory: MemoryClient instance
        file_path: Path to transcript file
        extract_entities: Whether to extract entities from messages
        generate_embeddings: Whether to generate embeddings for semantic search
        batch_size: Number of messages to process in each batch
        max_retries: Maximum retry attempts for transient failures
        on_progress: Optional callback for progress updates (current, total)

    Returns:
        Stats dict with turns loaded, speakers found, and any errors
    """
    turns = parse_transcript(file_path)
    guest_name = file_path.stem

    # Create a session_id for this episode
    session_id = f"lenny-podcast-{slugify(guest_name)}"

    stats = {"turns": 0, "speakers": set(), "errors": []}

    # Prepare messages
    messages = []
    for i, turn in enumerate(turns):
        role = "user" if turn.speaker.lower() == "lenny" else "assistant"
        metadata = {
            "episode_guest": turn.episode_guest,
            "speaker": turn.speaker,
            "timestamp": turn.timestamp,
            "source": "lenny_podcast",
            "turn_index": i,
        }
        messages.append(
            {
                "role": role,
                "content": turn.content,
                "metadata": metadata,
            }
        )
        stats["speakers"].add(turn.speaker)

    # Try batch loading with retries
    for attempt in range(max_retries):
        try:
            await memory.short_term.add_messages_batch(
                session_id=session_id,
                messages=messages,
                batch_size=batch_size,
                generate_embeddings=generate_embeddings,
                extract_entities=extract_entities,
                on_progress=on_progress,
            )
            stats["turns"] = len(messages)
            return stats
        except Exception as e:
            if attempt < max_retries - 1:
                wait_time = 2**attempt  # Exponential backoff
                stats["errors"].append(
                    f"Attempt {attempt + 1} failed: {e}, retrying in {wait_time}s"
                )
                await asyncio.sleep(wait_time)
            else:
                stats["errors"].append(f"All {max_retries} attempts failed: {e}")
                raise

    return stats


def resolve_data_dir(explicit: Path | None) -> Path:
    """Pick the transcript directory.

    Precedence: an explicit ``--data-dir``, then ``data/`` when it contains
    ``.txt`` transcripts, then the checked-in ``data/samples/`` fixtures. The
    real corpus is not redistributable (see ``data/README.md``), so a clean
    clone must still be able to run the pipeline end to end.
    """
    if explicit is not None:
        return explicit
    if DEFAULT_DATA_DIR.exists() and any(DEFAULT_DATA_DIR.glob("*.txt")):
        return DEFAULT_DATA_DIR
    if SAMPLE_DATA_DIR.exists() and any(SAMPLE_DATA_DIR.glob("*.txt")):
        print(
            color(
                f"No transcripts in {DEFAULT_DATA_DIR}; using the synthetic samples "
                f"in {SAMPLE_DATA_DIR}.",
                Colors.YELLOW,
            )
        )
        return SAMPLE_DATA_DIR
    return DEFAULT_DATA_DIR


async def generate_message_embeddings(
    memory: MemoryClient,
    data_dir: Path,
    sample_size: int | None = None,
    batch_size: int = 100,
) -> dict[str, int]:
    """Fill in message embeddings for already loaded transcripts.

    The counterpart to ``--no-embeddings``: without it, a fast load left every
    vector search in the app returning nothing with no way back but a reload.
    Uses ``short_term.generate_embeddings_batch()``.
    """
    files = sorted(data_dir.glob("*.txt"))
    if sample_size:
        files = files[:sample_size]

    totals = {"sessions": 0, "embedded": 0}
    for file_path in files:
        session_id = f"lenny-podcast-{slugify(file_path.stem)}"
        embedded = await memory.short_term.generate_embeddings_batch(
            session_id,
            batch_size=batch_size,
        )
        totals["sessions"] += 1
        totals["embedded"] += embedded
        print(f"  {file_path.stem}: {embedded} messages embedded")

    print()
    print(
        color(
            f"Embedded {totals['embedded']} messages across {totals['sessions']} sessions.",
            Colors.GREEN,
        )
    )
    return totals


async def extract_entities_from_loaded_sessions(
    memory: MemoryClient,
    data_dir: Path,
    sample_size: int | None = None,
    batch_size: int = 100,
    concurrency: int = 3,
) -> None:
    """Extract entities from already loaded transcripts.

    This is useful for running entity extraction separately after the initial
    data load (which can be done faster with --no-entities).

    Args:
        memory: MemoryClient instance
        data_dir: Directory containing transcript files (used to determine session IDs)
        sample_size: Optional limit on number of sessions to process
        batch_size: Number of messages per batch
        concurrency: Number of sessions to process concurrently
    """
    files = sorted(data_dir.glob("*.txt"))

    if not files:
        print(f"No .txt files found in {data_dir}")
        return

    if sample_size:
        files = files[:sample_size]

    # Get session IDs for all files
    session_ids = [f"lenny-podcast-{slugify(f.stem)}" for f in files]

    # Check which sessions exist
    print("Checking for loaded sessions...", end=" ", flush=True)
    existing_sessions = await check_sessions_exist_batch(memory, session_ids)
    print(color("Done!", Colors.GREEN))

    sessions_to_process = [sid for sid in session_ids if sid in existing_sessions]

    if not sessions_to_process:
        print(color("No loaded sessions found to extract entities from.", Colors.YELLOW))
        return

    print(f"\nExtracting entities from {len(sessions_to_process)} session(s)...")
    print()

    total_processed = 0
    total_entities = 0
    start_time = time.time()

    # Create a semaphore for concurrency control
    semaphore = asyncio.Semaphore(concurrency)

    async def process_session(session_id: str, index: int) -> dict:
        """Process a single session with entity extraction."""
        async with semaphore:
            try:
                result = await memory.short_term.extract_entities_from_session(
                    session_id,
                    batch_size=batch_size,
                    skip_existing=True,
                )
                return {
                    "session_id": session_id,
                    "messages": result.get("messages_processed", 0),
                    "entities": result.get("entities_extracted", 0),
                    "error": None,
                }
            except Exception as e:
                return {
                    "session_id": session_id,
                    "messages": 0,
                    "entities": 0,
                    "error": str(e),
                }

    # Process all sessions concurrently with progress bar
    progress_bar = ProgressBar(len(sessions_to_process), prefix="Extracting")

    with progress_bar:
        tasks = [process_session(sid, i) for i, sid in enumerate(sessions_to_process, 1)]

        for i, coro in enumerate(asyncio.as_completed(tasks), 1):
            result = await coro
            total_processed += result["messages"]
            total_entities += result["entities"]

            if result["error"]:
                progress_bar.add_warning(f"{result['session_id']}: {result['error']}")

            progress_bar.update(i)

    progress_bar.finish(color("Done!", Colors.GREEN))

    # Print summary
    elapsed = time.time() - start_time
    print()
    print(color("═" * 60, Colors.CYAN))
    print(color("  Entity Extraction Complete!", Colors.BOLD + Colors.GREEN))
    print(color("═" * 60, Colors.CYAN))
    print()
    print(f"  {color('Sessions processed:', Colors.DIM)} {len(sessions_to_process)}")
    print(f"  {color('Messages processed:', Colors.DIM)} {total_processed:,}")
    print(f"  {color('Entities extracted:', Colors.DIM)} {total_entities:,}")
    print(f"  {color('Elapsed time:', Colors.DIM)} {format_duration(elapsed)}")
    print(f"  {color('Throughput:', Colors.DIM)} {format_rate(total_processed, elapsed)}")
    print()


def print_dry_run_plan(files: list[Path], sample_size: int | None = None) -> None:
    """Print what a load would do, without opening a database connection.

    A dry run is a question about the files on disk, so it must not require
    Neo4j -- the script used to connect first and fail before printing anything.
    """
    planned = files[:sample_size] if sample_size else files
    print(f"\n{color('DRY RUN', Colors.YELLOW)} - Would load {len(planned)} transcript(s):")
    total_turns = 0
    for file_path in planned:
        turns = parse_transcript(file_path)
        total_turns += len(turns)
        print(f"  \u2022 {file_path.name}: {len(turns)} turns")
    print(f"\nTotal: {len(planned)} files, {total_turns} turns")


async def load_all_transcripts(
    memory: MemoryClient,
    data_dir: Path,
    sample_size: int | None = None,
    extract_entities: bool = True,
    generate_embeddings: bool = True,
    skip_existing: bool = False,
    batch_size: int = 100,
    concurrency: int = 3,
    dry_run: bool = False,
) -> LoadStats:
    """Load all transcripts from the data directory.

    Args:
        memory: MemoryClient instance
        data_dir: Directory containing transcript files
        sample_size: Optional limit on number of files to load
        extract_entities: Whether to extract entities
        generate_embeddings: Whether to generate embeddings
        skip_existing: Skip transcripts that are already loaded
        batch_size: Batch size for message loading
        concurrency: Number of transcripts to process concurrently
        dry_run: If True, only show what would be loaded without loading

    Returns:
        LoadStats with detailed statistics
    """
    files = sorted(data_dir.glob("*.txt"))

    if not files:
        print(f"No .txt files found in {data_dir}")
        return LoadStats()

    if sample_size:
        files = files[:sample_size]

    stats = LoadStats()

    # Check which files to skip if resume mode
    files_to_load = []
    if skip_existing:
        print("Checking for existing transcripts...", end=" ", flush=True)
        # Build list of session IDs to check
        session_ids = [f"lenny-podcast-{slugify(f.stem)}" for f in files]
        file_by_session = {f"lenny-podcast-{slugify(f.stem)}": f for f in files}

        # Batch check all sessions at once (much faster than individual checks)
        existing_sessions = await check_sessions_exist_batch(memory, session_ids)

        # Filter files
        for session_id in session_ids:
            if session_id in existing_sessions:
                stats.files_skipped += 1
            else:
                files_to_load.append(file_by_session[session_id])

        print(color("Done!", Colors.GREEN))
        if stats.files_skipped > 0:
            print(
                f"  {color(f'Skipping {stats.files_skipped} already loaded', Colors.YELLOW)}, "
                f"{len(files_to_load)} remaining"
            )
    else:
        files_to_load = files

    if not files_to_load:
        print(color("All transcripts already loaded!", Colors.GREEN))
        return stats

    # Dry run - just show what would be loaded
    if dry_run:
        print(
            f"\n{color('DRY RUN', Colors.YELLOW)} - Would load {len(files_to_load)} transcript(s):"
        )
        total_turns = 0
        for file_path in files_to_load:
            turns = parse_transcript(file_path)
            total_turns += len(turns)
            print(f"  • {file_path.name}: {len(turns)} turns")
        print(f"\nTotal: {len(files_to_load)} files, {total_turns} turns")
        return stats

    print(f"\nLoading {len(files_to_load)} transcript(s)...")
    print()

    # Calculate total turns for overall progress
    total_turns = 0
    file_turns: dict[Path, int] = {}
    for file_path in files_to_load:
        turns = parse_transcript(file_path)
        file_turns[file_path] = len(turns)
        total_turns += len(turns)

    # Build processing pipeline description
    pipeline_steps = []
    if generate_embeddings:
        pipeline_steps.append("embeddings")
    if extract_entities:
        pipeline_steps.append("entities")
    pipeline_desc = " + ".join(pipeline_steps) if pipeline_steps else "storage only"

    # Overall progress bar
    overall_bar = ProgressBar(total_turns, prefix="Loading ")
    turns_loaded = 0

    async def load_one(file_path: Path, file_index: int) -> dict | None:
        """Load a single transcript with progress reporting."""
        nonlocal turns_loaded

        file_name = file_path.name

        def progress_callback(current: int, total: int) -> None:
            nonlocal turns_loaded
            # Determine current phase based on progress
            phase = "storing"
            if generate_embeddings and current < total:
                phase = "generating embeddings"
            if extract_entities and current >= total * 0.5:
                phase = "extracting entities"
            if current >= total:
                phase = "finalizing"

            overall_bar.set_status(phase)
            # Update overall progress
            overall_bar.update(
                turns_loaded + current,
                suffix=color(f"[{file_index}/{len(files_to_load)}] {file_name}", Colors.DIM),
            )

        try:
            # Show initial status
            overall_bar.set_status("parsing transcript")
            overall_bar.update(
                turns_loaded,
                suffix=color(f"[{file_index}/{len(files_to_load)}] {file_name}", Colors.DIM),
            )

            result = await load_transcript(
                memory,
                file_path,
                extract_entities=extract_entities,
                generate_embeddings=generate_embeddings,
                batch_size=batch_size,
                on_progress=progress_callback,
            )
            turns_loaded += result["turns"]
            # Capture any errors from retries as warnings
            if result.get("errors"):
                for err in result["errors"]:
                    overall_bar.add_warning(f"{file_name}: {err}")
            return result
        except Exception as e:
            stats.errors.append(f"{file_name}: {e}")
            overall_bar.add_warning(f"{file_name}: {e}")
            return None

    # Show pipeline info
    print(f"  {color('Pipeline:', Colors.DIM)} {pipeline_desc}")
    if concurrency > 1:
        print(f"  {color('Concurrency:', Colors.DIM)} {concurrency} transcripts in parallel")
    print()

    # Process files with buffered output to prevent log disruption
    # Use context manager to buffer logs during progress bar updates
    if concurrency <= 1:
        # Sequential processing (original behavior)
        with overall_bar:
            for i, file_path in enumerate(files_to_load, 1):
                result = await load_one(file_path, i)
                if result:
                    stats.files_loaded += 1
                    stats.turns_loaded += result["turns"]
                    stats.speakers.update(result["speakers"])
                else:
                    stats.files_failed += 1
    else:
        # Concurrent processing with semaphore
        semaphore = asyncio.Semaphore(concurrency)

        async def load_one_with_semaphore(file_path: Path, file_index: int) -> dict | None:
            async with semaphore:
                return await load_one(file_path, file_index)

        with overall_bar:
            # Create tasks for all files
            tasks = [
                load_one_with_semaphore(file_path, i)
                for i, file_path in enumerate(files_to_load, 1)
            ]

            # Process as they complete
            for coro in asyncio.as_completed(tasks):
                result = await coro
                if result:
                    stats.files_loaded += 1
                    stats.turns_loaded += result["turns"]
                    stats.speakers.update(result["speakers"])
                else:
                    stats.files_failed += 1

    overall_bar.finish(color("Done!", Colors.GREEN))

    return stats


async def main():
    parser = argparse.ArgumentParser(
        description="Load Lenny's Podcast transcripts into neo4j-agent-memory",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Fast initial load (skip entities, extract later)
  %(prog)s --no-entities

  # Then extract entities from loaded transcripts
  %(prog)s --extract-entities-only

  # Load 5 sample transcripts for testing
  %(prog)s --sample 5

  # Resume loading (skip already loaded transcripts)
  %(prog)s --resume

  # Preview what would be loaded
  %(prog)s --dry-run

  # Maximum speed: no entities, no embeddings, high concurrency
  %(prog)s --no-entities --no-embeddings --concurrency 5

  # Verbose output with larger batches
  %(prog)s -v --batch-size 200

Performance Tips:
  - Use --no-entities for initial load, then --extract-entities-only
  - Default batch size (100) and concurrency (3) are optimized for most cases
  - Database indexes are automatically created on first run
""",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=None,
        help=(
            "Directory containing transcript .txt files. Defaults to data/ when "
            "it holds transcripts, otherwise data/samples/ (see data/README.md)."
        ),
    )
    parser.add_argument(
        "--sample",
        type=int,
        metavar="N",
        help="Load only N transcripts (for testing)",
    )
    parser.add_argument(
        "--no-entities",
        action="store_true",
        help="Skip entity extraction (faster loading)",
    )
    parser.add_argument(
        "--no-embeddings",
        action="store_true",
        help="Skip embedding generation (faster loading)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip transcripts that are already loaded",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=100,
        metavar="N",
        help="Number of messages per batch (default: 100)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=3,
        metavar="N",
        help="Number of transcripts to load concurrently (default: 3)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be loaded without loading",
    )
    parser.add_argument(
        "--skip-schema-setup",
        action="store_true",
        help="Skip database schema/index setup (use if already configured)",
    )
    parser.add_argument(
        "--extract-entities-only",
        action="store_true",
        help="Only extract entities from already loaded transcripts (run after initial load)",
    )
    parser.add_argument(
        "--embeddings-only",
        action="store_true",
        help=(
            "Only generate message embeddings for already loaded transcripts. "
            "The counterpart to --no-embeddings: without this, a load run with "
            "--no-embeddings could only be fixed by reloading everything."
        ),
    )
    parser.add_argument(
        "--repair-links",
        action="store_true",
        help=(
            "Rebuild FIRST_MESSAGE/NEXT_MESSAGE chains for conversations loaded "
            "before sequential linking existed (short_term.migrate_message_links)"
        ),
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show detailed progress",
    )
    add_neo4j_args(parser)
    add_model_args(parser)

    args = parser.parse_args()

    # Set up logging to prevent disruption of progress bars
    setup_logging(verbose=args.verbose)

    # Resolve the data directory. The real 299-episode corpus is not
    # redistributable, so a fresh clone only has the synthetic fixtures in
    # data/samples/ -- fall back to those rather than exiting. See data/README.md.
    args.data_dir = resolve_data_dir(args.data_dir)
    files = sorted(args.data_dir.glob("*.txt"))
    if not files:
        print(color(f"Error: No .txt files found in {args.data_dir}", Colors.RED))
        print("See examples/lennys-memory/data/README.md for the expected format.")
        sys.exit(1)

    print()
    print(color("═" * 60, Colors.CYAN))
    print(color("  Lenny's Podcast Transcript Loader", Colors.BOLD))
    print(color("═" * 60, Colors.CYAN))
    print()
    print(f"  {color('Data directory:', Colors.DIM)} {args.data_dir}")
    print(f"  {color('Available files:', Colors.DIM)} {len(files)}")
    print(f"  {color('Neo4j URI:', Colors.DIM)} {args.neo4j_uri}")
    print(f"  {color('Sample size:', Colors.DIM)} {args.sample or 'all'}")
    print(
        f"  {color('Entity extraction:', Colors.DIM)} {color('enabled', Colors.GREEN) if not args.no_entities else color('disabled', Colors.YELLOW)}"
    )
    print(
        f"  {color('Embeddings:', Colors.DIM)} {color('enabled', Colors.GREEN) if not args.no_embeddings else color('disabled', Colors.YELLOW)}"
    )
    print(
        f"  {color('Resume mode:', Colors.DIM)} {color('enabled', Colors.GREEN) if args.resume else color('disabled', Colors.DIM)}"
    )
    print(f"  {color('Batch size:', Colors.DIM)} {args.batch_size}")
    print(f"  {color('Concurrency:', Colors.DIM)} {args.concurrency}")
    if args.dry_run:
        print(f"  {color('Mode:', Colors.DIM)} {color('DRY RUN', Colors.YELLOW)}")
    if args.extract_entities_only:
        print(f"  {color('Mode:', Colors.DIM)} {color('EXTRACT ENTITIES ONLY', Colors.BLUE)}")
    print()

    # Configure extraction settings for podcast transcripts
    extraction_config = ExtractionConfig(
        extractor_type=ExtractorType.PIPELINE,
        enable_spacy=True,
        enable_gliner=True,
        enable_llm_fallback=False,  # Don't use LLM for faster extraction
        gliner_schema="podcast",  # Use podcast-optimized schema
        gliner_threshold=0.4,  # Lower threshold to capture more entities
    )

    # The shared builder resolves EMBEDDING_MODEL through from_provider(), so
    # the loader writes vectors in the same space the backend queries with. The
    # loader used to build its own MemorySettings with no embedding field at all
    # and silently fall back to the default OpenAI embedder.
    settings = build_memory_settings(args, extraction=extraction_config, quiet=True)

    # A dry run without --resume needs no database: answer from the files and
    # exit before connecting (the resume check is the only DB-dependent part).
    if args.dry_run and not args.resume:
        print_dry_run_plan(files, args.sample)
        return

    if not args.dry_run:
        print("Connecting to Neo4j...", end=" ", flush=True)

    try:
        # Suppress any warnings during connection
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            memory_client = MemoryClient(settings)

        async with memory_client as memory:
            if not args.dry_run:
                print(color("Connected!", Colors.GREEN))

            # Set up database schema (indexes and constraints) for optimal performance
            if not args.skip_schema_setup and not args.dry_run:
                print("Setting up database indexes...", end=" ", flush=True)
                await setup_database_schema(memory, verbose=args.verbose)
                print(color("Done!", Colors.GREEN))

            # Rebuild sequential message links (one-time migration helper)
            if args.repair_links:
                print("Rebuilding message links...", end=" ", flush=True)
                migrated = await memory.short_term.migrate_message_links()
                print(color(f"{len(migrated)} conversations linked", Colors.GREEN))
                if not (args.extract_entities_only or args.embeddings_only):
                    return

            # Handle embeddings-only mode
            if args.embeddings_only:
                await generate_message_embeddings(
                    memory,
                    args.data_dir,
                    sample_size=args.sample,
                    batch_size=args.batch_size,
                )
                return

            # Handle extract-entities-only mode
            if args.extract_entities_only:
                await extract_entities_from_loaded_sessions(
                    memory,
                    args.data_dir,
                    sample_size=args.sample,
                    batch_size=args.batch_size,
                    concurrency=args.concurrency,
                )
                return

            stats = await load_all_transcripts(
                memory,
                args.data_dir,
                sample_size=args.sample,
                extract_entities=not args.no_entities,
                generate_embeddings=not args.no_embeddings,
                skip_existing=args.resume,
                batch_size=args.batch_size,
                concurrency=args.concurrency,
                dry_run=args.dry_run,
            )

            if args.dry_run:
                sys.exit(0)

            # Print summary
            print()
            print(color("═" * 60, Colors.CYAN))
            print(color("  Loading Complete!", Colors.BOLD + Colors.GREEN))
            print(color("═" * 60, Colors.CYAN))
            print()
            print(f"  {color('Files loaded:', Colors.DIM)} {stats.files_loaded}")
            if stats.files_skipped > 0:
                print(f"  {color('Files skipped:', Colors.DIM)} {stats.files_skipped}")
            if stats.files_failed > 0:
                print(
                    f"  {color('Files failed:', Colors.DIM)} {color(str(stats.files_failed), Colors.RED)}"
                )
            print(f"  {color('Total turns:', Colors.DIM)} {stats.turns_loaded:,}")
            print(f"  {color('Unique speakers:', Colors.DIM)} {len(stats.speakers)}")
            print(f"  {color('Elapsed time:', Colors.DIM)} {format_duration(stats.elapsed)}")
            print(
                f"  {color('Throughput:', Colors.DIM)} {format_rate(stats.turns_loaded, stats.elapsed)}"
            )
            if stats.speakers:
                print()
                print(f"  {color('Speakers:', Colors.DIM)}")
                for speaker in sorted(stats.speakers):
                    print(f"    • {speaker}")

            if stats.errors:
                print()
                print(color("  Errors:", Colors.RED))
                for error in stats.errors[:10]:  # Show first 10 errors
                    print(f"    • {error}")
                if len(stats.errors) > 10:
                    print(f"    ... and {len(stats.errors) - 10} more")

            print()

    except KeyboardInterrupt:
        print()
        print(color("\nInterrupted by user.", Colors.YELLOW))
        sys.exit(130)
    except Exception as e:
        print(color(f"Error: {e}", Colors.RED))
        sys.exit(1)

    # Explicit successful exit
    sys.exit(0)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print()
        print(color("\nInterrupted by user.", Colors.YELLOW))
        sys.exit(130)
