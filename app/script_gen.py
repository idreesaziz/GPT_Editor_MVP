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
from .utils import Timer # <-- IMPORT TIMER

load_dotenv()
logger = logging.getLogger(__name__) # Keep for general logging
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

def _populate_sandbox_from_source(sandbox_path: str, asset_logs: List[Dict[str, Any]], source_path: str, run_logger: logging.Logger):
    """
    Populates a sandbox by copying real asset files from the session source path.
    """
    run_logger.debug(f"Populating sandbox {sandbox_path} by copying files from {source_path}")
    for asset in asset_logs:
        filename = asset.get("filename")
        if not filename:
            continue
        
        source_file = os.path.join(source_path, filename)
        dest_file = os.path.join(sandbox_path, filename)
        
        if os.path.exists(source_file):
            try:
                shutil.copy2(source_file, dest_file)
                run_logger.debug(f"Copied '{source_file}' to sandbox.")
            except Exception as e:
                run_logger.warning(f"Failed to copy '{source_file}' to sandbox: {e}")
        else:
            run_logger.warning(f"Asset for sandbox not found at source: {source_file}")


def generate_validated_script(
    task: str, 
    plugin: ToolPlugin, 
    context: Dict[str, Any], 
    inputs: Dict, 
    outputs: Dict,
    asset_logs: List[Dict[str, Any]],
    session_path: str,
    run_logger: logging.Logger,
) -> str:
    run_logger.info("-" * 20 + " SCRIPT GENERATION " + "-" * 20)
    run_logger.info(f"SCRIPT_GEN: Task: '{task}' | Plugin: '{plugin.name}'")
    
    feedback_str = ""
    last_attempt_errors = {}

    with Timer(run_logger, f"Total Script Generation for '{task}'"):
        for attempt in range(MAX_RETRIES):
            run_logger.info(f"SCRIPT_GEN: Generation attempt {attempt + 1}/{MAX_RETRIES}")

            with tempfile.TemporaryDirectory() as sandbox_path:
                with Timer(run_logger, f"Sandbox Population (Attempt {attempt + 1})", level=logging.DEBUG):
                    _populate_sandbox_from_source(sandbox_path, asset_logs, session_path, run_logger)
                
                user_content = USER_CONTENT_TEMPLATE.format(
                    task=task,
                    inputs=str(inputs),
                    outputs=str(outputs),
                    context=str(context),
                    script_history=context.get("script_history", "No history available.")
                )
                system_instruction = plugin.get_system_instruction()
                full_prompt = f"{system_instruction}\n\n{user_content}"
                if feedback_str:
                    full_prompt += f"\n\n{feedback_str}"

                run_logger.debug(f"--- SCRIPT_GEN PROMPT (Attempt {attempt+1}) ---\n{full_prompt}\n--- END SCRIPT_GEN PROMPT ---")
                
                try:
                    with Timer(run_logger, f"Gemini API Call (Attempt {attempt + 1})"):
                        candidate_count = CANDIDATES_FIRST if attempt == 0 else CANDIDATES_RETRY
                        iter_generation_config = generation_config.copy()
                        iter_generation_config["candidate_count"] = candidate_count
                        response = model.generate_content([full_prompt], generation_config=iter_generation_config)
                    if not response.candidates: raise ValueError("Gemini API returned no candidates.")
                except Exception as e:
                    run_logger.error(f"Error calling Gemini API on attempt {attempt + 1}: {e}")
                    if attempt < MAX_RETRIES - 1: continue
                    raise ConnectionError(f"Failed to communicate with Gemini API.") from e

                attempt_errors = {}
                for i, candidate in enumerate(response.candidates):
                    if not candidate.content.parts:
                        run_logger.warning(f"Candidate {i+1} has no content parts, skipping.")
                        continue
                    
                    script_content_raw = candidate.content.parts[0].text.strip().removeprefix("```python").removesuffix("```").strip()
                    
                    with Timer(run_logger, f"Script Validation (Attempt {attempt + 1}, Candidate {i + 1})"):
                        is_valid, error_msg = plugin.validate_script(script_content_raw, sandbox_path, inputs, outputs)
                    
                    if is_valid:
                        run_logger.info("SCRIPT_GEN: Candidate script PASSED validation.")
                        run_logger.info("-" * 51)
                        # Construct the final script with I/O definitions only AFTER successful validation
                        inputs_definition = f"inputs = {json.dumps(inputs)}"
                        outputs_definition = f"outputs = {json.dumps(outputs)}"
                        full_script_content = f"{inputs_definition}\n{outputs_definition}\n\n{script_content_raw}"
                        run_logger.debug(f"--- FINAL SCRIPT ---\n{full_script_content}\n--- END FINAL SCRIPT ---")
                        return full_script_content
                    else:
                        run_logger.warning(f"SCRIPT_GEN: Candidate {i+1} FAILED validation: {error_msg}")
                        attempt_errors[f"Candidate {i+1}"] = {"error": str(error_msg), "code": script_content_raw}
                
                last_attempt_errors = attempt_errors
                feedback_parts = ["# FEEDBACK\n# The previous script(s) failed validation. Analyze the error(s) and provide a new, corrected script."]
                for name, details in attempt_errors.items():
                    feedback_parts.append(f"\n# --- {name} ---")
                    feedback_parts.append(f"# FAILED SCRIPT:\n# " + "\n# ".join(details['code'].split('\n')))
                    feedback_parts.append(f"# ERROR MESSAGE:\n# " + "\n# ".join(details['error'].strip().split('\n')))
                feedback_str = "\n".join(feedback_parts)
    
    error_report = f"Failed to generate a valid script for task '{task}' after {MAX_RETRIES} retries.\nLast errors: {last_attempt_errors}"
    run_logger.error(error_report)
    raise ValueError(error_report)