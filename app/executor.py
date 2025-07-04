import subprocess
import logging
import os
import sys

from .utils import Timer # <-- IMPORT TIMER

logger = logging.getLogger(__name__) # Keep for general logging

def execute_script(script_path: str, cwd: str, run_logger: logging.Logger):
    """
    Execute a Python script in a specified working directory.
    """
    if not os.path.isdir(cwd):
        raise ValueError(f"The specified CWD does not exist or is not a directory: {cwd}")

    full_script_path = os.path.join(cwd, script_path)
    if not os.path.exists(full_script_path):
        raise FileNotFoundError(f"Script to execute not found at {full_script_path}")

    try:
        run_logger.info("-" * 20 + " SCRIPT EXECUTION " + "-" * 20)
        
        with Timer(run_logger, f"Execution of '{script_path}'"):
            python_executable = sys.executable
            run_logger.info(f"EXECUTOR: Executing script: {full_script_path}")
            run_logger.debug(f"EXECUTOR: Using interpreter: {python_executable}")
            run_logger.debug(f"EXECUTOR: Setting CWD to: {cwd}")

            result = subprocess.run(
                [python_executable, script_path], # Just use the script name, as CWD is set
                check=True, 
                capture_output=True, 
                text=True,
                cwd=cwd
            )
        
        run_logger.info("-" * 52)
        if result.stdout:
            run_logger.debug(f"EXECUTOR STDOUT:\n{result.stdout.strip()}")
        if result.stderr:
            run_logger.debug(f"EXECUTOR STDERR:\n{result.stderr.strip()}")
        return result
    except subprocess.CalledProcessError as e:
        error_msg = f"Execution of script '{script_path}' failed with exit code {e.returncode}.\n"
        error_msg += f"Stderr from script:\n{e.stderr}\n"
        error_msg += f"Stdout from script:\n{e.stdout}"
        run_logger.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        run_logger.error(f"An unhandled error occurred in execute_script: {str(e)}", exc_info=True)
        raise RuntimeError(f"Error during execution of '{script_path}': {e}") from e