import time
import logging
from contextlib import contextmanager

@contextmanager
def Timer(run_logger: logging.Logger, name: str, level=logging.INFO):
    """
    A context manager to log the duration of a block of code.

    Args:
        run_logger: The logger instance to use.
        name: A descriptive name for the timed block.
        level: The logging level to use for the duration message.
    """
    start_time = time.perf_counter()
    run_logger.debug(f"TIMER: Starting '{name}'")
    try:
        yield
    finally:
        end_time = time.perf_counter()
        duration = (end_time - start_time) * 1000  # Convert to milliseconds
        run_logger.log(level, f"TIMER: Finished '{name}'. Duration: {duration:.2f} ms")