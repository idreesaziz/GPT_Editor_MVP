# app/plugins/manim_plugin.py

import logging
import os
import shutil
import subprocess
import time
from typing import Dict, Optional

import google.generativeai as genai

from .base import ToolPlugin

# --- Configuration ---
MANIM_CODE_MODEL = "gemini-2.5-flash"
MAX_CODE_GEN_RETRIES = 3

# --- Custom Exception ---
class ManimGenerationError(Exception):
    """Custom exception for errors during Manim asset generation."""
    pass

# --- Plugin Definition ---
class ManimAnimationGenerator(ToolPlugin):
    """
    A plugin that generates animated videos using Manim.
    It internally uses an LLM to write Manim Python code, executes it,
    and can iteratively refine the code if errors occur.
    It always renders with a transparent background into a .mov file.
    """

    def __init__(self):
        super().__init__()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable not found or not set.")
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(MANIM_CODE_MODEL)

    @property
    def name(self) -> str:
        return "Manim Animation Generator"

    @property
    def description(self) -> str:
        return (
            "Generates animated videos from a text description of the animation. "
            "Use this for creating explainers, visualizing data, animating text or shapes, "
            "and creating motion graphics. The output is always a .mov video file, "
            "which supports transparency for overlays."
        )

    def execute_task(self, task_details: Dict, session_path: str, run_logger: logging.Logger) -> str:
        """
        Executes the full lifecycle of Manim animation generation.
        """
        prompt = task_details["task"]
        output_filename = task_details["output_filename"]
        original_asset_filename = task_details.get("original_asset_filename")
        
        run_logger.info(f"MANIM PLUGIN: Starting task - '{prompt[:100]}...'. Always rendering with transparency.")

        last_error = None
        generated_code = None

        for attempt in range(MAX_CODE_GEN_RETRIES):
            run_logger.info(f"MANIM PLUGIN: Code generation/execution attempt {attempt + 1}/{MAX_CODE_GEN_RETRIES}.")

            try:
                generated_code = self._generate_manim_code(
                    prompt=prompt,
                    original_asset_filename=original_asset_filename,
                    session_path=session_path,
                    last_generated_code=generated_code,
                    last_error=last_error,
                    run_logger=run_logger
                )
            except Exception as e:
                run_logger.error(f"MANIM PLUGIN: LLM code generation failed: {e}", exc_info=True)
                raise ManimGenerationError(f"LLM call for Manim code generation failed: {e}") from e

            script_filename = f"manim_script_{os.path.splitext(output_filename)[0]}_attempt{attempt+1}.py"
            script_path = os.path.join(session_path, script_filename)
            with open(script_path, "w") as f:
                f.write(generated_code)

            try:
                run_logger.info(f"MANIM PLUGIN: Executing Manim script: {script_filename}")
                self._run_manim_script(script_filename, session_path, run_logger)

                # Find the newest video file (.mov)
                found_video_path = None
                newest_time = 0
                for root, _, files in os.walk(session_path):
                    for file in files:
                        if file.lower().endswith('.mov'):
                            file_path = os.path.join(root, file)
                            file_mod_time = os.path.getmtime(file_path)
                            if file_mod_time > newest_time:
                                newest_time = file_mod_time
                                found_video_path = file_path

                if found_video_path:
                    run_logger.info(f"MANIM PLUGIN: Found generated video at '{found_video_path}'.")
                    final_output_path = os.path.join(session_path, output_filename)
                    shutil.move(found_video_path, final_output_path)
                    run_logger.info(f"MANIM PLUGIN: Renamed to '{final_output_path}'.")
                    
                    media_dir = os.path.join(session_path, "media")
                    if os.path.exists(media_dir):
                        shutil.rmtree(media_dir)
                        run_logger.info(f"MANIM PLUGIN: Cleaned up media directory.")

                    run_logger.info(f"MANIM PLUGIN: Successfully generated asset '{output_filename}'.")
                    successful_script_name = f"manim_script_{os.path.splitext(output_filename)[0]}.py"
                    os.rename(script_path, os.path.join(session_path, successful_script_name))
                    return output_filename
                else:
                    last_error = "Manim execution finished, but no .mov file was found in the session directory."
                    run_logger.warning(f"MANIM PLUGIN: {last_error}")

            except subprocess.CalledProcessError as e:
                last_error = f"Manim execution failed with exit code {e.returncode}.\nStderr:\n{e.stderr}"
                run_logger.warning(f"MANIM PLUGIN: Manim execution failed. Error:\n{e.stderr}")

        final_error_msg = f"MANIM PLUGIN: Failed to generate a valid Manim animation for prompt '{prompt}' after {MAX_CODE_GEN_RETRIES} attempts. Last known error: {last_error}"
        run_logger.error(final_error_msg)
        raise ManimGenerationError(final_error_msg)


    def _generate_manim_code(self, prompt: str, original_asset_filename: Optional[str], session_path: str, last_generated_code: Optional[str], last_error: Optional[str], run_logger: logging.Logger) -> str:
        # This function remains the same.
        system_prompt = """
You are an expert Manim developer. Your task is to write a complete, self-contained Python script to generate a single Manim animation.

CRITICAL RULES:
1.  The script must import all necessary components from `manim`.
2.  The script must define a single class named `GeneratedScene` that inherits from `manim.Scene`.
3.  All animation logic MUST be inside the `construct(self)` method of the `GeneratedScene` class.
4.  **If the user asks for a specific background color, add `self.camera.background_color = <COLOR>` at the start of the `construct` method. Otherwise, DO NOT set a background color, as it will be rendered transparently.**
5.  Do NOT include any code to render the scene (e.g., `if __name__ == "__main__"`)
6.  If you need to use an external asset like an image, its filename will be provided. Assume it exists in the same directory where the script is run. Use `manim.ImageMobject("filename.png")`.
7.  Your entire response MUST be just the Python code, with no explanations, markdown, or other text.
"""
        user_content = []
        original_script = None
        if original_asset_filename and not last_error:
            original_script_name = f"manim_script_{os.path.splitext(original_asset_filename)[0]}.py"
            original_script_path = os.path.join(session_path, original_script_name)
            try:
                with open(original_script_path, 'r') as f:
                    original_script = f.read()
                user_content.append("You are modifying an existing animation. Here is the original Manim script:")
                user_content.append("--- ORIGINAL SCRIPT ---")
                user_content.append(original_script)
                user_content.append("--- END ORIGINAL SCRIPT ---")
                user_content.append("\nYour task is to modify this script based on the following instruction:")
                user_content.append(f"Instruction: '{prompt}'")
            except FileNotFoundError:
                run_logger.warning(f"MANIM PLUGIN: Original script '{original_script_name}' not found for modification. Treating as a new generation request.")
        if last_error:
            user_content.append("You are fixing a script that failed to execute. Here is the code that failed:")
            user_content.append("--- FAILED SCRIPT ---")
            user_content.append(last_generated_code)
            user_content.append("--- END FAILED SCRIPT ---")
            user_content.append("\nIt failed with the following error:")
            user_content.append("--- ERROR MESSAGE ---")
            user_content.append(last_error)
            user_content.append("--- END ERROR MESSAGE ---")
            user_content.append("\nPlease fix the script to resolve the error while still fulfilling the original request:")
            user_content.append(f"Original Request: '{prompt}'")
        if not user_content:
            user_content.append("Your task is to write a new Manim script based on the following instruction:")
            user_content.append(f"Instruction: '{prompt}'")
        user_content.append("\nRemember, your response must be only the complete, corrected Python code for the `GeneratedScene` class.")
        final_prompt = f"{system_prompt}\n\n{''.join(user_content)}"
        run_logger.debug(f"--- MANIM PLUGIN LLM PROMPT ---\n{final_prompt}\n--- END ---")
        response = self.model.generate_content(final_prompt)
        cleaned_code = response.text.strip()
        if cleaned_code.startswith("```python"):
            cleaned_code = cleaned_code[9:]
        if cleaned_code.startswith("```"):
            cleaned_code = cleaned_code[3:]
        if cleaned_code.endswith("```"):
            cleaned_code = cleaned_code[:-3]
        return cleaned_code.strip()

    def _run_manim_script(self, script_filename: str, session_path: str, run_logger: logging.Logger):
        """
        Executes a Manim script using subprocess, always rendering with a transparent background.
        """
        # --- CHANGE HERE ---
        # We now use '-ql' for low quality to speed up previews and testing.
        # This will be parameterized later when we implement a full preview system.
        command = [
            "manim",
            "-t", # ALWAYS render with a transparent background
            "-q", "l", # Set quality to LOW for fast previews
            "--format", "mov", # ALWAYS output a .mov file
            script_filename,
            "GeneratedScene",
        ]
        
        run_logger.debug(f"MANIM PLUGIN: Executing command: {' '.join(command)}")
        
        result = subprocess.run(
            command,
            cwd=session_path,
            capture_output=True,
            text=True,
            check=True,
            timeout=300
        )
        run_logger.debug(f"MANIM PLUGIN STDOUT:\n{result.stdout}")
        run_logger.debug(f"MANIM PLUGIN STDERR:\n{result.stderr}")