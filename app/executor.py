import subprocess
import logging
import os
import sys

logger = logging.getLogger(__name__)

def execute_script(script_path: str, cwd: str):
    """
    Execute a Python script in a specified working directory.
    
    Args:
        script_path: The filename of the script to execute (relative to cwd).
        cwd: The directory to set as the Current Working Directory for the subprocess.
    """
    if not os.path.isdir(cwd):
        raise ValueError(f"The specified CWD does not exist or is not a directory: {cwd}")

    full_script_path = os.path.join(cwd, script_path)
    if not os.path.exists(full_script_path):
        raise FileNotFoundError(f"Script to execute not found at {full_script_path}")

    try:
        python_executable = sys.executable
        logger.debug(f"Executing script: {full_script_path}")
        logger.debug(f"Using interpreter: {python_executable}")
        logger.debug(f"Setting CWD to: {cwd}")

        result = subprocess.run(
            [python_executable, script_path], # Just use the script name, as CWD is set
            check=True, 
            capture_output=True, 
            text=True,
            cwd=cwd
        )
        logger.info(f"Script execution successful. stdout:\n{result.stdout}")
        logger.info(f"Script execution successful. stderr:\n{result.stderr}")
        return result
    except subprocess.CalledProcessError as e:
        error_msg = f"Execution of script '{script_path}' failed with exit code {e.returncode}.\n"
        error_msg += f"Stderr from script:\n{e.stderr}\n"
        error_msg += f"Stdout from script:\n{e.stdout}"
        logger.error(error_msg)
        raise RuntimeError(error_msg)
    except Exception as e:
        logger.error(f"An unhandled error occurred in execute_script: {str(e)}")
        raise RuntimeError(f"Error during execution of '{script_path}': {e}") from e