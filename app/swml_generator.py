# app/swml_generator.py
import logging
import google.generativeai as genai
import json
from typing import Dict, Any, List

from .utils import Timer
# We will embed the SWML spec in the prompt itself for now.
# In the future, this could come from a shared spec file.

logger = logging.getLogger(__name__)
GENERATOR_MODEL_NAME = "gemini-2.5-pro"
# It's good practice to give each "agent" its own model instance and system prompt
swml_model = genai.GenerativeModel(GENERATOR_MODEL_NAME)

def generate_swml(prompt: str, current_swml: Dict[str, Any], prompt_history: List[str], run_logger: logging.Logger) -> Dict[str, Any]:
    run_logger.info("=" * 20 + " SWML GENERATION " + "=" * 20)
    
    # The system instruction for this LLM call
    system_prompt = """
You are an expert AI assistant that generates and edits declarative video compositions in a JSON format called SWML.
Your task is to take a user's editing request and an existing SWML JSON object, and produce a new, modified SWML JSON object that reflects the user's desired changes.

**CRITICAL RULES:**
1.  Respond ONLY with a single, complete, valid JSON object representing the new SWML. Do not include any explanations, markdown, or other text.
2.  Preserve IDs of existing sources, tracks, and clips unless the user explicitly asks to remove or replace them.
3.  Work from the provided "Current SWML". Your output must be the full, new version of the SWML, not just a snippet.
4.  Ensure all paths in the `sources` list are just filenames, not full paths.
"""
    # Create a history of prompts for context
    formatted_history = "\n".join([f"- '{p}'" for p in prompt_history]) if prompt_history else "This is the initial version."

    user_prompt = f"""
**Full Project History (Previous Prompts):**
{formatted_history}

**Current SWML State:**
```json
{json.dumps(current_swml, indent=2)}
```

**New Composition Instruction:**
"{prompt}"

**Your Task:**
Generate the new, complete SWML file that incorporates the new composition instruction.
Your new SWML (JSON only):
"""
    
    with Timer(run_logger, "SWML Generation LLM Call & Parsing"):
        run_logger.debug(f"--- SWML GEN PROMPT ---\n{user_prompt}\n--- END ---")
        try:
            # We combine the system and user prompt for this API
            response = swml_model.generate_content(f"{system_prompt}\n{user_prompt}")
            raw_response_text = response.candidates[0].content.parts[0].text
            cleaned_response = raw_response_text.strip().removeprefix("```json").removesuffix("```").strip()
            new_swml = json.loads(cleaned_response)

            run_logger.info("SWML_GEN: Successfully generated and parsed new SWML.")
            return new_swml
        except (json.JSONDecodeError, ValueError) as e:
            run_logger.error(f"SWML Generator failed to create a valid plan. Error: {e}. Raw response:\n{cleaned_response if 'cleaned_response' in locals() else 'N/A'}")
            raise ValueError(f"The SWML Generator failed to create valid JSON. Error: {e}")
        except Exception as e:
            run_logger.error(f"An unexpected error occurred in the SWML generator: {e}", exc_info=True)
            raise