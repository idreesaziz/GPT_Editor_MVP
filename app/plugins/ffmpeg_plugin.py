# app/plugins/ffmpeg_plugin.py

import logging
import os
import shutil
import subprocess
import json
import sys
from typing import Dict, Optional, List

import google.generativeai as genai
from google import genai as vertex_genai
from google.genai import types
from google.genai.types import HttpOptions
import ffmpeg

from .base import ToolPlugin

# --- Configuration ---
FFMPEG_CODE_MODEL = "gemini-2.5-flash"
MAX_CODE_GEN_RETRIES = 3

# Check if we should use Vertex AI
USE_VERTEX_AI = os.getenv("USE_VERTEX_AI", "false").lower() == "true"

# --- Custom Exception ---
class FFmpegGenerationError(Exception):
    """Custom exception for errors during FFmpeg processing."""
    pass

# --- Plugin Definition ---
class FFmpegProcessor(ToolPlugin):
    """
    A plugin that processes videos and images using FFmpeg.
    It creates FFmpeg scripts for simple video processing tasks like
    flipping, color correction, contrast adjustment, and other basic transformations.
    """

    def __init__(self):
        super().__init__()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable not found or not set.")
        
        if USE_VERTEX_AI:
            self.vertex_client = vertex_genai.Client(
                vertexai=True,
                project=os.getenv("VERTEX_PROJECT_ID"),
                location=os.getenv("VERTEX_LOCATION", "us-central1")
            )
            self.model = None  # We'll use the client directly
        else:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(FFMPEG_CODE_MODEL)

    @property
    def name(self) -> str:
        return "FFmpeg Processor"

    @property
    def description(self) -> str:
        return (
            "Processes videos and images using FFmpeg for simple transformations like flipping, rotating, "
            "color correction, contrast adjustment, brightness changes, cropping, grayscale conversion, and other basic effects. "
            "Takes an input video or image file and applies the requested transformation to produce an output file."
        )

    def execute_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        try:
            prompt = task_details["task"]
            # The output filename is now relative to the asset_unit_path
            output_filename = task_details["output_filename"]
            
            run_logger.info(f"FFMPEG PLUGIN: Starting task for unit '{task_details.get('unit_id')}' - '{prompt[:100]}...'.")
            run_logger.debug(f"FFMPEG PLUGIN: Task details: {task_details}")
            run_logger.debug(f"FFMPEG PLUGIN: Asset unit path: {asset_unit_path}")

            # Check if there's an input file specified
            input_file = task_details.get("input_file")
            if not input_file:
                run_logger.error(f"FFMPEG PLUGIN: Missing 'input_file' parameter in task_details: {task_details}")
                raise FFmpegGenerationError("FFmpeg plugin requires an 'input_file' parameter in task_details")
        
            # Store the original relative path for metadata
            original_input_file = input_file
        
            # Convert relative path to absolute path if necessary
            if not os.path.isabs(input_file):
                # Input file should be relative to the session directory
                # asset_unit_path is like: /path/to/GPT_Editor_MVP/sessions/session_id/assets/unit_id
                # We need to go up to the session directory and then resolve the input file path
                session_dir = os.path.dirname(os.path.dirname(asset_unit_path))  # Go up from assets/unit_id to session root
                input_file = os.path.join(session_dir, input_file)
                run_logger.debug(f"FFMPEG PLUGIN: Resolved input file path to: {input_file}")
        
            # Check if input file exists
            if not os.path.exists(input_file):
                run_logger.error(f"FFMPEG PLUGIN: Input file not found: {input_file}")
                raise FFmpegGenerationError(f"Input file not found: {input_file}")
        
            run_logger.info(f"FFMPEG PLUGIN: Using input file: {input_file}")

            last_error = None
            generated_code = None
        
            # Amendment data is now passed directly by the orchestrator
            original_code = task_details.get("original_plugin_data", {}).get("ffmpeg_script")
            if original_code:
                run_logger.info(f"FFMPEG PLUGIN: Amendment mode detected. Using provided script.")

            for attempt in range(MAX_CODE_GEN_RETRIES):
                run_logger.info(f"FFMPEG PLUGIN: Code generation attempt {attempt + 1}/{MAX_CODE_GEN_RETRIES}.")
                try:
                    generated_code = self._generate_ffmpeg_script(
                        prompt=prompt,
                        input_file=input_file,
                        output_file=output_filename,
                        original_script=original_code,
                        last_generated_script=generated_code,
                        last_error=last_error,
                        run_logger=run_logger
                    )
                except Exception as e:
                    run_logger.error(f"FFMPEG PLUGIN: LLM code generation failed: {e}", exc_info=True)
                    raise FFmpegGenerationError(f"LLM call for FFmpeg script generation failed: {e}") from e

                # Script is now created inside the asset unit directory
                script_filename = f"ffmpeg_script_attempt{attempt+1}.py"
                script_path = os.path.join(asset_unit_path, script_filename)
                with open(script_path, "w") as f:
                    f.write(generated_code)

                try:
                    run_logger.info(f"FFMPEG PLUGIN: Executing FFmpeg script: {script_filename} in {asset_unit_path}")
                    # The CWD for FFmpeg is now the asset unit's own directory
                    self._run_ffmpeg_script(script_filename, input_file, output_filename, asset_unit_path, run_logger)

                    # Check if output file was created
                    final_output_path = os.path.join(asset_unit_path, output_filename)
                    if os.path.exists(final_output_path):
                        ffmpeg_plugin_data = {"ffmpeg_script": generated_code, "input_file": original_input_file}
                        self._create_metadata_file(task_details, asset_unit_path, [output_filename], ffmpeg_plugin_data)
                    
                        self._cleanup(asset_unit_path)
                        run_logger.info(f"FFMPEG PLUGIN: Successfully processed media '{output_filename}' in unit '{task_details.get('unit_id')}'.")
                        return [output_filename]
                    else:
                        last_error = "FFmpeg script execution finished, but no output file was found."
                        run_logger.warning(f"FFMPEG PLUGIN: {last_error}")

                except subprocess.CalledProcessError as e:
                    last_error = f"FFmpeg execution failed with exit code {e.returncode}.\nStderr:\n{e.stderr}"
                    run_logger.warning(f"FFMPEG PLUGIN: FFmpeg execution failed. Error:\n{e.stderr}")
                finally:
                    if os.path.exists(script_path):
                        os.remove(script_path)

            final_error_msg = f"FFMPEG PLUGIN: Failed to process media after {MAX_CODE_GEN_RETRIES} attempts. Last error: {last_error}"
            run_logger.error(final_error_msg)
            raise FFmpegGenerationError(final_error_msg)
            
        except Exception as e:
            run_logger.error(f"FFMPEG PLUGIN: Unexpected error in execute_task: {e}", exc_info=True)
            raise

    def _generate_ffmpeg_script(
        self, 
        prompt: str, 
        input_file: str, 
        output_file: str,
        original_script: Optional[str], 
        last_generated_script: Optional[str], 
        last_error: Optional[str], 
        run_logger: logging.Logger
    ) -> str:
        
        system_prompt = """
You are an expert FFmpeg developer. Your task is to write a complete, self-contained Python script using the ffmpeg-python library to process a video or image file.

CRITICAL RULES:
1. The script must import ffmpeg (the ffmpeg-python library)
2. The script must accept exactly 2 command line arguments: input_file and output_file
3. Use ffmpeg-python syntax, NOT command-line ffmpeg syntax
4. The script must be executable as: python script.py input.mp4 output.mp4 OR python script.py input.png output.png
5. Handle both video and image files gracefully
6. Handle common errors gracefully and provide informative error messages
7. Your entire response MUST be just the Python code, with no explanations, markdown, or other text

COMMON OPERATIONS EXAMPLES:
- Flip horizontally: ffmpeg.input(input_file).hflip().output(output_file).run()
- Flip vertically: ffmpeg.input(input_file).vflip().output(output_file).run()
- Rotate 90 degrees: ffmpeg.input(input_file).filter('transpose', 1).output(output_file).run()
- Adjust brightness: ffmpeg.input(input_file).filter('eq', brightness=0.2).output(output_file).run()
- Adjust contrast: ffmpeg.input(input_file).filter('eq', contrast=1.5).output(output_file).run()
- Adjust saturation: ffmpeg.input(input_file).filter('eq', saturation=1.5).output(output_file).run()
- Convert to grayscale: ffmpeg.input(input_file).filter('colorchannelmixer', rr=0.3, rg=0.59, rb=0.11, gr=0.3, gg=0.59, gb=0.11, br=0.3, bg=0.59, bb=0.11).output(output_file).run()
- Crop video: ffmpeg.input(input_file).filter('crop', width, height, x, y).output(output_file).run()
- Scale video/image: ffmpeg.input(input_file).filter('scale', width, height).output(output_file).run()
- Add blur: ffmpeg.input(input_file).filter('boxblur', 2).output(output_file).run()

SCRIPT TEMPLATE:
```python
import ffmpeg
import sys
import os

def main():
    if len(sys.argv) != 3:
        print("Usage: python script.py <input_file> <output_file>")
        sys.exit(1)
    
    input_file = sys.argv[1]
    output_file = sys.argv[2]
    
    if not os.path.exists(input_file):
        print(f"Error: Input file '{input_file}' not found")
        sys.exit(1)
    
    try:
        # Your ffmpeg processing code here
        stream = ffmpeg.input(input_file)
        # Apply transformations...
        stream = stream.output(output_file)
        ffmpeg.run(stream, overwrite_output=True)
        print(f"Successfully processed {input_file} -> {output_file}")
    except ffmpeg.Error as e:
        print(f"FFmpeg error: {e}")
        if hasattr(e, 'stderr') and e.stderr:
            print(f"FFmpeg stderr: {e.stderr.decode('utf-8')}")
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected error: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
```

Remember: Use ffmpeg-python syntax, not command-line ffmpeg! Handle both video and image inputs gracefully.
"""

        # Build the user prompt
        if original_script:
            user_prompt = f"""This is an AMENDMENT task. Modify the existing FFmpeg script below according to the new requirements.

ORIGINAL SCRIPT:
{original_script}

NEW REQUIREMENTS: {prompt}

Please modify the script to incorporate the new requirements while keeping the overall structure intact.
"""
        else:
            user_prompt = f"""Create a complete Python script using ffmpeg-python to process a media file.

TASK: {prompt}
INPUT FILE: {input_file}
OUTPUT FILE: {output_file}

The script should take the input file (video or image) and apply the requested transformation to create the output file.
For image processing, ensure the output maintains appropriate quality and format.
"""

        if last_error:
            user_prompt += f"""

PREVIOUS ATTEMPT FAILED WITH ERROR:
{last_error}

Please fix the issues in the previous attempt.
"""

        if last_generated_script and not original_script:
            user_prompt += f"""

PREVIOUS GENERATED SCRIPT:
{last_generated_script}
"""

        final_prompt = f"{system_prompt}\\n\\n{user_prompt}"
        
        try:
            if USE_VERTEX_AI:
                response = self.vertex_client.models.generate_content(
                    model=FFMPEG_CODE_MODEL,
                    contents=final_prompt
                )
                generated_code = response.text.strip()
            else:
                response = self.model.generate_content(final_prompt)
                generated_code = response.text.strip()
            
            # Clean up potential markdown code blocks
            if generated_code.startswith("```python"):
                generated_code = generated_code[9:]  # Remove ```python
            if generated_code.startswith("```"):
                generated_code = generated_code[3:]  # Remove ```
            if generated_code.endswith("```"):
                generated_code = generated_code[:-3]  # Remove trailing ```
            
            return generated_code.strip()
        except Exception as e:
            run_logger.error(f"FFMPEG PLUGIN: LLM generation failed: {e}")
            raise

    def _run_ffmpeg_script(self, script_filename: str, input_file: str, output_filename: str, asset_unit_path: str, run_logger: logging.Logger):
        # Use the same Python executable that's running the main application
        python_executable = sys.executable
        script_path = os.path.join(asset_unit_path, script_filename)
        
        # Create the full output path 
        output_file_path = os.path.join(asset_unit_path, output_filename)
        
        command = [
            python_executable, script_path, input_file, output_file_path
        ]
        run_logger.debug(f"FFMPEG PLUGIN: Executing command: {' '.join(command)}")
        run_logger.debug(f"FFMPEG PLUGIN: Python executable: {python_executable}")
        run_logger.debug(f"FFMPEG PLUGIN: Input file: {input_file}")
        run_logger.debug(f"FFMPEG PLUGIN: Output file: {output_file_path}")
        
        # Run with the current working directory (not asset_unit_path) to avoid path issues
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=300
        )
        
        # Log stdout and stderr for debugging
        if result.stdout:
            run_logger.debug(f"FFMPEG PLUGIN: Script stdout: {result.stdout}")
        if result.stderr:
            run_logger.debug(f"FFMPEG PLUGIN: Script stderr: {result.stderr}")
            
        # Check for errors
        if result.returncode != 0:
            error_msg = f"Script exited with code {result.returncode}"
            if result.stderr:
                error_msg += f". Stderr: {result.stderr}"
            if result.stdout:
                error_msg += f". Stdout: {result.stdout}"
            raise subprocess.CalledProcessError(result.returncode, command, output=result.stdout, stderr=result.stderr)
            
    def _cleanup(self, asset_unit_path: str):
        # Clean up any temporary script files created during processing
        for file in os.listdir(asset_unit_path):
            if file.startswith("ffmpeg_script_attempt"):
                file_path = os.path.join(asset_unit_path, file)
                if os.path.exists(file_path):
                    os.remove(file_path)
