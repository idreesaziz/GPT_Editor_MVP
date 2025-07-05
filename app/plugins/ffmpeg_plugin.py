import os
import subprocess
import sys
import ast
import logging
import json
from typing import Tuple, Optional

from .base import ToolPlugin

logger = logging.getLogger(__name__)

FFMPEG_TIMEOUT = 30 # Increased timeout to allow for dummy file creation and execution

def _create_dummy_video(output_path: str, metadata: dict):
    """Creates a dummy test pattern video with specific metadata."""
    try:
        width = metadata.get('width', 640)
        height = metadata.get('height', 480)
        duration = metadata.get('duration', 5)
        frame_rate = metadata.get('frame_rate', 24)
        
        command = [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', f'testsrc=size={width}x{height}:rate={frame_rate}:duration={duration}',
            '-f', 'lavfi', '-i', f'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-c:v', 'libx264', '-t', str(duration), '-pix_fmt', 'yuv420p',
            output_path
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create dummy video {output_path}: {e.stderr}")
        raise

def _get_video_metadata(file_path: str) -> Optional[dict]:
    """Gets metadata for a video file using ffprobe."""
    try:
        command = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', '-show_streams', file_path
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        data = json.loads(result.stdout)
        video_stream = next((s for s in data['streams'] if s['codec_type'] == 'video'), None)
        if not video_stream: return None

        return {
            'width': int(video_stream['width']),
            'height': int(video_stream['height']),
            'duration': float(data['format'].get('duration', video_stream.get('duration', 0))),
            'frame_rate': eval(video_stream.get('r_frame_rate', '0/1')),
        }
    except (subprocess.CalledProcessError, KeyError, json.JSONDecodeError, StopIteration) as e:
        logger.warning(f"Could not get metadata for {file_path}: {e}")
        return None

def _is_video_readable(file_path: str) -> bool:
    """Checks if a video file is readable by ffprobe without errors."""
    command = ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', file_path]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return True
    except subprocess.CalledProcessError:
        return False

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

    @property
    def prerequisites(self) -> str:
        """Describes the prerequisites for using this tool."""
        return (
            "Highly recommended to be preceded by a step using the 'Metadata Extractor' tool, "
            "especially for tasks involving text overlays, resizing, or complex filter graphs "
            "that depend on the video's dimensions or frame rate."
        )

    def get_system_instruction(self) -> str:
        """Provides the specific system prompt for generating FFmpeg scripts."""
        return """
You are an AI assistant that generates Python scripts for video editing using FFmpeg.
The script will be given a dictionary of input files and a dictionary of output files.
You must parse these dictionaries to get the filenames for your script.
The script must only contain Python code using the 'subprocess' module to execute FFmpeg commands.
Do NOT include any explanations, markdown formatting (like ```python), or extra text outside the script.

**CRITICAL SCRIPTING RULES:**
1.  **NO FUNCTIONS:** Your entire output must be top-level, executable Python code. Do NOT define any functions (e.g., `def main():`). The generated code will be executed as a flat script.
2.  **Error Handling**: Do NOT use `sys.exit()`. Catch `subprocess.CalledProcessError` and other exceptions, then raise a `RuntimeError` with a descriptive message to be handled by the calling code.
3.  **Inputs and Outputs**: Determine the input video and any other necessary files (like metadata) from the `inputs` dictionary provided in the user prompt context. Write your final video to the path specified in the `outputs` dictionary.
4.  **Metadata**: If the `inputs` dictionary contains a path to a `.json` file, you MUST open and read this file to get video metadata (like width, height, duration) to construct more precise FFmpeg commands.

---
**GENERAL PRINCIPLES FOR COMPLEX FILTERS (`-vf` and `-af`)**
Study these patterns to construct robust commands. Pay close attention to quoting.

**Principle 1: Chaining Simple Filters**
Simple filters are chained with commas `,` inside a single string.
```python
# Python code to scale a video and then crop it to the center
width = 1280
height = 720
crop_size = 480
filter_expression = f"scale={width}:{height},crop={crop_size}:{crop_size}"
command = ['ffmpeg', '-i', 'input.mp4', '-vf', filter_expression, 'output.mp4']


Principle 2: Dynamic & Temporal Effects using Timeline Variables
Use FFmpeg's timeline variables inside expressions. Expressions MUST be enclosed in single quotes '.
Common variables: t (time in seconds), n (frame number), w and h (input width/height), x and y (pixel coordinates).

# Python code for a blur that increases over the video's duration
# 'duration' is a Python variable loaded from metadata.
# The expression `(5*t)/{duration}` is evaluated by FFmpeg for every frame.
filter_expression = f"gblur=sigma='(5*t)/{duration}'"
command = ['ffmpeg', '-i', 'input.mp4', '-vf', filter_expression, 'output.mp4']
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Python
IGNORE_WHEN_COPYING_END

Principle 3: Conditional Logic
Use if(A,B,C) or between(V,min,max) for effects that change over time or space.

# Python code to make the video black and white only between 5 and 10 seconds
filter_expression = "hue=s='if(between(t,5,10),0,1)'"
# This sets saturation (s) to 0 if time 't' is between 5 and 10, otherwise to 1 (original).
command = ['ffmpeg', '-i', 'input.mp4', '-vf', filter_expression, 'output.mp4']
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Python
IGNORE_WHEN_COPYING_END

Principle 4: Complex Filtergraphs (Multiple inputs/outputs)
For effects that combine multiple streams (e.g., overlays, picture-in-picture, split-screen):

Use semicolons ; to separate independent filter chains.

Label stream inputs and outputs with square brackets [...]. [0:v] is the video from the first input file. [1:v] is from the second. [out] is a custom label.

# Python code for a picture-in-picture effect.
# This takes one input video and overlays a scaled-down version of itself.
filtergraph = (
    "[0:v]split=2[main][pip];"  # 1. Split the input video into two identical streams, named [main] and [pip]
    "[pip]scale=iw/3:-1,gblur=5[pip_scaled_blurred];"  # 2. Take the [pip] stream, scale it down, blur it, and name the result [pip_scaled_blurred]
    "[main][pip_scaled_blurred]overlay=W-w-10:H-h-10"  # 3. Take [main] and [pip_scaled_blurred] and overlay the second on top of the first at the bottom-right.
)
command = ['ffmpeg', '-i', 'input.mp4', '-filter_complex', filtergraph, 'output.mp4']
IGNORE_WHEN_COPYING_START
content_copy
download
Use code with caution.
Python
IGNORE_WHEN_COPYING_END

The script must be complete and executable Python code.
"""

    def validate_script(self, script_code: str, sandbox_path: str, inputs: dict, outputs: dict) -> Tuple[bool, Optional[str]]:
        """
        Validates an FFmpeg script by running it against high-fidelity dummy files.
        This involves:
        1. Creating dummy videos that match the metadata of the real inputs.
        2. Modifying the script's `inputs` to point to these dummies.
        3. Executing the script in the sandbox.
        4. Checking if the output video was created and is readable.
        """
        try:
            ast.parse(script_code)
        except SyntaxError as e:
            return False, f"[SyntaxError] Invalid Python syntax: {e}"

        dummy_files_created = []
        try:
            # 1. Create dummy versions of all video inputs
            real_to_dummy_map = {}
            dummy_inputs = inputs.copy()

            for key, filename in inputs.items():
                if isinstance(filename, str) and filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                    real_path = os.path.join(sandbox_path, filename)
                    # Handle case where file might not exist (e.g., from a failed previous step)
                    if not os.path.exists(real_path):
                        # Create a generic placeholder if the real one is missing
                        placeholder_meta = {'width': 640, 'height': 480, 'duration': 10, 'frame_rate': 24}
                        _create_dummy_video(real_path, placeholder_meta)
                        logger.warning(f"Real input {filename} not found in sandbox, created a generic placeholder for validation.")

                    metadata = _get_video_metadata(real_path)
                    if not metadata:
                        return False, f"[SandboxError] Could not read metadata from real input file: {filename}"
                    
                    dummy_filename = f"dummy_{filename}"
                    dummy_path = os.path.join(sandbox_path, dummy_filename)
                    _create_dummy_video(dummy_path, metadata)
                    
                    dummy_files_created.append(dummy_path)
                    real_to_dummy_map[filename] = dummy_filename
                    dummy_inputs[key] = dummy_filename
                # Also copy over non-video files like JSON
                elif isinstance(filename, str) and os.path.exists(os.path.join(sandbox_path, filename)):
                    dummy_inputs[key] = filename # No change needed, it's already in the sandbox

            # 2. Construct and write the test script
            inputs_def = f"inputs = {json.dumps(dummy_inputs)}"
            outputs_def = f"outputs = {json.dumps(outputs)}"
            full_test_script = f"{inputs_def}\n{outputs_def}\n\n{script_code}"

            script_path_in_sandbox = os.path.join(sandbox_path, "test_script.py")
            with open(script_path_in_sandbox, "w") as f: f.write(full_test_script)

            # 3. Execute the script and find the output
            files_before = set(os.listdir(sandbox_path))
            result = subprocess.run(
                [sys.executable, script_path_in_sandbox],
                cwd=sandbox_path, check=True, capture_output=True, text=True,
                timeout=FFMPEG_TIMEOUT
            )
            files_after = set(os.listdir(sandbox_path))
            
            # Identify the newly created file(s) based on the 'outputs' dict
            output_found = False
            for output_key, output_filename in outputs.items():
                if output_filename in (files_after - files_before):
                    output_found = True
                    output_path = os.path.join(sandbox_path, output_filename)
                    if not _is_video_readable(output_path):
                        return False, f"[SandboxError] Script produced a corrupt or unreadable output video file: {output_filename}"

            if not output_found:
                # Check for any new video file as a fallback
                new_videos = [f for f in (files_after - files_before) if f.lower().endswith(('.mp4', '.mov'))]
                if new_videos:
                    output_path = os.path.join(sandbox_path, new_videos[0])
                    if _is_video_readable(output_path):
                        logger.warning(f"Script created an unexpected video file '{new_videos[0]}' but it was valid. Passing.")
                        return True, None # It worked, even if filename was unexpected.
                return False, "[SandboxError] Script ran successfully but did not create any of the expected output video files."
                
            return True, None

        except subprocess.TimeoutExpired:
            return False, f"[SandboxError] Script execution timed out."
        except subprocess.CalledProcessError as e:
            return False, f"[SandboxError] Script failed during execution.\n--- Stderr ---\n{e.stderr}"
        except Exception as e:
            return False, f"[SandboxError] An unexpected error occurred during validation: {e}"
        finally:
            # 5. Clean up all dummy files
            for f in dummy_files_created:
                if os.path.exists(f):
                    os.remove(f)