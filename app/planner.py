# app/planner.py

import logging
import google.generativeai as genai
import json
from typing import List, Dict, Any, Optional
from .plugins.base import ToolPlugin
from .utils import Timer

logger = logging.getLogger(__name__)
# NOTE: Using gemini-1.5-flash as gemini-2.5-flash may be a future model.
# This can be updated once the model is available.
PLANNER_MODEL_NAME = "gemini-2.5-flash"
planner_model = genai.GenerativeModel(PLANNER_MODEL_NAME)

FEW_SHOT_PLANNER_PROMPT = """
You are an expert AI video production planner. Your primary goal is to create editing plans for a specific composition engine called the **Swimlane Engine**. You must operate *strictly* within the documented capabilities of this engine. If a task cannot be accomplished using the engine's features, you MUST delegate it to a generation tool. **DO NOT HALLUCINATE or assume any capabilities not explicitly listed below.**

---
### **Swimlane Composition Engine Capabilities (CRITICAL CONTEXT)**
---
The Swimlane Engine is a declarative renderer using a JSON format (SWML). Your `composition_prompt` must describe changes that are possible *only* within this system. Your knowledge of this engine is limited to the features listed here and nothing more.

**CAPABILITIES (Compositional Tasks):**
*   **Static Transforms:** You can set a clip's `position`, `size` (scale), and `anchor` point. These are *static* properties for the entire duration of the clip.
*   **Timing:** You can set a clip's `start_time`, `end_time`, and `source_start` (trimming).
*   **Layering:** Clips can be layered on different tracks.
*   **Transitions:** The engine supports `fade`, `wipe`, and `dissolve` transitions *between clips*. A fade on a single clip (in or out) is also possible.
*   **Audio:** You can adjust a clip's `volume` and apply `fade_in` or `fade_out`.

**LIMITATIONS (Requires Generation Task):**
*   **NO KEYFRAME ANIMATION:** The engine **cannot** animate properties over time (e.g., animate position, scale, or rotation). Any request for an animated transform MUST be a `generation_task`.
*   **NO BUILT-IN TEXT GENERATION:** The engine **cannot** create text. All text must be generated as a new asset (e.g., a PNG image with transparency) via a `generation_task`.
*   **NO DYNAMIC EFFECTS/FILTERS:** The engine has no color grading, blur, or other visual effect capabilities beyond the specified transforms and transitions.
*   **ABSOLUTE RULE:** If a requested capability is not explicitly in the `CAPABILITIES` list above, you MUST assume it is a limitation and create a `generation_task`.

---
### **Your Core Principles (CRITICAL):**
1.  **Composition First Principle:** For any request that involves changing the static timing, position, scale, or adding a supported transition to *existing* clips, you MUST default to a composition-only solution. `generation_tasks` must be `[]`.
2.  **The Generation Rule:** Any request that cannot be fulfilled by the Swimlane Engine's documented capabilities MUST be delegated as a `generation_task`. This applies to any task that requires creating new content or fundamentally altering the pixels of an existing asset, beyond what simple transforms and timing can achieve. Examples include: creating animations (like sliding or growing), generating text, or applying visual effects not listed in the capabilities.
3.  **Clean Generation Tasks:** Instructions for NEW assets must be pure and self-contained. Do not include compositional context (e.g., "under the blue box").
4.  **JSON Output:** Your entire response MUST be a single, valid JSON object.
5.  **Strict Adherence to Limitations:** Your `composition_prompt` can ONLY describe operations that are explicitly listed in the `Swimlane Composition Engine Capabilities`. Any other operation is, by definition, a `generation_task`. There are no exceptions to this rule.

---
### **Planner Curriculum: Core Editing Patterns**
---

**-- PATTERN 1: Composition-Only Edit (Positioning) --**
*   **Concept:** Recognizing that moving an existing element is a static transform in SWML.
*   **User Request:** "The title is overlapping the speaker. Can you move it to the top-right corner?"
*   **Available Assets:** `[{"filename": "title.png"}, {"filename": "speaker.mp4"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. In the SWML file, find the clip that uses the 'title.png' source and update its `transform.position` property to place it in the top-right corner of the frame. Do not generate any new assets."
    }
    ```

**-- PATTERN 2: Composition-Only Edit (Transitions) --**
*   **Concept:** Recognizing that adding a fade is a standard SWML transition.
*   **User Request:** "Instead of the video ending abruptly, can you make it fade to black?"
*   **Available Assets:** `[{"filename": "final_clip.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. In the SWML, add a `transition` object at the end of the timeline targeting the 'final_clip.mov'. The transition should have an `effect` of 'fade' and a duration of 1 second to create a fade-to-black."
    }
    ```

**-- PATTERN 3: Fundamental Animation (Requires Generation) --**
*   **Concept:** Recognizing that animated transforms are not supported by Swimlane and require asset generation.
*   **User Request:** "Animate a white box growing from a tiny dot to fill the screen over 3 seconds."
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Create a 3-second animation of a white square that starts at a very small scale and animates its growth to a large scale, appearing to fill the frame.",
          "output_filename": "gen_asset_3_1_growing_box.mov",
          "parameters": { "duration": 3.0 }
        }
      ],
      "composition_prompt": "Place the new 'gen_asset_3_1_growing_box.mov' animation on a new video track on the timeline."
    }
    ```
    
**-- PATTERN 4: Content Amendment (Requires Generation) --**
*   **Concept:** Changing the internal content (pixels, text) of an asset REQUIRES regeneration.
*   **User Request:** "I like that title animation, but can you change the text to say 'Hello World' instead?"
*   **Available Assets:** `[{"filename": "gen_asset_4_1_title.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Modify the animation's source code to change the text to 'Hello World', keeping the style the same.",
          "output_filename": "gen_asset_5_1_title_v2.mov",
          "original_asset_filename": "gen_asset_4_1_title.mov"
        }
      ],
      "composition_prompt": "This is an amendment. In the SWML, find the clip using 'gen_asset_4_1_title.mov' and update its `source_id` to point to the new 'gen_asset_5_1_title_v2.mov' asset. All other properties (timing, transform) must be preserved."
    }
    ```
    
**-- PATTERN 5: Additive Layering (Generate & Compose) --**
*   **Concept:** Adding a new element requires generation, then composition places it.
*   **User Request:** "Okay, the blue box is good. Now, add the text 'My Cool Product' right underneath it."
*   **Available Assets:** `[{"filename": "gen_asset_2_1_blue_box.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Image Generator",
          "task": "Create a static image with the text 'My Cool Product' on a transparent background. The text should be white and centered.",
          "output_filename": "gen_asset_3_1_product_text.png"
        }
      ],
      "composition_prompt": "This is an additive change. In the SWML, add a new clip using the 'gen_asset_3_1_product_text.png' source. Place it on a new video track above the existing ones. Set its `transform.position` so it appears visually centered directly underneath the 'gen_asset_2_1_blue_box.mov' clip."
    }
    ```

**-- PATTERN 6: New Text (Requires Generation) --**
*   **Concept:** Demonstrating that all new text must be generated as an asset because Swimlane has no text tool.
*   **User Request:** "Add a title at the beginning that says 'Welcome to Our Demo'."
*   **Your JSON Output:**
    ```json
    {
        "generation_tasks": [
            {
                "tool": "An Image Generator",
                "task": "Generate a high-quality static image with the text 'Welcome to Our Demo'. The background must be transparent.",
                "output_filename": "gen_asset_1_1_welcome_title.png"
            }
        ],
        "composition_prompt": "Add the new asset 'gen_asset_1_1_welcome_title.png' to the composition. Create a new clip for it on a top video track, starting at time 0.0 and lasting for 5 seconds. Set its `transform` to be centered horizontally and positioned in the top 20% of the screen."
    }
    ```

---
### **TASK TO BE PERFORMED**
---
"""

def create_plan(
    prompt: str,
    plugins: List[ToolPlugin],
    edit_index: int,
    run_logger: logging.Logger,
    available_assets_metadata: Optional[str] = None,
    composition_settings: Dict[str, Any] = None
) -> Dict[str, Any]:
    """
    Generates a video editing plan using an LLM, specifically tailored for the Swimlane Engine.

    The planner decides whether to generate new assets (for animation, text, etc.)
    or just modify the SWML composition for static changes, strictly adhering to
    the documented capabilities of the engine.

    Args:
        prompt: The user's editing request.
        plugins: A list of available tool plugins for asset generation.
        edit_index: The current edit number in the session.
        run_logger: The logger instance for the current run.
        available_assets_metadata: A JSON string of metadata for existing assets.
        composition_settings: A dictionary with project settings like width and height.

    Returns:
        A dictionary representing the plan, containing 'generation_tasks' and a
        'composition_prompt' with instructions for modifying the SWML file.

    Raises:
        ValueError: If the LLM output is not valid JSON or misses required keys.
        Exception: For any other errors during the LLM call.
    """
    run_logger.info("=" * 20 + " PLANNING " + "=" * 20)
    tools_description = "\n".join([f'- tool_name: "{p.name}"\n  description: "{p.description}"' for p in plugins])

    if not available_assets_metadata or available_assets_metadata.strip() in ["[]", "{}"]:
        assets_metadata_section = "No assets are currently available in the project."
    else:
        assets_metadata_section = f"```json\n{available_assets_metadata}\n```"

    composition_section = f"```json\n{json.dumps(composition_settings, indent=2)}\n```" if composition_settings else "Default composition settings."

    final_prompt = f"""{FEW_SHOT_PLANNER_PROMPT}
*   **Edit Index:** {edit_index}
*   **User Request:** "{prompt}"
*   **Composition Settings:**
{composition_section}
*   **Available Generation Tools:**
{tools_description}
*   **Available Assets:**
{assets_metadata_section}
*   **Your JSON Output:**
"""

    with Timer(run_logger, "Planner LLM Call & Parsing"):
        run_logger.debug(f"--- PLANNER PROMPT ---\n{final_prompt}\n--- END ---")
        response_text = ""
        try:
            response = planner_model.generate_content(
                final_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            response_text = response.text
            plan = json.loads(response_text)

            # Basic validation of the plan structure
            if "generation_tasks" not in plan or "composition_prompt" not in plan:
                raise ValueError("Planner output is missing 'generation_tasks' or 'composition_prompt' keys.")

            run_logger.info(f"PLANNER: Plan created with {len(plan['generation_tasks'])} generation task(s).")
            run_logger.info(f"PLANNER: Composition Prompt: '{plan['composition_prompt'][:150]}...'")
            return plan
        except (json.JSONDecodeError, ValueError) as e:
            run_logger.error(f"Failed to parse or validate planner's JSON response: {e}")
            run_logger.error(f"LLM Raw Response Text: {response_text}")
            raise
        except Exception as e:
            run_logger.error(f"An unexpected error occurred in the planner: {e}", exc_info=True)
            raise