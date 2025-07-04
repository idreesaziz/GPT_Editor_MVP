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

FFMPEG_TIMEOUT = 15 

class FFmpegPlugin(ToolPlugin):
    """Plugin for video editing using FFmpeg."""

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
The script will be given a dictionary of input files and a dictionary of output files.
You must parse these dictionaries to get the filenames for your script.
The script must only contain Python code using the 'subprocess' module to execute FFmpeg commands.
Do NOT include any explanations, markdown formatting (like ```python), or extra text outside the script.

**IMPORTANT RULES FOR SCRIPTING:**
1.  **Error Handling**: Do NOT use `sys.exit()`. Catch `subprocess.CalledProcessError` and other exceptions, then raise them to be handled by the calling code.
2.  **Inputs and Outputs**: Determine the input video and any other necessary files (like metadata) from the `inputs` dictionary provided in the user prompt context. Write your final video to the path specified in the `outputs` dictionary.
3.  **Metadata**: If the `inputs` dictionary contains a path to a `.json` file, you can open and read this file to get video metadata (like width, height, duration) to construct more precise FFmpeg commands.

The script must be complete and executable Python code.
"""

    def validate_script(self, script_code: str, sandbox_path: str) -> Tuple[bool, Optional[str]]:
        """
        Validates an FFmpeg script within a given, pre-populated sandbox.
        """
        try:
            ast.parse(script_code)
        except SyntaxError as e:
            return False, f"[SyntaxError] Invalid Python syntax: {e}"

        script_path_in_sandbox = os.path.join(sandbox_path, "test_script.py")
        with open(script_path_in_sandbox, "w") as f:
            f.write(script_code)

        try:
            result = subprocess.run(
                [sys.executable, script_path_in_sandbox],
                cwd=sandbox_path,
                check=True,
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT
            )
            return True, None
        except subprocess.TimeoutExpired:
            return False, f"[SandboxError] Script execution timed out."
        except subprocess.CalledProcessError as e:
            return False, f"[SandboxError] Script failed during execution.\n--- Stderr ---\n{e.stderr}"
        except Exception as e:
            return False, f"[SandboxError] An unexpected error occurred: {e}"