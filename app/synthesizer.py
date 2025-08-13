# app/synthesizer.py

import logging
import google.generativeai as genai
from google import genai as vertex_genai
from google.genai import types
from google.genai.types import HttpOptions
import json
import os
from typing import List, Dict, Any, Optional

from .utils import Timer

logger = logging.getLogger(__name__)

SYNTHESIZER_MODEL_NAME = "gemini-2.5-flash"

# Check if we should use Vertex AI
USE_VERTEX_AI = os.getenv("USE_VERTEX_AI", "false").lower() == "true"

if USE_VERTEX_AI:
    vertex_client = vertex_genai.Client(
        vertexai=True,
        project=os.getenv("VERTEX_PROJECT_ID"),
        location=os.getenv("VERTEX_LOCATION", "us-central1")
    )
    synthesizer_model = None  # We'll use the client directly
else:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable not found or not set.")
    genai.configure(api_key=api_key)
    synthesizer_model = genai.GenerativeModel(SYNTHESIZER_MODEL_NAME)

# --- UPGRADED SYSTEM PROMPT ---
# This prompt now teaches the LLM how to use the current SWML state.
SYSTEM_PROMPT = """
You are an expert AI assistant who functions as a "Prompt Clarification Layer". Your only job is to re-write a conversational user request into a clear, specific, and self-contained instruction for a downstream "Planner" AI.

You DO NOT create the plan. You ONLY clarify the user's intent by resolving ambiguity.

---
### Your Context Sources (How you make decisions)
---
You have three sources of information to make your decision:
1.  **Conversation History:** What the user has asked for in the past.
2.  **Available Assets:** A list of all media files that have ever been created.
3.  **Current SWML State (The Timeline):** This is your MOST IMPORTANT source. It shows you exactly which assets are currently being used in the video timeline and how they are arranged. It tells you what the user is actually looking at.

---
### Your Core Task
---
Analyze the user's latest request using all three context sources to produce a new, clarified prompt.

- **Use the SWML State to find the subject.** When the user says "it" or "the title", you MUST look at the SWML file to see which asset is currently on the timeline. This is your primary clue.
- **Resolve Pronouns:** Replace vague terms with the specific asset name or description.
- **Preserve Context:** If the user asks to modify an asset (e.g., change its color), your clarified prompt must include all the original details of that asset (e.g., its text content).

---
### Examples of Your Task
---

**-- Example 1: A vague follow-up request. --**
*   **User's LATEST Request:** "ok i wanna make the text black and the background white"
*   **Conversation History:** ["make a nice intro saying hello world"]
*   **Available Assets:** `[ { "filename": "assets/hello_world_intro/asset.mov", "creation_info": { "source_prompt": "Create a nice intro..." } } ]`
*   **Current SWML State:** `{ "tracks": [ { "id": 1, "clips": [ { "source_id": "hello_world_intro_asset", ... } ] } ] }`
*   **Your Reasoning:** The user wants to change "the text". The SWML state shows that "hello_world_intro_asset" is on the timeline. That asset's creation prompt was about "Hello World".
*   **Your Clarified Prompt (Output):**
    "Change the 'Hello World' intro animation to have black text and a white background."

**-- Example 2: A vague request about color scheme. --**
*   **User's LATEST Request:** "change the color scheme to be more exciting"
*   **Conversation History:** ["...create a 'Bellow Borld' title..."]
*   **Available Assets:** `[ { "filename": "assets/bellow_borld_animation/asset.mov", "creation_info": { "source_prompt": "Create a title saying 'Bellow Borld'." } } ]`
*   **Current SWML State:** `{ "tracks": [ { "id": 1, "clips": [ { "source_id": "bellow_borld_animation_asset", ... } ] } ] }`
*   **Your Reasoning:** The user wants to change a "color scheme." The only thing on the timeline is the "Bellow Borld" animation. Therefore, they are referring to that asset.
*   **Your Clarified Prompt (Output):**
    "Amend the 'Bellow Borld' animation to have a more exciting color scheme."
"""

class PromptSynthesizer:
    """
    An AI layer that analyzes user intent and context to create a clear,
    unambiguous prompt for the Planner.
    """

    def synthesize_prompt(
        self,
        user_prompt: str,
        prompt_history: List[str],
        available_assets_metadata: str,
        current_swml_data: Dict[str, Any], # <-- NEW ARGUMENT
        run_logger: logging.Logger
    ) -> str:
        """
        Takes conversational context and generates a precise instruction.
        """
        run_logger.info("=" * 20 + " PROMPT SYNTHESIS " + "=" * 20)

        with Timer(run_logger, "Prompt Synthesizer LLM Call"):
            formatted_history = "\n".join(f"- {p}" for p in prompt_history) if prompt_history else "No previous prompts in this session."

            if not available_assets_metadata or available_assets_metadata.strip() in ["[]", "{}"]:
                assets_metadata_section = "No assets are currently available in the project."
            else:
                assets_metadata_section = f"```json\n{available_assets_metadata}\n```"
            
            # --- NEW: Format the current SWML data for the prompt ---
            swml_state_section = f"```json\n{json.dumps(current_swml_data, indent=2)}\n```"

            final_prompt_for_llm = f"""{SYSTEM_PROMPT}
---
### Your Task for THIS Request
---

*   **Full Conversation History:**
{formatted_history}

*   **User's LATEST Request:**
"{user_prompt}"

*   **Available Assets (Your Inventory):**
{assets_metadata_section}

*   **Current SWML State (The Timeline - Your Most Important Clue):**
{swml_state_section}

*   **Your Clarified Prompt for the Planner (Your output MUST be only this single, refined instruction):**
"""

            run_logger.debug(f"--- SYNTHESIZER PROMPT ---\n{final_prompt_for_llm}\n--- END ---")
            
            try:
                if USE_VERTEX_AI:
                    response = vertex_client.models.generate_content(
                        model=SYNTHESIZER_MODEL_NAME,
                        contents=final_prompt_for_llm
                    )
                    synthesized_prompt = response.text.strip()
                else:
                    response = synthesizer_model.generate_content(final_prompt_for_llm)
                    synthesized_prompt = response.text.strip()

                if not synthesized_prompt:
                    raise ValueError("Synthesizer returned an empty prompt.")

                run_logger.info(f"SYNTHESIZER: Original prompt: '{user_prompt}'")
                run_logger.info(f"SYNTHESIZER: Clarified prompt: '{synthesized_prompt}'")
                return synthesized_prompt
            except Exception as e:
                run_logger.error(f"An unexpected error occurred in the Prompt Synthesizer: {e}", exc_info=True)
                run_logger.warning("Synthesizer failed. Falling back to using the original user prompt for the Planner.")
                return user_prompt