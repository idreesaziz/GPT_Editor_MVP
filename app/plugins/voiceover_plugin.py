# app/plugins/voiceover_plugin.py

import logging
import os
from typing import Dict, List

# Google Cloud TTS
from google.cloud import texttospeech_v1 as texttospeech

# Use absolute import for base class
from app.plugins.base import ToolPlugin

# --- Custom Exception ---
class VoiceoverGenerationError(Exception):
    """Custom exception for errors during voiceover asset generation."""
    pass

# --- Plugin Definition ---
class VoiceoverGenerator(ToolPlugin):
    """
    A plugin that generates a single, high-quality voiceover audio file
    from a given text script. This version does not perform any chunking or slicing.
    """

    def __init__(self):
        super().__init__()
        try:
            self.client = texttospeech.TextToSpeechClient()
        except Exception as e:
            logging.error(f"Failed to initialize Google Text-to-Speech client: {e}")
            raise ValueError("Google Cloud TTS client setup failed. Ensure GOOGLE_APPLICATION_CREDENTIALS is set.") from e

        self.voice_name = "en-US-Chirp3-HD-Achernar"
        self.language_code = "en-US"
        self.audio_encoding = texttospeech.AudioEncoding.MP3
        self.speaking_rate = 1.0
        self.pitch = 0.0

    @property
    def name(self) -> str:
        return "Voiceover Generator"

    @property
    def description(self) -> str:
        return (
            "Generates a single, high-quality voiceover audio file (.mp3) from a text script. "
            "This tool is best for short phrases or paragraphs that do not require precise "
            "synchronization of individual sentences to video."
        )

    def execute_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        full_script = task_details["task"]
        unit_id = task_details["unit_id"]
        output_filename = task_details.get("output_filename", "narration.mp3")

        run_logger.info(f"VOICEOVER PLUGIN: Starting task for unit '{unit_id}'.")
        run_logger.info(f"VOICEOVER PLUGIN: Script: '{full_script[:100]}...'")

        synthesis_input = texttospeech.SynthesisInput(text=full_script)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=self.language_code,
            name=self.voice_name
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=self.audio_encoding,
            speaking_rate=self.speaking_rate,
            pitch=self.pitch,
        )

        final_output_path = os.path.join(asset_unit_path, output_filename)

        try:
            run_logger.info("VOICEOVER PLUGIN: Calling Google Cloud TTS API...")
            response = self.client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )

            with open(final_output_path, "wb") as out:
                out.write(response.audio_content)
            
            run_logger.info(f"VOICEOVER PLUGIN: Successfully generated audio file at {final_output_path}")

            # Create the metadata file for the unit
            plugin_data = {"source_prompt": full_script}
            self._create_metadata_file(task_details, asset_unit_path, [output_filename], plugin_data)
            
            run_logger.info(f"VOICEOVER PLUGIN: Successfully generated asset unit '{unit_id}'.")
            
            # Return a list containing the single generated filename
            return [output_filename]

        except Exception as e:
            run_logger.error(f"VOICEOVER PLUGIN: Error during voiceover generation for unit '{unit_id}': {e}", exc_info=True)
            raise VoiceoverGenerationError(f"Failed to generate voiceover: {e}") from e