import asyncio
import functools
import logging

logger = logging.getLogger(__name__)

def task(timeout: float, name: str = None):
    """
    Decorator to designate an async function as a task that can be executed with a timeout.

    Args:
        timeout (float): The maximum time in seconds to wait for the task to complete.
                         Must be a finite positive number.
        name (str, optional): An optional name for the task. Defaults to the function's name.
    """
    # Validate timeout when the decorator is created.
    if not isinstance(timeout, (int, float)):
        raise TypeError(f"Timeout for task decorator must be a number (int or float), got {type(timeout).__name__}")
    if timeout <= 0:
        raise ValueError("Timeout for task decorator must be a positive number.")
    if timeout == float('inf'):
        # asyncio.wait_for does not accept float('inf') for timeout; it expects None for infinite wait.
        # However, the requirement is "positive numbers," usually implying finite durations.
        raise ValueError("Timeout for task decorator must be a finite positive number.")

    def decorator(func):
        @functools.wraps(func)
        async def wrapper(*args, **kwargs):
            task_name = name if name is not None else func.__name__
            logger.debug(f"Starting task '{task_name}' with timeout {timeout}s.")
            try:
                result = await asyncio.wait_for(func(*args, **kwargs), timeout=timeout)
                logger.debug(f"Task '{task_name}' completed successfully.")
                return result
            except asyncio.TimeoutError:
                logger.warning(f"Task '{task_name}' timed out after {timeout}s.")
                raise # Re-raise the timeout error for upstream handling
            except Exception:
                # Use logger.exception to include traceback in logs for unexpected errors
                logger.exception(f"Task '{task_name}' failed with an unexpected exception.")
                raise
        return wrapper
    return decorator