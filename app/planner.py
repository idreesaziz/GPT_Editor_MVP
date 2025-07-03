import logging
import google.generativeai as genai
import json
from typing import List, Dict

from .plugins.base import ToolPlugin

logger = logging.getLogger(__name__)

# This could be a different, cheaper model optimized for planning/JSON generation
PLANNER_MODEL_NAME = "gemini-2.5-pro" 
planner_model = genai.GenerativeModel(PLANNER_MODEL_NAME)


def create_plan(prompt: str, plugins: List[ToolPlugin]) -> List[Dict]:
    """
    Uses an LLM to break a user prompt into a series of subtasks with assigned tools.
    """
    logger.info(f"Creating a plan for the prompt: '{prompt}'")

    tools_description = ""
    for plugin in plugins:
        tools_description += f'- name: "{plugin.name}"\n  description: "{plugin.description}"\n'

    planner_prompt = f"""
You are an expert AI planner for a Python-based video editing system. Your task is to break down a user's video editing request into a sequence of individual steps that will be turned into executable Python scripts.

For each step, you must:
1.  Write a clear, simple instruction for a single, atomic action. This instruction will be fed to another AI to generate a Python script.
2.  Choose the most appropriate tool to accomplish that action from the list of available tools. The script will use this tool.
3.  Some steps may not produce a video, but instead produce intermediate assets (like images or audio files) that will be used by later steps. The final step should result in a single video file.

If the user's request is already a single, simple action, respond with a JSON list containing only that one action.

Respond ONLY with a valid JSON-formatted list of objects. Each object must have two keys: "task" (the instruction for the script-generating AI) and "tool" (the name of the chosen tool).

Available Tools:
{tools_description}
User Request: "{prompt}"
"""
    
    logger.debug(f"--- Planner Prompt ---\n{planner_prompt}\n--- End of Planner Prompt ---")
    
    try:
        response = planner_model.generate_content(planner_prompt)
        raw_response_text = response.text.strip().removeprefix("```json").removesuffix("```").strip()
        
        logger.debug(f"Planner raw response: {raw_response_text}")
        plan = json.loads(raw_response_text)
        
        if not isinstance(plan, list) or not all(isinstance(p, dict) and 'task' in p and 'tool' in p for p in plan):
            raise ValueError("Planner returned a malformed plan.")

        logger.info(f"Successfully created a plan with {len(plan)} step(s).")
        return plan

    except (json.JSONDecodeError, ValueError) as e:
        logger.error(f"Failed to parse plan from LLM response: {e}\nResponse was: {response.text}")
        raise ValueError(f"The planner failed to create a valid JSON plan. Error: {e}")
    except Exception as e:
        logger.error(f"An unexpected error occurred in the planner: {e}")
        raise