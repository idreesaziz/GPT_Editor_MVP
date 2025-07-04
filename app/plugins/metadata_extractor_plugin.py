import os
import subprocess
import sys
import ast
import logging
from typing import Tuple, Optional

from .base import ToolPlugin

logger = logging.getLogger(__name__)
FFPROBE_TIMEOUT = 10

class MetadataExtractorPlugin(ToolPlugin):
    """Plugin for extracting video metadata using FFprobe."""

    @property
    def name(self) -> str:
        return "Metadata Extractor"

    @property
    def description(self) -> str:
        return (
            "Analyzes a video file to extract and save its metadata (e.g., dimensions, duration, frame rate) "
            "to a JSON file. This is often a required first step before performing complex video manipulations."
        )

    @property
    def prerequisites(self) -> str:
        """Describes the prerequisites for using this tool."""
        return "None. This tool is typically a prerequisite for other tools."

    def get_system_instruction(self) -> str:
        """Provides the specific system prompt for generating FFprobe scripts."""
        return """
You are an AI assistant that generates Python scripts to extract video metadata using ffprobe.
The script will be given a dictionary of input files and a dictionary of output files.
You must read the video filename from the inputs dictionary.
You must write the JSON output of the ffprobe command to the filename specified in the outputs dictionary.
The script must only contain Python code using the 'subprocess' module to execute the ffprobe command.
Do NOT include any explanations, markdown formatting (like ```python), or extra text outside the script.

Example of a good script:
import subprocess
import json

# Assume inputs are like {'input_video': 'some_video.mp4'}
# Assume outputs are like {'metadata_json': 'some_output.json'}
# NOTE: The actual dictionary keys might vary, so parse them from the provided context.

input_video_path = ... # Get path from inputs dict
output_json_path = ... # Get path from outputs dict

command = [
    'ffprobe', '-v', 'quiet', '-print_format', 'json',
    '-show_format', '-show_streams', input_video_path
]
result = subprocess.run(command, check=True, capture_output=True, text=True)
with open(output_json_path, 'w') as f:
    f.write(result.stdout)

IMPORTANT: For error handling, do NOT use sys.exit(). Instead, catch exceptions and raise them.
"""

    def validate_script(self, script_code: str, sandbox_path: str) -> Tuple[bool, Optional[str]]:
        """Validates a script by checking syntax and running it in a sandbox."""
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
                timeout=FFPROBE_TIMEOUT
            )
            # Also check if the script actually created an output file
            # Note: We can't know the output filename here, but we can check if *any* new files were made.
            # A more robust check could parse the script to find the output filename. For now, this is sufficient.
            if not any(f.endswith('.json') for f in os.listdir(sandbox_path)):
                 return False, "[SandboxError] Script ran but did not create the expected JSON output file."

            return True, None
        except subprocess.TimeoutExpired:
            return False, f"[SandboxError] Script execution timed out."
        except subprocess.CalledProcessError as e:
            return False, f"[SandboxError] Script failed during execution.\n--- Stderr ---\n{e.stderr}"
        except Exception as e:
            return False, f"[SandboxError] An unexpected error occurred: {e}"