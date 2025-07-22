# app/plugins/manim_plugin.py

import logging
import os
import shutil
import subprocess
import time
import json
from typing import Dict, Optional

import google.generativeai as genai

from .base import ToolPlugin

# --- Configuration ---
MANIM_CODE_MODEL = "gemini-1.5-flash"
MAX_CODE_GEN_RETRIES = 3

# --- Custom Exception ---
class ManimGenerationError(Exception):
    """Custom exception for errors during Manim asset generation."""
    pass

# --- Plugin Definition ---
class ManimAnimationGenerator(ToolPlugin):
    """
    A plugin that generates animated videos using Manim.
    It creates a companion .meta.json file for each generated asset,
    containing the source code needed for future amendments.
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
            "Generates animated videos from a text description (e.g., titles, explainers). "
            "The output is always a .mov file with a transparent background, suitable for overlays. "
            "IMPORTANT BEHAVIOR: For speed, this plugin currently renders all animations as low-resolution previews (e.g., 480p). "
            "The composition step will need to scale these assets up to fit the final video frame."
        )

    def execute_task(self, task_details: Dict, session_path: str, run_logger: logging.Logger) -> str:
        prompt = task_details["task"]
        output_filename = task_details["output_filename"]
        original_asset_filename = task_details.get("original_asset_filename")
        
        run_logger.info(f"MANIM PLUGIN: Starting task - '{prompt[:100]}...'.")

        last_error = None
        generated_code = None
        original_code = None

        if original_asset_filename:
            run_logger.info(f"MANIM PLUGIN: Amendment mode detected. Base asset: {original_asset_filename}")
            meta_path = os.path.join(session_path, f"{os.path.splitext(original_asset_filename)[0]}.meta.json")
            try:
                with open(meta_path, 'r') as f:
                    meta_data = json.load(f)
                    original_code = meta_data.get("plugin_data", {}).get("source_code")
                if not original_code:
                    run_logger.warning(f"MANIM PLUGIN: Metadata for {original_asset_filename} found, but no source code. Proceeding as new generation.")
            except (FileNotFoundError, json.JSONDecodeError) as e:
                run_logger.warning(f"MANIM PLUGIN: Could not load metadata for {original_asset_filename}: {e}. Proceeding as new generation.")

        for attempt in range(MAX_CODE_GEN_RETRIES):
            run_logger.info(f"MANIM PLUGIN: Code generation attempt {attempt + 1}/{MAX_CODE_GEN_RETRIES}.")
            try:
                generated_code = self._generate_manim_code(
                    prompt=prompt,
                    original_code=original_code,
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

                found_video_path = self._find_latest_video(session_path)
                if found_video_path:
                    run_logger.info(f"MANIM PLUGIN: Found generated video at '{found_video_path}'.")
                    final_output_path = os.path.join(session_path, output_filename)
                    shutil.move(found_video_path, final_output_path)
                    
                    # --- CHANGE: Call the base class method to create the metadata file ---
                    manim_plugin_data = {"source_code": generated_code}
                    self._create_metadata_file(task_details, session_path, manim_plugin_data)
                    
                    self._cleanup(session_path)
                    run_logger.info(f"MANIM PLUGIN: Successfully generated asset '{output_filename}'.")
                    # No need to save the script separately anymore, it's in the metadata
                    return output_filename
                else:
                    last_error = "Manim execution finished, but no video file was found in the session directory."
                    run_logger.warning(f"MANIM PLUGIN: {last_error}")

            except subprocess.CalledProcessError as e:
                last_error = f"Manim execution failed with exit code {e.returncode}.\nStderr:\n{e.stderr}"
                run_logger.warning(f"MANIM PLUGIN: Manim execution failed. Error:\n{e.stderr}")
            finally:
                if os.path.exists(script_path):
                    os.remove(script_path)


        final_error_msg = f"MANIM PLUGIN: Failed to generate a valid Manim animation after {MAX_CODE_GEN_RETRIES} attempts. Last error: {last_error}"
        run_logger.error(final_error_msg)
        raise ManimGenerationError(final_error_msg)


    def _generate_manim_code(self, prompt: str, original_code: Optional[str], last_generated_code: Optional[str], last_error: Optional[str], run_logger: logging.Logger) -> str:
        system_prompt = """
You are an expert Manim developer. Your task is to write a complete, self-contained Python script to generate a single Manim animation.

CRITICAL RULES:
1.  The script must import all necessary components from `manim`.
2.  The script must define a single class named `GeneratedScene` that inherits from `manim.Scene`.
3.  All animation logic MUST be inside the `construct(self)` method of the `GeneratedScene` class.
4.  **AESTHETICS & LAYOUT:** Strive for clean, modern animations. All text and primary visual elements MUST be placed and scaled to be fully visible within the video frame. Use alignment methods like `.move_to(ORIGIN)` or `.to_edge()` to ensure proper composition.
5.  **BACKGROUND:** If the user asks for a specific background color, add `self.camera.background_color = <COLOR>` at the start of the `construct` method. Otherwise, DO NOT set a background color, as it will be rendered transparently.
6.  Do NOT include any code to render the scene (e.g., `if __name__ == "__main__"`)
7.  If you need to use an external asset like an image, its filename will be provided. Assume it exists in the same directory where the script is run. Use `manim.ImageMobject("filename.png")`.
8.  Your entire response MUST be just the Python code, with no explanations, markdown, or other text.
"""
        user_content = []
        if original_code and not last_error:
            user_content.append("You are modifying an existing animation. Here is the original Manim script:")
            user_content.append(f"--- ORIGINAL SCRIPT ---\n{original_code}\n--- END ORIGINAL SCRIPT ---")
            user_content.append(f"\nYour task is to modify this script based on the following instruction:\nInstruction: '{prompt}'")
        elif last_error:
            user_content.append("You are fixing a script that failed to execute. Here is the code that failed:")
            user_content.append(f"--- FAILED SCRIPT ---\n{last_generated_code}\n--- END FAILED SCRIPT ---")
            user_content.append(f"\nIt failed with the following error:\n--- ERROR MESSAGE ---\n{last_error}\n--- END ERROR MESSAGE ---")
            user_content.append(f"\nPlease fix the script to resolve the error while still fulfilling the original request:\nOriginal Request: '{prompt}'")
        else:
            user_content.append(f"Your task is to write a new Manim script based on the following instruction:\nInstruction: '{prompt}'")
        
        user_content.append("\nRemember, your response must be only the complete, corrected Python code for the `GeneratedScene` class.")
        final_prompt = f"{system_prompt}\n\n{''.join(user_content)}"
        run_logger.debug(f"--- MANIM PLUGIN LLM PROMPT ---\n{final_prompt}\n--- END ---")
        response = self.model.generate_content(final_prompt)
        cleaned_code = response.text.strip()
        if cleaned_code.startswith("```python"): cleaned_code = cleaned_code[9:]
        if cleaned_code.startswith("```"): cleaned_code = cleaned_code[3:]
        if cleaned_code.endswith("```"): cleaned_code = cleaned_code[:-3]
        return cleaned_code.strip()

    def _run_manim_script(self, script_filename: str, session_path: str, run_logger: logging.Logger):
        command = [
            "manim", "-t", "-q", "l", "--format", "mov",
            script_filename, "GeneratedScene",
        ]
        run_logger.debug(f"MANIM PLUGIN: Executing command: {' '.join(command)}")
        subprocess.run(
            command, cwd=session_path, capture_output=True, text=True, check=True, timeout=300
        )

    def _find_latest_video(self, session_path: str) -> Optional[str]:
        found_video_path, newest_time = None, 0
        search_dir = os.path.join(session_path, "media", "videos")
        if not os.path.isdir(search_dir): return None
        for root, _, files in os.walk(search_dir):
            for file in files:
                if file.lower().endswith('.mov'):
                    file_path = os.path.join(root, file)
                    file_mod_time = os.path.getmtime(file_path)
                    if file_mod_time > newest_time:
                        newest_time, found_video_path = file_mod_time, file_path
        return found_video_path
            
    def _cleanup(self, session_path: str):
        media_dir = os.path.join(session_path, "media")
        if os.path.exists(media_dir):
            shutil.rmtree(media_dir)