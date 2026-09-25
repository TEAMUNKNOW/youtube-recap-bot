"""Retry helpers with exponential backoff and jitter."""

from __future__ import annotations

import asyncio
import logging
import random
from functools import wraps
from typing import Any, Callable, Optional, Type, TypeVar

from bot.exceptions import BotError, ErrorCode

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

RETRYABLE_STATUS = {408, 429, 500, 502, 503, 504}


def is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, BotError):
        return exc.retryable
    name = type(exc).__name__.lower()
    if any(k in name for k in ("timeout", "connection", "network", "temporary")):
        return True
    return False


async def retry_async(
    func: Callable[..., Any],
    *,
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    jitter: float = 0.25,
    retryable_exceptions: Optional[tuple[Type[BaseException], ...]] = None,
    on_retry: Optional[Callable[[int, BaseException], Any]] = None,
    **kwargs: Any,
) -> Any:
    """Execute an async callable with exponential backoff."""
    last_exc: Optional[BaseException] = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await func(**kwargs)
        except Exception as exc:
            last_exc = exc
            should_retry = False
            if retryable_exceptions and isinstance(exc, retryable_exceptions):
                should_retry = True
            elif is_retryable(exc):
                should_retry = True

            if not should_retry or attempt >= max_attempts:
                raise

            delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
            delay *= 1 + random.uniform(-jitter, jitter)
            delay = max(0.1, delay)
            logger.warning(
                "Retry %s/%s after %s: %s",
                attempt,
                max_attempts,
                type(exc).__name__,
                str(exc)[:200],
            )
            if on_retry:
                maybe = on_retry(attempt, exc)
                if asyncio.iscoroutine(maybe):
                    await maybe
            await asyncio.sleep(delay)

    assert last_exc is not None
    raise last_exc


def with_retry(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
) -> Callable[[F], F]:
    def decorator(fn: F) -> F:
        @wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            return await retry_async(
                lambda: fn(*args, **kwargs),
                max_attempts=max_attempts,
                base_delay=base_delay,
                max_delay=max_delay,
            )

        return wrapper  # type: ignore[return-value]

    return decorator
