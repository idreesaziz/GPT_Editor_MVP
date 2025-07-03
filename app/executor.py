import subprocess
import logging
import os
import sys

logger = logging.getLogger(__name__)

def execute_script(script_path: str, cwd: str):
    """
    Execute a Python script in a specified working directory.
    The script is expected to be fully formed with no placeholders.
    
    Args:
        script_path: Absolute path to the script file to execute.
        cwd: The directory to set as the Current Working Directory for the subprocess.
    """
    if not os.path.isabs(script_path):
        raise ValueError("execute_script requires an absolute path for the script.")
    if not os.path.isdir(cwd):
        raise ValueError(f"The specified CWD does not exist or is not a directory: {cwd}")

    try:
        python_executable = sys.executable
        logger.debug(f"Executing script: {script_path}")
        logger.debug(f"Using interpreter: {python_executable}")
        logger.debug(f"Setting CWD to: {cwd}")

        result = subprocess.run(
            [python_executable, os.path.basename(script_path)], # Run with relative name from CWD
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