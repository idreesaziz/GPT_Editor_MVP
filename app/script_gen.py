import os
import logging
import google.generativeai as genai
from dotenv import load_dotenv
from typing import Dict, Any, List
import tempfile

from .prompts import USER_CONTENT_TEMPLATE
from .plugins.base import ToolPlugin
from . import sandbox_provider

# ... (Constants and Gemini config are unchanged) ...
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

def generate_validated_script(
    task: str, 
    plugin: ToolPlugin, 
    context: Dict[str, Any], 
    inputs: Dict, 
    outputs: Dict,
    asset_logs: List[Dict[str, Any]],
    session_path: str,
) -> str:
    # ... (Most of this function is the same, just the sandbox call changes) ...
    logger.debug(f"Generating script for task: '{task}' using plugin '{plugin.name}'")
    
    feedback_str = ""
    last_attempt_errors = {}

    for attempt in range(MAX_RETRIES):
        logger.info(f"Generation attempt {attempt + 1}/{MAX_RETRIES} for task '{task}'")

        # Create the high-fidelity sandbox for this validation attempt
        with tempfile.TemporaryDirectory() as sandbox_path:
            # The sandbox is populated based on the asset logs
            sandbox_provider.populate_sandbox(sandbox_path, asset_logs)
            
            # ... (Rest of the loop is unchanged) ...
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
                response = model.generate_content([{"role": "user", "parts": [{"text": full_prompt}]}], generation_config=iter_generation_config)
                if not response.candidates: raise ValueError("Gemini API returned no candidates.")
            except Exception as e:
                logger.error(f"Error calling Gemini API on attempt {attempt + 1}: {e}")
                if attempt < MAX_RETRIES - 1: continue
                raise ConnectionError(f"Failed to communicate with Gemini API.") from e

            attempt_errors = {}
            for i, candidate in enumerate(response.candidates):
                script_content = candidate.content.parts[0].text.strip().removeprefix("```python").removesuffix("```").strip()
                is_valid, error_msg = plugin.validate_script(script_content, sandbox_path)
                
                if is_valid:
                    logger.info(f"Candidate script passed validation for task '{task}'.")
                    return script_content
                else:
                    logger.warning(f"Candidate script failed validation: {error_msg}")
                    attempt_errors[f"Candidate {i+1}"] = {"error": error_msg, "code": script_content}
            
            last_attempt_errors = attempt_errors
            feedback_parts = ["# FEEDBACK\n# The previous script(s) failed validation. Analyze the error(s) and provide a new, corrected script."]
            for name, details in attempt_errors.items():
                feedback_parts.append(f"\n# --- {name} ---")
                feedback_parts.append(f"# FAILED SCRIPT:\n# " + "\n# ".join(details['code'].split('\n')))
                feedback_parts.append(f"# ERROR MESSAGE:\n# " + "\n# ".join(details['error'].strip().split('\n')))
            feedback_str = "\n".join(feedback_parts)
    
    error_report = f"Failed to generate a valid script for task '{task}' after {MAX_RETRIES} retries.\n"
    logger.error(error_report)
    raise ValueError(error_report)