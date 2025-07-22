# app/planner.py

import logging
import google.generativeai as genai
import json
from typing import List, Dict, Any, Optional
from .plugins.base import ToolPlugin
from .utils import Timer

logger = logging.getLogger(__name__)
PLANNER_MODEL_NAME = "gemini-2.5-flash"
planner_model = genai.GenerativeModel(PLANNER_MODEL_NAME)

FEW_SHOT_PLANNER_PROMPT = """
You are an expert AI video production planner. Your primary goal is to create efficient and flexible editing plans. You must distinguish between tasks requiring new asset **content** and tasks that are purely **compositional** (arrangement, transforms, effects).

**Your Core Principles (CRITICAL):**
1.  **Composition First Principle:** For any request that involves changing the timing, position, scale, rotation, or adding simple transitions (like fades) to *existing* clips, you MUST default to a composition-only solution. This means `generation_tasks` must be `[]` and the `composition_prompt` must describe the change.
2.  **The Exception - Fundamental Content:** The only time you should ask a plugin to handle transforms (like scaling or moving) is when the *animation of that transform is the content itself* (e.g., "animate a box growing"). If it's just setting a final state (e.g., "make the box smaller"), that is a composition task.
3.  **Composition Engine Capabilities:** Assume the composition engine is powerful. It can handle layering, transforms (position, scale, rotation), and basic transitions (fades).
4.  **Clean Generation Tasks:** Instructions for NEW assets must be pure and self-contained. Do not include compositional context (e.g., "under the blue box").
5.  **JSON Output:** Your entire response MUST be a single, valid JSON object.

---
### **Planner Curriculum: Core Editing Patterns**
---

**-- PATTERN 1: Composition-Only Edit (Positioning) --**
*   **Concept:** Recognizing that moving an existing element is a composition task.
*   **User Request:** "The title is overlapping the speaker. Can you move it to the top-right corner?"
*   **Available Assets:** `[{"filename": "title.mov"}, {"filename": "speaker.mp4"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. Based on the previous composition, find the clip containing 'title.mov' and change its position to the top-right corner of the frame. Do not generate any new assets."
    }
    ```

---
**-- PATTERN 2: Composition-Only Edit (Transitions) --**
*   **Concept:** Recognizing that adding a fade is a composition task.
*   **User Request:** "Instead of the video ending abruptly, can you make it fade to black?"
*   **Available Assets:** `[{"filename": "final_clip.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. At the very end of the timeline, add a 1-second fade-to-black transition on the 'final_clip.mov'."
    }
    ```

---
**-- PATTERN 3: Fundamental Animation (The Exception to the Rule) --**
*   **Concept:** Recognizing when an animation of a transform IS the content, requiring generation.
*   **User Request:** "Animate a white box growing from a tiny dot to fill the screen over 3 seconds."
*   **Composition Settings:** `{"width": 1920, "height": 1080}`
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
      "composition_prompt": "Place the new 'gen_asset_3_1_growing_box.mov' animation on the timeline. IMPORTANT CONTEXT: The asset is a low-res preview. It MUST be scaled up to fit the 1080p frame."
    }
    ```
    
---
**-- PATTERN 4: Content Amendment (Requires Generation) --**
*   **Concept:** Changing the internal content (pixels, text) of an asset, which REQUIRES regeneration.
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
      "composition_prompt": "This is an amendment. The composition should be based on the previous version, but the clip using 'gen_asset_4_1_title.mov' MUST be updated to use the new source 'gen_asset_5_1_title_v2.mov'. All other properties (timing, position, scale) must be preserved."
    }
    ```
    
---
**-- PATTERN 5: Additive Layering (Generate & Compose) --**
*   **Concept:** Adding a new, independent element that is spatially related to an existing one.
*   **User Request:** "Okay, the blue box is good. Now, add the text 'My Cool Product' right underneath it."
*   **Available Assets:** `[{"filename": "gen_asset_2_1_blue_box.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Create a text-only animation for the words 'My Cool Product'. It must have a transparent background and contain NO other shapes or elements.",
          "output_filename": "gen_asset_3_1_product_text.mov",
          "parameters": { "duration": 5.0 }
        }
      ],
      "composition_prompt": "This is an additive change. Keep all clips from the previous composition. Add a new layer for the text animation 'gen_asset_3_1_product_text.mov'. Position this new text clip so that it appears visually centered directly underneath the 'gen_asset_2_1_blue_box.mov' clip."
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
        try:
            response = planner_model.generate_content(
                final_prompt,
                generation_config={"response_mime_type": "application/json"}
            )
            plan = json.loads(response.text)
            if "generation_tasks" not in plan or "composition_prompt" not in plan:
                raise ValueError("Planner output is missing 'generation_tasks' or 'composition_prompt' keys.")
            run_logger.info(f"PLANNER: Plan created with {len(plan['generation_tasks'])} generation task(s).")
            run_logger.info(f"PLANNER: Composition Prompt: '{plan['composition_prompt'][:150]}...'")
            return plan
        except Exception as e:
            run_logger.error(f"An unexpected error occurred in the planner: {e}", exc_info=True)
            raise