import os
import logging
import google.generativeai as genai
from dotenv import load_dotenv
from typing import Dict, Any, List
import tempfile
import shutil
import json

from .prompts import USER_CONTENT_TEMPLATE
from .plugins.base import ToolPlugin

load_dotenv()
logger = logging.getLogger(__name__)
MAX_RETRIES = 3
CANDIDATES_FIRST = 1
CANDIDATES_RETRY = 3
API_KEY = os.environ.get("GOOGLE_API_KEY")
if not API_KEY:
    raise ValueError("GOOGLE_API_KEY environment variable is required.")
genai.configure(api_key=API_KEY)
MODEL_NAME = "gemini-2.5-pro"
generation_config = {"temperature": 0.2, "top_p": 1, "top_k": 1}
model = genai.GenerativeModel(model_name=MODEL_NAME, generation_config=generation_config)

def _populate_sandbox_from_source(sandbox_path: str, asset_logs: List[Dict[str, Any]], source_path: str):
    """
    Populates a sandbox by copying real asset files from the session source path.
    """
    logger.debug(f"Populating sandbox {sandbox_path} by copying files from {source_path}")
    for asset in asset_logs:
        filename = asset.get("filename")
        if not filename:
            continue
        
        source_file = os.path.join(source_path, filename)
        dest_file = os.path.join(sandbox_path, filename)
        
        if os.path.exists(source_file):
            try:
                shutil.copy2(source_file, dest_file) # copy2 preserves metadata
                logger.debug(f"Copied '{source_file}' to sandbox.")
            except Exception as e:
                logger.warning(f"Failed to copy '{source_file}' to sandbox: {e}")
        else:
            logger.warning(f"Asset for sandbox not found at source: {source_file}")


def generate_validated_script(
    task: str, 
    plugin: ToolPlugin, 
    context: Dict[str, Any], 
    inputs: Dict, 
    outputs: Dict,
    asset_logs: List[Dict[str, Any]],
    session_path: str,
) -> str:
    logger.debug(f"Generating script for task: '{task}' using plugin '{plugin.name}'")
    
    feedback_str = ""
    last_attempt_errors = {}

    for attempt in range(MAX_RETRIES):
        logger.info(f"Generation attempt {attempt + 1}/{MAX_RETRIES} for task '{task}'")

        with tempfile.TemporaryDirectory() as sandbox_path:
            _populate_sandbox_from_source(sandbox_path, asset_logs, session_path)
            
            user_content = USER_CONTENT_TEMPLATE.format(
                task=task, inputs=str(inputs), outputs=str(outputs),
                context=str(context),
                completed_steps_log=str(context.get("completed_steps_log", []))
            )
            system_instruction = plugin.get_system_instruction()
            full_prompt = f"{system_instruction}\n\n{user_content}"
            if feedback_str:
                full_prompt += f"\n\n{feedback_str}"
            
            try:
                candidate_count = CANDIDATES_FIRST if attempt == 0 else CANDIDATES_RETRY
                iter_generation_config = generation_config.copy()
                iter_generation_config["candidate_count"] = candidate_count
                response = model.generate_content([full_prompt], generation_config=iter_generation_config)
                if not response.candidates: raise ValueError("Gemini API returned no candidates.")
            except Exception as e:
                logger.error(f"Error calling Gemini API on attempt {attempt + 1}: {e}")
                if attempt < MAX_RETRIES - 1: continue
                raise ConnectionError(f"Failed to communicate with Gemini API.") from e

            attempt_errors = {}
            for i, candidate in enumerate(response.candidates):
                if not candidate.content.parts:
                    logger.warning(f"Candidate {i+1} has no content parts, skipping.")
                    continue
                
                script_content_raw = candidate.content.parts[0].text.strip().removeprefix("```python").removesuffix("```").strip()
                
                # --- FIX: Prepend the inputs and outputs dictionaries to the script ---
                # This makes the variables available in the script's execution scope.
                # We use json.dumps because its output is valid Python literal syntax.
                inputs_definition = f"inputs = {json.dumps(inputs)}"
                outputs_definition = f"outputs = {json.dumps(outputs)}"
                
                full_script_content = f"{inputs_definition}\n{outputs_definition}\n\n{script_content_raw}"
                # --- END FIX ---
                
                is_valid, error_msg = plugin.validate_script(full_script_content, sandbox_path)
                
                if is_valid:
                    logger.info(f"Candidate script passed validation for task '{task}'.")
                    # Return the full script with prepended definitions
                    return full_script_content
                else:
                    logger.warning(f"Candidate script {i+1} failed validation: {error_msg}")
                    # We log the raw script so the feedback loop isn't confused by our prepended code
                    attempt_errors[f"Candidate {i+1}"] = {"error": str(error_msg), "code": script_content_raw}
            
            last_attempt_errors = attempt_errors
            feedback_parts = ["# FEEDBACK\n# The previous script(s) failed validation. Analyze the error(s) and provide a new, corrected script."]
            for name, details in attempt_errors.items():
                feedback_parts.append(f"\n# --- {name} ---")
                feedback_parts.append(f"# FAILED SCRIPT:\n# " + "\n# ".join(details['code'].split('\n')))
                feedback_parts.append(f"# ERROR MESSAGE:\n# " + "\n# ".join(details['error'].strip().split('\n')))
            feedback_str = "\n".join(feedback_parts)
    
    error_report = f"Failed to generate a valid script for task '{task}' after {MAX_RETRIES} retries.\nLast errors: {last_attempt_errors}"
    logger.error(error_report)
    raise ValueError(error_report)