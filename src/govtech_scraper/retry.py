"""Retry and backoff utilities."""

import asyncio
import functools
import logging
from typing import TypeVar, Callable, Any

logger = logging.getLogger(__name__)

T = TypeVar("T")


def retry_async(
    max_attempts: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 60.0,
    exponential: bool = True,
    retry_on: tuple[type[Exception], ...] = (Exception,),
):
    """Decorator for async functions with exponential backoff.

    Handles both regular API failures and GitHub secondary rate limits.
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exception = None
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except retry_on as e:
                    last_exception = e
                    if attempt == max_attempts - 1:
                        raise
                    delay = base_delay * (2 ** attempt if exponential else 1)
                    delay = min(delay, max_delay)
                    logger.warning(
                        f"{func.__name__} attempt {attempt + 1}/{max_attempts} failed: {e}. "
                        f"Retrying in {delay:.1f}s..."
                    )
                    await asyncio.sleep(delay)
            raise last_exception  # type: ignore
        return wrapper
    return decorator
