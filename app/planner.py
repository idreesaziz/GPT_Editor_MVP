import logging
import google.generativeai as genai
import json
from typing import List, Dict

from .plugins.base import ToolPlugin
from .utils import Timer # <-- IMPORT TIMER

logger = logging.getLogger(__name__) # Keep for general logging

PLANNER_MODEL_NAME = "gemini-2.5-pro" 
planner_model = genai.GenerativeModel(PLANNER_MODEL_NAME)


def create_plan(prompt: str, plugins: List[ToolPlugin], run_logger: logging.Logger) -> List[Dict]:
    """
    Uses an LLM to break a user prompt into a series of subtasks with assigned tools.
    """
    run_logger.info("=" * 20 + " PLANNING " + "=" * 20)
    run_logger.info(f"PLANNER: User prompt: '{prompt}'")
    
    with Timer(run_logger, "Planner LLM Call & Parsing"):
        tools_description = ""
        for plugin in plugins:
            tools_description += f"""- name: "{plugin.name}"
  description: "{plugin.description}"
  prerequisites: "{plugin.prerequisites}"
"""

        planner_prompt = f"""
You are an expert AI planner for a Python-based video editing system. Your task is to break down a user's video editing request into a sequence of individual, atomic steps.

For each step, you must:
1.  Write a clear instruction for a single action. This instruction will be fed to another AI to generate a Python script.
2.  Choose the most appropriate tool to accomplish that action from the list of available tools.

**IMPORTANT RULES:**
1.  **Check Prerequisites**: For each tool, a 'prerequisites' field is provided. You MUST read this field. If a tool has prerequisites (e.g., it needs video metadata), you MUST add the necessary preliminary steps to your plan to satisfy them.
2.  **Atomic Steps**: Each step in your plan should be a single, simple action.
3.  **JSON Output**: Respond ONLY with a valid JSON-formatted list of objects. Each object must have two keys: "task" and "tool".

**Available Tools:**
{tools_description}
**User Request:** "{prompt}"
"""
        
        run_logger.debug(f"--- PLANNER PROMPT ---\n{planner_prompt}\n--- END OF PLANNER PROMPT ---")
        
        try:
            response = planner_model.generate_content(planner_prompt)
            
            if not response.candidates:
                if response.prompt_feedback:
                     raise ValueError(f"Planner response was blocked: {response.prompt_feedback}")
                raise ValueError("Planner API returned no candidates.")

            raw_response_text = response.candidates[0].content.parts[0].text
            
            cleaned_response = raw_response_text.strip()
            if cleaned_response.startswith("```json"):
                cleaned_response = cleaned_response[7:]
            if cleaned_response.endswith("```"):
                cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip()

            run_logger.debug(f"PLANNER raw cleaned response: {cleaned_response}")
            try:
                plan = json.loads(cleaned_response)
                if not isinstance(plan, list) or not all(isinstance(p, dict) and 'task' in p and 'tool' in p for p in plan):
                    raise ValueError("Plan is not a list of dicts with 'task' and 'tool' keys.")
            except (json.JSONDecodeError, ValueError) as e:
                raise ValueError(f"Planner returned a malformed plan. Error: {e}. Raw response: {cleaned_response}")

            run_logger.info(f"PLANNER: Successfully created a plan with {len(plan)} step(s).")
            run_logger.info("=" * 48)
            return plan

        except (json.JSONDecodeError, ValueError) as e:
            error_msg = f"Failed to parse plan from LLM response: {e}\nResponse was: {raw_response_text if 'raw_response_text' in locals() else 'unavailable'}"
            run_logger.error(error_msg)
            raise ValueError(f"The planner failed to create a valid JSON plan. Error: {e}")
        except Exception as e:
            run_logger.error(f"An unexpected error occurred in the planner: {e}", exc_info=True)
            raise