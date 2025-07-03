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

    # ... (name, description, get_system_instruction are unchanged) ...
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
            # We no longer need special handling for "acceptable errors" because
            # our dummy files are now high-fidelity. A failure is a real failure.
            return False, f"[SandboxError] Script failed during execution.\n--- Stderr ---\n{e.stderr}"
        except Exception as e:
            return False, f"[SandboxError] An unexpected error occurred: {e}"