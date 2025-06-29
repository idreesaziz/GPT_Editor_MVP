import subprocess
import logging
import os
import re
import sys

logger = logging.getLogger(__name__)

def execute_script(script_path: str):
    """
    Execute the generated Python script, first replacing placeholder strings.
    
    Args:
        script_path: Path to the script file to execute
    """
    try:
        absolute_script_path = os.path.abspath(script_path)
        session_dir = os.path.dirname(absolute_script_path)
        script_name = os.path.basename(absolute_script_path)

        with open(absolute_script_path, 'r') as f:
            script_content = f.read()
        
        match = re.search(r'edit(\d+)\.py', script_name)
        if not match:
             raise ValueError(f"Could not extract index from script name: {script_name}")
        current_index = int(match.group(1))
        next_index = current_index + 1
        
        # *** THE FIX IS HERE ***
        # We now replace the variable assignment lines directly. This is much more robust.
        # This will correctly change `input_file = 'proxyN.mp4'` to `input_file = 'proxy0.mp4'`, etc.
        logger.debug(f"Replacing placeholders: 'proxyN.mp4' -> 'proxy{current_index}.mp4'")
        script_content = script_content.replace("input_file = 'proxyN.mp4'", f"input_file = 'proxy{current_index}.mp4'")
        
        logger.debug(f"Replacing placeholders: 'proxyN+1.mp4' -> 'proxy{next_index}.mp4'")
        script_content = script_content.replace("output_file = 'proxyN+1.mp4'", f"output_file = 'proxy{next_index}.mp4'")

        logger.debug(f"Modified script content:\n---\n{script_content}\n---")

        with open(absolute_script_path, 'w') as f:
            f.write(script_content)
        
        try:
            python_executable = sys.executable
            logger.debug(f"Executing script: {absolute_script_path}")
            logger.debug(f"Using interpreter: {python_executable}")
            logger.debug(f"Setting CWD to: {session_dir}")

            result = subprocess.run(
                [python_executable, absolute_script_path],
                check=True, 
                capture_output=True, 
                text=True,
                cwd=session_dir
            )
            logger.info(f"Script execution successful. stdout:\n{result.stdout}")
            logger.info(f"Script execution successful. stderr:\n{result.stderr}")
            return result
        except subprocess.CalledProcessError as e:
            error_msg = f"Execution of script '{absolute_script_path}' failed with exit code {e.returncode}.\n"
            error_msg += f"Stderr from script:\n{e.stderr}\n"
            error_msg += f"Stdout from script:\n{e.stdout}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
            
    except Exception as e:
        logger.error(f"An unhandled error occurred in execute_script: {str(e)}")
        raise RuntimeError(f"Error during execution of '{script_path}': {e}") from e