# app/planner.py
import logging
import google.generativeai as genai
import json
from typing import List, Dict, Any

from .plugins.base import ToolPlugin
from .utils import Timer

logger = logging.getLogger(__name__)
PLANNER_MODEL_NAME = "gemini-2.5-pro"
planner_model = genai.GenerativeModel(PLANNER_MODEL_NAME)

def create_plan(prompt: str, plugins: List[ToolPlugin], edit_index: int, run_logger: logging.Logger) -> Dict[str, Any]:
    run_logger.info("=" * 20 + " PLANNING " + "=" * 20)
    tools_description = "\n".join([f'- tool_name: "{p.name}"\n  description: "{p.description}"' for p in plugins])

    planner_prompt = f"""
You are an expert AI video production planner. Your job is to analyze a user's request and deconstruct it into two distinct parts:
1.  `generation_tasks`: A list of steps that create or modify individual media assets.
2.  `composition_prompt`: A single, clear instruction for a separate AI that will arrange assets on a timeline.

**CRITICAL RULES:**
- For each generation task, assign a deterministic `output_filename` using the format `gen_asset_{edit_index}_<part_num>_<desc>.<ext>`.
- The `composition_prompt` MUST refer to new assets by their exact `output_filename`.
- If ONLY composition is needed (e.g., rearranging clips), `generation_tasks` MUST be an empty list `[]`.
- If ONLY asset generation is needed (e.g., "create an image of a cat"), `generation_tasks` should be populated, and `composition_prompt` should be a simple instruction like "Add the new asset 'gen_asset_{edit_index}_1_cat.png' to a new track on the timeline."
- Your entire response MUST be a single, valid JSON object with keys "generation_tasks" and "composition_prompt".

**Available Asset Generation Tools:**
{tools_description}

**Context:**
- The current edit will be version number: {edit_index}
- User Request: "{prompt}"
"""
    with Timer(run_logger, "Planner LLM Call & Parsing"):
        run_logger.debug(f"--- PLANNER PROMPT ---\n{planner_prompt}\n--- END ---")
        try:
            response = planner_model.generate_content(planner_prompt)
            raw_response_text = response.candidates[0].content.parts[0].text
            cleaned_response = raw_response_text.strip().removeprefix("```json").removesuffix("```").strip()
            plan = json.loads(cleaned_response)

            if "generation_tasks" not in plan or "composition_prompt" not in plan:
                raise ValueError("Planner output missing required keys.")

            run_logger.info(f"PLANNER: Plan created with {len(plan['generation_tasks'])} generation task(s).")
            return plan
        except (json.JSONDecodeError, ValueError) as e:
            run_logger.error(f"Planner failed to create a valid plan. Error: {e}. Raw response:\n{cleaned_response if 'cleaned_response' in locals() else 'N/A'}")
            raise ValueError(f"The planner failed to create a valid JSON plan. Error: {e}")
        except Exception as e:
            run_logger.error(f"An unexpected error occurred in the planner: {e}", exc_info=True)
            raise