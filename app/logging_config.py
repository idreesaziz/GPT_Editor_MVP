import logging
import sys

def setup_run_logger(name: str, log_file: str) -> logging.Logger:
    """
    Creates and a configures a dedicated logger for a single execution run.

    This logger writes detailed, formatted messages to a specific file
    and does not propagate its messages to the root logger, preventing
    duplicate output on the console.

    Args:
        name: A unique name for the logger instance.
        log_file: The full path to the log file.

    Returns:
        A configured logging.Logger instance.
    """
    # Use a specific, unique name for each logger to avoid conflicts
    logger = logging.getLogger(name)
    
    # Set the level to capture everything from DEBUG upwards
    logger.setLevel(logging.DEBUG)
    
    # Prevent messages from being passed to the handlers of the root logger
    logger.propagate = False
    
    # If handlers are already attached (e.g., from a previous failed run in a notebook), clear them
    if logger.hasHandlers():
        logger.handlers.clear()

    # Create a file handler to write to the specified log file
    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    
    # Create a console handler to also see the output in real-time if needed
    # This can be commented out if you only want file output
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO) # Show only INFO and above on console

    # Define a beautiful format for the log messages
    file_formatter = logging.Formatter(
        '%(asctime)s,%(msecs)03d - %(levelname)-8s - %(message)s'
    )
    console_formatter = logging.Formatter('%(levelname)-8s - %(message)s')
    
    file_handler.setFormatter(file_formatter)
    console_handler.setFormatter(console_formatter)

    # Add the handlers to the logger
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    return logger
