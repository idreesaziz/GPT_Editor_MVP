# app/plugins/music_plugin.py

import logging
import os
import time
import json
import requests
import shutil
import base64
from typing import Dict, List

from app.plugins.base import ToolPlugin

# --- Configuration ---
VERTEX_PROJECT_ID = os.getenv("VERTEX_PROJECT_ID")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "us-central1")

# --- DUMMY MODE SWITCH ---
MUSIC_DUMMY_MODE = os.getenv("MUSIC_DUMMY_MODE", "false").lower() == "true"
DUMMY_MUSIC_PATH = os.path.join(os.path.dirname(__file__), "dummy_music.txt")

LYRIA_MODEL_ID = "lyria-002"

# --- Custom Exception ---
class MusicGenerationError(Exception):
    """Custom exception for errors during music generation."""
    pass

# --- Plugin Definition ---
class MusicGenerator(ToolPlugin):
    """
    A plugin that generates a 30-second, loopable instrumental music track
    from a text prompt using Google's Lyria model on Vertex AI.
    """

    def __init__(self):
        super().__init__()
        if not MUSIC_DUMMY_MODE and not VERTEX_PROJECT_ID:
            raise ValueError("In non-dummy mode, VERTEX_PROJECT_ID environment variable must be set.")

    @property
    def name(self) -> str:
        return "AI Music Generator"

    @property
    def description(self) -> str:
        return (
            "Generates instrumental music from a text description of a genre, mood, or style "
            "(e.g., 'upbeat electronic pop', 'sad piano melody'). CRITICAL BEHAVIOR: This tool "
            "always produces a 30-second audio clip (.wav) that should be prompted to be loopable. "
            "It does not support custom durations."
        )

    def _execute_dummy_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        """Bypasses the API and provides a placeholder audio file for testing."""
        run_logger.info("MUSIC PLUGIN: --- DUMMY MODE ENABLED ---")
        
        prompt = task_details["task"]
        output_filename = task_details.get("output_filename", "music.wav")
        final_output_path = os.path.join(asset_unit_path, output_filename)

        run_logger.info(f"MUSIC PLUGIN (DUMMY): Creating dummy WAV file at {final_output_path}")
        
        # Create a minimal valid WAV file (44-byte header + minimal audio data)
        wav_header = (
            b'RIFF'
            b'\x28\x00\x00\x00'  # File size - 8 (40 bytes)
            b'WAVE'
            b'fmt '
            b'\x10\x00\x00\x00'  # fmt chunk size (16)
            b'\x01\x00'          # Audio format (1 = PCM)
            b'\x01\x00'          # Number of channels (1)
            b'\x44\xac\x00\x00'  # Sample rate (44100)
            b'\x88\x58\x01\x00'  # Byte rate
            b'\x02\x00'          # Block align
            b'\x10\x00'          # Bits per sample (16)
            b'data'
            b'\x04\x00\x00\x00'  # Data chunk size (4 bytes)
            b'\x00\x00\x00\x00'  # Minimal audio data (4 bytes of silence)
        )
        
        with open(final_output_path, "wb") as f:
            f.write(wav_header)
        
        time.sleep(0.5)

        plugin_data = {
            "source_prompt": prompt,
            "is_dummy": True
        }
        self._create_metadata_file(
            task_details=task_details,
            asset_unit_path=asset_unit_path,
            child_assets=[output_filename],
            plugin_data=plugin_data
        )
        
        run_logger.info(f"MUSIC PLUGIN (DUMMY): Successfully generated dummy asset unit '{task_details['unit_id']}'.")
        return [output_filename]

    def execute_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        if MUSIC_DUMMY_MODE:
            return self._execute_dummy_task(task_details, asset_unit_path, run_logger)

        prompt = task_details["task"]
        unit_id = task_details["unit_id"]
        output_filename = task_details.get("output_filename", "music.wav")

        run_logger.info(f"MUSIC PLUGIN: Starting task for unit '{unit_id}'.")
        run_logger.info(f"MUSIC PLUGIN: Prompt: '{prompt[:100]}...'")

        try:
            from google.auth import default
            from google.auth.transport.requests import Request
            
            run_logger.info("MUSIC PLUGIN: Authenticating with Google Cloud...")
            scopes = ['https://www.googleapis.com/auth/cloud-platform']
            credentials, project = default(scopes=scopes)
            
            if not credentials.valid:
                run_logger.info("MUSIC PLUGIN: Refreshing credentials...")
                credentials.refresh(Request())
            
            access_token = credentials.token
            if not access_token:
                raise MusicGenerationError("No access token available after authentication")
            
            run_logger.info("MUSIC PLUGIN: Authentication successful.")
            
            # Prepare request payload according to official Lyria API documentation
            request_payload = {
                "instances": [
                    {
                        "prompt": prompt
                    }
                ],
                "parameters": {
                    "sample_count": 1
                }
            }
            
            predict_url = f"https://{VERTEX_LOCATION}-aiplatform.googleapis.com/v1/projects/{VERTEX_PROJECT_ID}/locations/{VERTEX_LOCATION}/publishers/google/models/{LYRIA_MODEL_ID}:predict"
            headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json; charset=utf-8"}
            
            run_logger.info("MUSIC PLUGIN: Submitting music generation request to Vertex AI...")
            response = requests.post(predict_url, headers=headers, json=request_payload)
            response.raise_for_status()
            
            response_data = response.json()
            predictions = response_data.get("predictions", [])
            
            if not predictions:
                raise MusicGenerationError("Lyria API response contained no predictions.")
            
            # Get the audio content - Lyria API uses 'bytesBase64Encoded' field
            audio_content_base64 = predictions[0].get("bytesBase64Encoded")
            
            if not audio_content_base64:
                run_logger.error(f"MUSIC PLUGIN: bytesBase64Encoded not found. Available keys: {list(predictions[0].keys())}")
                raise MusicGenerationError("Prediction in Lyria API response did not contain 'bytesBase64Encoded'.")

            run_logger.info(f"MUSIC PLUGIN: Received audio data ({len(audio_content_base64)} characters). Decoding and saving...")
            
            # Decode the Base64 string to bytes
            audio_bytes = base64.b64decode(audio_content_base64)
            
            final_output_path = os.path.join(asset_unit_path, output_filename)
            with open(final_output_path, "wb") as f:
                f.write(audio_bytes)

            plugin_data = {"source_prompt": prompt, "is_dummy": False}
            self._create_metadata_file(
                task_details=task_details,
                asset_unit_path=asset_unit_path,
                child_assets=[output_filename],
                plugin_data=plugin_data
            )
            
            run_logger.info(f"MUSIC PLUGIN: Successfully generated asset unit '{unit_id}'.")
            return [output_filename]

        except requests.exceptions.HTTPError as e:
            error_text = e.response.text
            run_logger.error(f"MUSIC PLUGIN: HTTP Error: {e.response.status_code} - {error_text}", exc_info=True)
            raise MusicGenerationError(f"API request failed: {error_text}") from e
        except Exception as e:
            run_logger.error(f"MUSIC PLUGIN: Error during music generation for unit '{unit_id}': {e}", exc_info=True)
            raise MusicGenerationError(f"Failed to generate music with Lyria: {e}") from e

    def _create_metadata_file(self, task_details: Dict, asset_unit_path: str, child_assets: List[str], plugin_data: Dict):
        """Create metadata file for the generated assets."""
        metadata = {
            "plugin": "music",
            "task_details": task_details,
            "output_files": child_assets,
            "plugin_data": plugin_data,
            "generated_at": time.time()
        }
        
        metadata_path = os.path.join(asset_unit_path, "metadata.json")
        with open(metadata_path, "w") as f:
            json.dump(metadata, f, indent=2)