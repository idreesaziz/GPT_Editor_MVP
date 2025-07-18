# app/utils.py

import time
import logging
from contextlib import contextmanager

@contextmanager
def Timer(run_logger: logging.Logger, name: str, level=logging.INFO):
    """
    A context manager to log the duration of a block of code.
    """
    start_time = time.perf_counter()
    run_logger.debug(f"TIMER: Starting '{name}'")
    try:
        yield
    finally:
        end_time = time.perf_counter()
        duration_ms = (end_time - start_time) * 1000
        run_logger.log(level, f"TIMER: Finished '{name}'. Duration: {duration_ms:.2f} ms")