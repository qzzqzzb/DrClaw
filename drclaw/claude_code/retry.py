"""Retry helper for transient Claude SDK connection errors."""

from __future__ import annotations

import asyncio
import random
from typing import Any, Callable, Coroutine, TypeVar

from loguru import logger

T = TypeVar("T")

# These are the only error types worth retrying — transient subprocess/IPC failures.
# CLINotFoundError subclasses CLIConnectionError but should NOT be retried
# (missing binary won't heal itself).
_RETRYABLE: tuple[type[Exception], ...] = ()
_NON_RETRYABLE: tuple[type[Exception], ...] = ()

try:
    from claude_agent_sdk import CLIConnectionError, CLINotFoundError, ProcessError

    _RETRYABLE = (CLIConnectionError, ProcessError)
    _NON_RETRYABLE = (CLINotFoundError,)
except ImportError:
    pass


def _is_retryable(exc: BaseException) -> bool:
    if not _RETRYABLE:
        return False
    if _NON_RETRYABLE and isinstance(exc, _NON_RETRYABLE):
        return False
    return isinstance(exc, _RETRYABLE)


async def with_retry(
    fn: Callable[[], Coroutine[Any, Any, T]],
    *,
    max_retries: int,
    base_delay: float,
    max_delay: float,
    session_id: str = "",
) -> T:
    """Run *fn* with exponential back-off retry on transient SDK errors.

    Each attempt calls *fn()* fresh — the caller is responsible for providing
    a factory that creates a new client on each invocation so corrupted
    subprocesses don't poison retries.

    Args:
        fn: Zero-argument async callable to retry.
        max_retries: Maximum number of additional attempts after the first.
        base_delay: Initial sleep duration in seconds before the first retry.
        max_delay: Upper bound for sleep duration.
        session_id: Used only for log context.

    Returns:
        The return value of *fn* on success.

    Raises:
        The last exception if all attempts are exhausted, or immediately for
        non-retryable errors (e.g. CLINotFoundError).
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return await fn()
        except BaseException as exc:
            if not _is_retryable(exc):
                raise
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = min(base_delay * (2 ** attempt), max_delay)
            # Full jitter: sleep for a random fraction of the computed delay.
            jitter = delay * random.uniform(0.5, 1.0)
            logger.warning(
                "CC session {} transient error (attempt {}/{}), retrying in {:.1f}s: {}",
                session_id, attempt + 1, max_retries, jitter, exc,
            )
            await asyncio.sleep(jitter)

    raise last_exc  # type: ignore[misc]
