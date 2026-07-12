"""Shared utilities for agent framework integrations.

This module provides common functionality used across all framework integrations,
including async/sync bridging, input validation, and helper functions.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable, Coroutine
from functools import wraps
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")

# Shared executor for all integrations to avoid per-call overhead
# Using a module-level executor with lazy initialization
_executor: concurrent.futures.ThreadPoolExecutor | None = None
_EXECUTOR_MAX_WORKERS = 4
_SYNC_TIMEOUT_SECONDS = 30


def _get_executor() -> concurrent.futures.ThreadPoolExecutor:
    """Get or create the shared thread pool executor."""
    global _executor
    if _executor is None:
        _executor = concurrent.futures.ThreadPoolExecutor(max_workers=_EXECUTOR_MAX_WORKERS)
    return _executor


def run_sync(func: Callable[P, Coroutine[Any, Any, T]]) -> Callable[P, T]:
    """
    Decorator to run an async function synchronously.

    This decorator wraps an async function to make it callable from sync code.
    It handles the case where the code is already running in an async context
    by using a thread pool executor.

    Args:
        func: The async function to wrap

    Returns:
        A sync wrapper function

    Example:
        @run_sync
        async def my_async_function(arg: str) -> str:
            return await some_async_operation(arg)

        # Now callable synchronously
        result = my_async_function("test")
    """

    @wraps(func)
    def wrapper(*args: P.args, **kwargs: P.kwargs) -> T:
        try:
            loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None:
            # Running in async context - use thread pool
            executor = _get_executor()
            coro = func(*args, **kwargs)
            future = executor.submit(lambda: asyncio.run(coro))
            return future.result(timeout=_SYNC_TIMEOUT_SECONDS)
        else:
            # Not in async context - run directly
            return asyncio.run(func(*args, **kwargs))

    return wrapper


def validate_session_id(session_id: str) -> str:
    """
    Validate and normalize a session ID.

    Args:
        session_id: The session ID to validate

    Returns:
        The normalized session ID

    Raises:
        ValueError: If session_id is invalid
    """
    if not session_id:
        raise ValueError("session_id must be a non-empty string")
    if not isinstance(session_id, str):
        raise ValueError(f"session_id must be a string, got {type(session_id).__name__}")
    return session_id.strip()


def validate_query(query: str, allow_empty: bool = False) -> str:
    """
    Validate a search query.

    Args:
        query: The query string to validate
        allow_empty: Whether to allow empty queries

    Returns:
        The normalized query string

    Raises:
        ValueError: If query is invalid
    """
    if not isinstance(query, str):
        raise ValueError(f"query must be a string, got {type(query).__name__}")

    normalized = query.strip()
    if not allow_empty and not normalized:
        raise ValueError("query must be a non-empty string")

    return normalized


def validate_limit(limit: int, max_limit: int = 1000) -> int:
    """
    Validate a limit parameter.

    Args:
        limit: The limit value to validate
        max_limit: Maximum allowed limit

    Returns:
        The validated limit

    Raises:
        ValueError: If limit is invalid
    """
    if not isinstance(limit, int):
        raise ValueError(f"limit must be an integer, got {type(limit).__name__}")
    if limit < 1:
        raise ValueError("limit must be at least 1")
    if limit > max_limit:
        raise ValueError(f"limit must be at most {max_limit}")
    return limit


def validate_threshold(threshold: float) -> float:
    """
    Validate a similarity threshold.

    Args:
        threshold: The threshold value (0.0 to 1.0)

    Returns:
        The validated threshold

    Raises:
        ValueError: If threshold is invalid
    """
    if not isinstance(threshold, (int, float)):
        raise ValueError(f"threshold must be a number, got {type(threshold).__name__}")
    if threshold < 0.0 or threshold > 1.0:
        raise ValueError("threshold must be between 0.0 and 1.0")
    return float(threshold)


def format_context_section(title: str, items: list[str], max_items: int = 10) -> str:
    """
    Format a section of context for inclusion in prompts.

    Args:
        title: Section title
        items: List of context items
        max_items: Maximum items to include

    Returns:
        Formatted section string
    """
    if not items:
        return ""

    limited_items = items[:max_items]
    formatted = [f"## {title}"]
    for item in limited_items:
        formatted.append(f"- {item}")

    if len(items) > max_items:
        formatted.append(f"- ... and {len(items) - max_items} more")

    return "\n".join(formatted)


def truncate_text(text: str, max_length: int = 200, suffix: str = "...") -> str:
    """
    Truncate text to a maximum length.

    Args:
        text: Text to truncate
        max_length: Maximum length including suffix
        suffix: Suffix to add when truncated

    Returns:
        Truncated text
    """
    if len(text) <= max_length:
        return text
    return text[: max_length - len(suffix)] + suffix


class IntegrationError(Exception):
    """Base exception for integration errors."""

    pass


class ValidationError(IntegrationError):
    """Exception raised for validation errors."""

    pass


class ConnectionError(IntegrationError):
    """Exception raised for connection errors."""

    pass


class TimeoutError(IntegrationError):
    """Exception raised for timeout errors."""

    pass
