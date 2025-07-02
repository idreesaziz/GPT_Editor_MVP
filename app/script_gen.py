import os
import subprocess
import logging
import google.generativeai as genai
from dotenv import load_dotenv
import ast
import platform
import tempfile
import shutil
import sys
from typing import List

# --- Engine Imports ---
from .prompts import USER_CONTENT_TEMPLATE
from .plugins.base import ToolPlugin
from .plugins.ffmpeg_plugin import FFmpegPlugin

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

# --- Plugin Management ---
# The engine's registry of available tools.
# In the future, this could be discovered dynamically.
PLUGIN_REGISTRY: List[ToolPlugin] = [
    FFmpegPlugin(),
]

def _select_plugin(prompt: str, plugins: List[ToolPlugin]) -> ToolPlugin:
    """
    Selects the most appropriate plugin for the given prompt.
    
    For now, with only one plugin, it always returns that plugin.
    In the future, this will use an LLM call to decide.
    """
    # TODO: Implement LLM-based selection when multiple plugins exist.
    if not plugins:
        raise ValueError("No plugins are registered.")
    
    logger.info(f"Plugin selection stub: Defaulting to first registered plugin '{plugins[0].name}'")
    return plugins[0]


# --- Constants for iterative generation ---
MAX_RETRIES = 3
CANDIDATES_FIRST = 1
CANDIDATES_RETRY = 3

# Configure Gemini API
API_KEY = os.environ.get("GOOGLE_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable is required.")
genai.configure(api_key=API_KEY)
MODEL_NAME = "gemini-2.5-pro" # Using a capable model
generation_config = {"temperature": 0.2, "top_p": 1, "top_k": 1}
model = genai.GenerativeModel(model_name=MODEL_NAME, generation_config=generation_config)

def generate_edit_script(prompt: str) -> str:
    """
    Selects a plugin, then generates and validates a script to perform a video edit.
    Retries generation with feedback upon validation failure.
    """
    logger.debug(f"Entering generate_edit_script with prompt: '{prompt}'")
    
    # 1. Select the appropriate plugin for the task
    try:
        selected_plugin = _select_plugin(prompt, PLUGIN_REGISTRY)
        logger.info(f"Selected plugin: {selected_plugin.name}")
    except Exception as e:
        logger.error(f"Failed to select a plugin: {e}")
        raise ValueError(f"Could not find a suitable plugin for the prompt: {e}") from e

    feedback_str = ""
    last_attempt_errors = {}

    for attempt in range(MAX_RETRIES):
        logger.info(f"Generation attempt {attempt + 1}/{MAX_RETRIES} using {selected_plugin.name}")
        
        candidate_count = CANDIDATES_FIRST if attempt == 0 else CANDIDATES_RETRY
        
        # 2. Assemble the prompt using the selected plugin's instructions
        system_instruction = selected_plugin.get_system_instruction()
        user_content = USER_CONTENT_TEMPLATE.format(prompt=prompt)
        full_prompt = f"{system_instruction}\n\n{user_content}"
        if feedback_str:
            full_prompt += f"\n\n{feedback_str}"
        logger.debug(f"--- Full prompt for attempt {attempt + 1} ---\n{full_prompt}\n--- End of prompt ---")

        try:
            iter_generation_config = generation_config.copy()
            iter_generation_config["candidate_count"] = candidate_count
            response = model.generate_content([{"role": "user", "parts": [{"text": full_prompt}]}], generation_config=iter_generation_config)
            if not response.candidates:
                raise ValueError("Gemini API returned no candidates.")
        except Exception as e:
            logger.error(f"Error calling Gemini API on attempt {attempt + 1}: {e}")
            if attempt < MAX_RETRIES - 1: continue
            raise ConnectionError(f"Failed to communicate with Gemini API after {MAX_RETRIES} attempts.") from e

        attempt_errors = {}
        for i, candidate in enumerate(response.candidates):
            logger.debug(f"--- Processing Candidate {i+1}/{len(response.candidates)} ---")
            if not candidate.content.parts:
                logger.warning(f"Candidate {i+1} has no content, skipping.")
                continue
            
            raw_script_content = candidate.content.parts[0].text
            script_content = raw_script_content.strip().removeprefix("```python").removesuffix("```").strip()
            
            # 3. Validate the script using the selected plugin's self-contained validator
            is_valid, error_msg = selected_plugin.validate_script(script_content)
            
            if is_valid:
                logger.info(f"Candidate {i+1} passed validation via {selected_plugin.name}. Script generation successful.")
                return script_content
            else:
                logger.warning(f"Candidate {i+1} failed validation: {error_msg}")
                attempt_errors[f"Candidate {i+1}"] = {"error": error_msg, "code": script_content}
        
        last_attempt_errors = attempt_errors
        feedback_parts = [
            "# FEEDBACK",
            "# The previous script(s) were invalid. Analyze the error and provide a new, valid script.",
        ]
        for name, details in attempt_errors.items():
            feedback_parts.append(f"\n# --- {name} ---")
            feedback_parts.append("# FAILED SCRIPT:")
            feedback_parts.append("\n".join([f"# {line}" for line in details['code'].split('\n')]))
            feedback_parts.append("# ERROR MESSAGE:")
            feedback_parts.append("\n".join([f"# {line}" for line in details['error'].strip().split('\n')]))

        feedback_str = "\n".join(feedback_parts)

    error_report = f"Failed to generate a valid {selected_plugin.name} script after all retries.\nLast attempt errors:\n"
    for name, details in last_attempt_errors.items():
        error_report += f"- {name}: {details['error']}\n---\n{details['code']}\n---\n"
    
    logger.error(error_report)
    raise ValueError(error_report)