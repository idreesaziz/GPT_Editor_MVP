import subprocess
import logging
import os
import re

logger = logging.getLogger(__name__)

def execute_script(script_path: str):
    """
    Execute the generated Python script, first replacing placeholder strings.
    
    Args:
        script_path: Path to the script file to execute
    """
    try:
        # First, read and modify the script
        with open(script_path, 'r') as f:
            script_content = f.read()
        
        # Get the session directory and determine input/output files
        session_dir = os.path.dirname(script_path)
        script_name = os.path.basename(script_path)
        
        # Extract index from the script name (e.g., edit0.py -> 0)
        current_index = int(re.search(r'edit(\d+)\.py', script_name).group(1))
        next_index = current_index + 1
        
        # Replace placeholder strings with actual file paths
        script_content = script_content.replace('proxyN.mp4', f'proxy{current_index}.mp4')
        script_content = script_content.replace('proxyN+1.mp4', f'proxy{next_index}.mp4')
        
        # Create a modified script with the working directory set to the session directory
        # This ensures relative paths will work correctly
        new_script_content = f"""
import os
import sys

# Change to the session directory first
os.chdir(r"{session_dir}")

# Then execute the original script
{script_content}
"""
        
        # Write the modified script back
        with open(script_path, 'w') as f:
            f.write(new_script_content)
        
        # Now execute the modified script
        try:
            result = subprocess.run(["python", script_path], check=True, capture_output=True, text=True)
            logger.info(f"Script execution successful: {result.stdout}")
            return result
        except subprocess.CalledProcessError as e:
            error_msg = f"Script execution failed: {e.stderr}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    except Exception as e:
        logger.error(f"Error in execute_script: {str(e)}")
        raise
        
        # Now execute the modified script
        try:
            result = subprocess.run(["python", script_path], check=True, capture_output=True, text=True)
            logger.info(f"Script execution successful: {result.stdout}")
            return result
        except subprocess.CalledProcessError as e:
            error_msg = f"Script execution failed: {e.stderr}"
            logger.error(error_msg)
            raise RuntimeError(error_msg)
    except Exception as e:
        logger.error(f"Error in execute_script: {str(e)}")
        raise
        with open(script_path, 'w') as f:
            f.write(script_content)
        
        # Now execute the modified script
        try:
            result = subprocess.run(
                ["python", script_path], 
                check=True, 
                capture_output=True, 
                text=True
            )
            logger.info(f"Script execution successful: {result.stdout}")
            return result
        except subprocess.CalledProcessError as e:
            error_msg = f"Script execution failed: {e.stderr}"
            logger.error(error_msg)
            # Convert the exception to a more informative one
            raise RuntimeError(error_msg) from e
        
    except subprocess.CalledProcessError as e:
        logger.error(f"Error executing edit script: {e.stderr}")
        raise
