import os
import subprocess
import sys
import tempfile
import ast
import shutil
import logging
from typing import Tuple, Optional

from .base import ToolPlugin

logger = logging.getLogger(__name__)

# Constants specific to this plugin's validation
FFMPEG_TIMEOUT = 15 

class FFmpegPlugin(ToolPlugin):
    """Plugin for video editing using FFmpeg."""

    # A class-level cache for the dummy video path to avoid recreating it on every single validation.
    # It will be cleaned up when the app process ends.
    _dummy_video_path: Optional[str] = None

    @property
    def name(self) -> str:
        return "FFmpeg Video Editor"

    @property
    def description(self) -> str:
        return (
            "A powerful tool for command-line video and audio manipulation. "
            "Use this for tasks like trimming, cropping, adding text, changing speed, "
            "applying filters (e.g., black and white, blur), and format conversion."
        )

    def get_system_instruction(self) -> str:
        """Provides the specific system prompt for generating FFmpeg scripts."""
        return """
You are an AI assistant that generates Python scripts for video editing using FFmpeg.
The script should take a video file named 'proxyN.mp4' as input and output the result
to a file named 'proxyN+1.mp4', where N is the current proxy index.
The script must only contain Python code using the 'subprocess' module to execute FFmpeg commands.
Do NOT include any explanations, markdown formatting (like ```python), or extra text outside the script.

IMPORTANT: For error handling, do NOT use sys.exit(). Instead, catch exceptions and raise them
to be handled by the calling code. This allows the FastAPI application to properly report errors.

The script must be executable Python code.
"""

    def _get_or_create_dummy_video(self) -> str:
        """Creates a dummy video if it doesn't exist, and returns its path."""
        if FFmpegPlugin._dummy_video_path and os.path.exists(FFmpegPlugin._dummy_video_path):
            return FFmpegPlugin._dummy_video_path

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_f:
            dummy_path = temp_f.name
        
        logger.debug(f"Creating dummy video for validation at: {dummy_path}")
        command = [
            'ffmpeg', '-y', '-f', 'lavfi', '-i', 'color=c=black:s=128x72:r=15:d=60',
            '-f', 'lavfi', '-i', 'anullsrc', '-c:v', 'libx264', '-c:a', 'aac', '-t', '60',
            dummy_path
        ]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True)
            FFmpegPlugin._dummy_video_path = dummy_path
            return dummy_path
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to create dummy video for validation: {e.stderr}")
            raise RuntimeError(f"FFmpeg failed to create a dummy video file: {e.stderr}") from e


    def validate_script(self, script_code: str) -> Tuple[bool, Optional[str]]:
        """
        Validates an FFmpeg script, managing the dummy video asset internally.
        """
        dummy_input_path = self._get_or_create_dummy_video()

        # 1. Quick Syntax Check
        logger.debug("Running validation step 1 (FFmpeg Plugin): Quick Syntax Check")
        try:
            ast.parse(script_code)
        except SyntaxError as e:
            error_msg = f"[SyntaxError] Invalid Python syntax on line {e.lineno}: {e.msg}"
            logger.debug(f"Syntax check FAILED: {error_msg}")
            return False, error_msg
        logger.debug("Syntax check PASSED.")

        # 2. Sandboxed Execution
        logger.debug("Running validation step 2 (FFmpeg Plugin): Sandboxed Execution")
        with tempfile.TemporaryDirectory() as sandbox_dir:
            try:
                script_path_in_sandbox = os.path.join(sandbox_dir, "test_script.py")
                with open(script_path_in_sandbox, "w") as f:
                    f.write(script_code)

                shutil.copy(dummy_input_path, os.path.join(sandbox_dir, "proxyN.mp4"))

                logger.debug(f"Executing sandboxed script: {script_path_in_sandbox}")
                result = subprocess.run(
                    [sys.executable, script_path_in_sandbox],
                    cwd=sandbox_dir,
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=FFMPEG_TIMEOUT
                )
                logger.debug(f"Sandboxed execution successful. Stderr: {result.stderr}")
                return True, None

            except subprocess.TimeoutExpired:
                error_msg = f"[SandboxError] Script execution timed out after {FFMPEG_TIMEOUT} seconds."
                logger.debug(f"Sandboxed execution FAILED: {error_msg}")
                return False, error_msg
            except subprocess.CalledProcessError as e:
                stderr = e.stderr.lower()
                acceptable_errors = ["invalid duration", "cannot seek", "end of file", "past eof"]
                
                if any(err_str in stderr for err_str in acceptable_errors):
                    logger.debug(f"Sandboxed execution failed with an acceptable, data-dependent error. Approving script. Error: {e.stderr}")
                    return True, None
                else:
                    error_msg = f"[SandboxError] Script failed during execution.\n--- Stderr ---\n{e.stderr}"
                    logger.debug(f"Sandboxed execution FAILED with a real error: {error_msg}")
                    return False, error_msg
            except Exception as e:
                error_msg = f"[SandboxError] An unexpected error occurred during validation: {e}"
                logger.debug(f"Sandboxed execution FAILED: {error_msg}")
                return False, error_msg