import logging
import google.generativeai as genai
import json
from typing import List, Dict, Any, Optional  # Import Optional
from .plugins.base import ToolPlugin
from .utils import Timer

logger = logging.getLogger(__name__)
PLANNER_MODEL_NAME = "gemini-2.5-flash"
planner_model = genai.GenerativeModel(PLANNER_MODEL_NAME)


def create_plan(
    prompt: str,
    plugins: List[ToolPlugin],
    edit_index: int,
    run_logger: logging.Logger,
    available_assets_metadata: Optional[str] = None  # New argument
) -> Dict[str, Any]:
    run_logger.info("=" * 20 + " PLANNING " + "=" * 20)
    tools_description = "\n".join([f'- tool_name: "{p.name}"\n  description: "{p.description}"' for p in plugins])
    
    # Prepare available assets metadata section
    assets_metadata_section = ""
    if available_assets_metadata:
        assets_metadata_section = f"""
**Currently Available Assets (Metadata for files already in the session):**
```json
{available_assets_metadata}
```
"""

    planner_prompt = f"""
You are an expert AI video production planner. Your job is to analyze a user's request and deconstruct it into two distinct parts:

generation_tasks: A list of steps that create or modify individual media assets.

composition_prompt: A single, clear instruction for a separate AI that will arrange assets on a timeline.

CRITICAL RULES:

For each generation task, assign a deterministic output_filename using the format gen_asset_{edit_index}_<part_num>_<desc>.<ext>.

The composition_prompt MUST refer to new assets by their exact output_filename.

If ONLY composition is needed (e.g., rearranging clips, or using existing assets), generation_tasks MUST be an empty list [].

If ONLY asset generation is needed (e.g., "create an image of a cat"), generation_tasks should be populated, and composition_prompt should be a simple instruction like "Add the new asset 'gen_asset_{edit_index}_1_cat.png' to a new track on the timeline."

Your entire response MUST be a single, valid JSON object with keys "generation_tasks" and "composition_prompt".

Available Asset Generation Tools:
{tools_description}

{assets_metadata_section}

Context:

The current edit will be version number: {edit_index}

User Request: "{prompt}"
"""

    with Timer(run_logger, "Planner LLM Call & Parsing"):
        run_logger.debug(f"--- PLANNER PROMPT ---\n{planner_prompt}\n--- END ---")
        try:
            # Use generation_config to force JSON output
            response = planner_model.generate_content(
                planner_prompt,
                generation_config={"response_mime_type": "application/json"}
            )

            # With response_mime_type="application/json", response.text is guaranteed to be valid JSON
            plan = json.loads(response.text)

            if "generation_tasks" not in plan or "composition_prompt" not in plan:
                raise ValueError("Planner output missing required keys.")

            run_logger.info(f"PLANNER: Plan created with {len(plan['generation_tasks'])} generation task(s).")
            return plan

        except (json.JSONDecodeError, ValueError) as e:
            # This block should be hit less often with response_mime_type, but good for robustness
            raw_response_text = response.text if 'response' in locals() else 'N/A (No response object)'
            run_logger.error(f"Planner failed to create a valid plan. Error: {e}. Raw response:\n{raw_response_text}")
            raise ValueError(f"The planner failed to create a valid JSON plan. Error: {e}")

        except Exception as e:
            run_logger.error(f"An unexpected error occurred in the planner: {e}", exc_info=True)
            raise