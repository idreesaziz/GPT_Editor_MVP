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
2.  **The Generation Rule:** Any request that cannot be fulfilled by the Swimlane Engine's documented capabilities MUST be delegated as a `generation_task`.
3.  **Unique Unit ID:** For each task in `generation_tasks`, you MUST provide a unique `unit_id`. This ID should be a descriptive, snake-case string that represents the asset being created (e.g., `main_title_animation`, `intro_narration_s1`).
4.  **Clean Generation Tasks:** Instructions for NEW assets must be pure and self-contained. The `output_filename` should be a simple, generic name like `asset.mov` or `image.png`, as it will be placed inside a unique directory named after the `unit_id`.
5.  **JSON Output:** Your entire response MUST be a single, valid JSON object.
6.  **Strict Adherence to Limitations:** Your `composition_prompt` can ONLY describe operations that are explicitly listed in the `Swimlane Composition Engine Capabilities`.

---
### **Planner Curriculum: Core Editing Patterns**
---

**-- PATTERN 1: Composition-Only Edit (Positioning) --**
*   **Concept:** Recognizing that moving an existing element is a static transform in SWML.
*   **User Request:** "The title is overlapping the speaker. Can you move it to the top-right corner?"
*   **Available Assets:** `[{"filename": "assets/welcome_title/title.png"}, {"filename": "speaker.mp4"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. In the SWML file, find the clip that uses the 'assets/welcome_title/title.png' source and update its `transform.position` property to place it in the top-right corner of the frame. Do not generate any new assets."
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
          "unit_id": "growing_white_box_anim",
          "task": "Create a 3-second animation of a white square that starts at a very small scale and animates its growth to a large scale, appearing to fill the frame.",
          "output_filename": "animation.mov",
          "parameters": { "duration": 3.0 }
        }
      ],
      "composition_prompt": "Place the new 'assets/growing_white_box_anim/animation.mov' asset on a new video track on the timeline."
    }
    ```
    
**-- PATTERN 4: Content Amendment (Requires Generation) --**
*   **Concept:** Changing the internal content (pixels, text) of an asset REQUIRES regeneration.
*   **User Request:** "I like that title animation, but can you change the text to say 'Hello World' instead?"
*   **Available Assets:** `[{"filename": "assets/title_animation/asset.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "unit_id": "hello_world_title_v2",
          "task": "Modify the animation's source code to change the text to 'Hello World', keeping the style the same.",
          "output_filename": "asset.mov",
          "original_asset_path": "assets/title_animation/asset.mov"
        }
      ],
      "composition_prompt": "This is an amendment. In the SWML, find the clip using 'assets/title_animation/asset.mov' and update its `source_id` to point to the new 'assets/hello_world_title_v2/asset.mov' asset. All other properties (timing, transform) must be preserved."
    }
    ```
    
**-- PATTERN 5: Additive Layering (Generate & Compose) --**
*   **Concept:** Adding a new element requires generation, then composition places it.
*   **User Request:** "Okay, the blue box is good. Now, add the text 'My Cool Product' right underneath it."
*   **Available Assets:** `[{"filename": "assets/blue_box/asset.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Image Generator",
          "unit_id": "cool_product_text",
          "task": "Create a static image with the text 'My Cool Product' on a transparent background. The text should be white and centered.",
          "output_filename": "image.png"
        }
      ],
      "composition_prompt": "This is an additive change. In the SWML, add a new clip using the 'assets/cool_product_text/image.png' source. Place it on a new video track above the existing ones. Set its `transform.position` so it appears visually centered directly underneath the 'assets/blue_box/asset.mov' clip."
    }
    ```

    **-- PATTERN 6: Add Voiceover (NEW PATTERN) --**
*   **Concept:** Generating a new audio asset from a script.
*   **User Request:** "Add a voiceover that says: 'Welcome to our presentation. We hope you enjoy it.'"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Voiceover Generator",
          "unit_id": "welcome_voiceover",
          "task": "Welcome to our presentation. We hope you enjoy it.",
          "output_filename": "narration.mp3"
        }
      ],
      "composition_prompt": "This is an additive change. Add a new audio track to the composition. On this track, create a new clip for the 'assets/welcome_voiceover/narration.mp3' source. The clip should start at time 0.0."
    }
    ```

    **-- PATTERN 7: Generative Video (Using Veo) --**
*   **Concept:** Recognizing that a request for a realistic or cinematic scene requires the Veo tool.
*   **User Request:** "I need a beautiful shot of a sunset over the ocean."
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Veo Video Generator",
          "unit_id": "ocean_sunset_shot",
          "task": "A beautiful, cinematic, photorealistic shot of a sunset over a calm ocean. Warm colors, gentle waves.",
          "output_filename": "video.mp4"
        }
      ],
      "composition_prompt": "Add the new video 'assets/ocean_sunset_shot/video.mp4' to the main video track at the end of the current timeline."
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