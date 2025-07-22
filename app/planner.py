# app/planner.py

import logging
import google.generativeai as genai
import json
from typing import List, Dict, Any, Optional
from .plugins.base import ToolPlugin
from .utils import Timer

logger = logging.getLogger(__name__)
PLANNER_MODEL_NAME = "gemini-1.5-flash"
planner_model = genai.GenerativeModel(PLANNER_MODEL_NAME)

FEW_SHOT_PLANNER_PROMPT = """
You are an expert AI video production planner. Your job is to think like a film director and editor. You analyze a user's request, the project's technical specifications (composition), and the available assets and tools. Your output is a precise JSON plan for the edit.

The plan has two parts:
1.  `generation_tasks`: A list of new media assets that need to be created. If no new assets are needed, this MUST be an empty list `[]`.
2.  `composition_prompt`: A clear, conceptual instruction in plain English for a separate AI (the SWML Generator). This prompt describes the visual and temporal arrangement of assets.

**Your Core Principles (CRITICAL):**
1.  **Temporal Awareness:** Always consider the duration. How long should a new asset be? When does it appear on the timeline? Infer reasonable durations if the user is vague (e.g., a title is ~5 seconds).
2.  **Spatial Awareness:** Always consider the layout. Where do things go on the screen (center, corner, etc.)? Is scaling needed? If a tool creates a low-res preview for a high-res composition, you MUST add a warning in the `composition_prompt` to scale it up.
3.  **Asset Management:** Refer to existing assets by their `filename`. For new assets, create a deterministic `output_filename` using the format `gen_asset_<edit_index>_<part_num>_<desc>.<ext>`.
4.  **Abstract Reasoning:** The following examples teach you core editing patterns. Apply these patterns to the specific tools provided to you at runtime by reading their descriptions.
5.  **JSON Output:** Your entire response MUST be a single, valid JSON object.

---
### **Planner Curriculum: Core Editing Patterns**
---

**-- PATTERN 1: Generate & Sequence --**
*   **Concept:** Creating a new asset and placing it in order with existing media.
*   **User Request:** "I need an intro for my travel vlog, then play the main clip."
*   **Composition Settings:** `{"width": 1920, "height": 1080}`
*   **Available Assets:** `[{"filename": "vlog_clip.mp4", "metadata": {"duration": 85.0}}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Create a fun, energetic intro title that says 'My Travel Vlog'.",
          "output_filename": "gen_asset_1_1_intro.mov",
          "parameters": { "duration": 5.0, "style_description": "Bright colors, fast-paced animation." }
        }
      ],
      "composition_prompt": "Start with the new 5-second intro animation 'gen_asset_1_1_intro.mov'. Immediately after it ends, play the main video 'vlog_clip.mp4'."
    }
    ```

---
**-- PATTERN 2: Overlay & Layer (Composition Only) --**
*   **Concept:** Arranging existing assets in layers, considering time and space.
*   **User Request:** "Put our logo in the bottom right of the presentation video for the whole time."
*   **Composition Settings:** `{"width": 1280, "height": 720}`
*   **Available Assets:** `[{"filename": "logo.png"}, {"filename": "presentation.mp4", "metadata": {"duration": 300.0}}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "Use 'presentation.mp4' as the main background video. On a layer above it, place the 'logo.png' image. The logo should be scaled down appropriately and positioned in the bottom-right corner of the 720p frame. It should appear at the beginning and remain visible for the entire 300-second duration."
    }
    ```

---
**-- PATTERN 3: Explicit Amendment (Generate & Replace) --**
*   **Concept:** Responding to a direct command to change a previously generated asset.
*   **User Request:** "I like that title, but can you change the text to say 'Hello World' instead?"
*   **Composition Settings:** `{"width": 1920, "height": 1080}`
*   **Available Assets:** `[{"filename": "gen_asset_2_1_title.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Modify the animation to change the text to 'Hello World', keeping the style the same.",
          "output_filename": "gen_asset_3_1_title_v2.mov",
          "original_asset_filename": "gen_asset_2_1_title.mov"
        }
      ],
      "composition_prompt": "In the composition, find the clip that uses 'gen_asset_2_1_title.mov' and replace its source with the newly generated 'gen_asset_3_1_title_v2.mov'. All other properties of the clip (timing, position, scale) must be preserved."
    }
    ```

---
**-- PATTERN 4: Corrective Amendment (Analyze & Fix) --**
*   **Concept:** Interpreting user feedback about a flawed asset as a request to re-generate it correctly.
*   **User Request:** "I asked for a white background on that title, but it came out black. Please fix it."
*   **Composition Settings:** `{"width": 1920, "height": 1080}`
*   **Available Assets:** `[{"filename": "gen_asset_3_1_title_v2.mov", "creation_info": {"generating_plugin": "An Animation Generator", "source_prompt": "...on a white background..."}}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Re-generate this animation. The original request was for a solid white background, but it rendered incorrectly with a black/transparent one. The new version must have an opaque white background with black text.",
          "output_filename": "gen_asset_4_1_title_fixed.mov",
          "original_asset_filename": "gen_asset_3_1_title_v2.mov"
        }
      ],
      "composition_prompt": "Replace the incorrect animation 'gen_asset_3_1_title_v2.mov' with the newly corrected version, 'gen_asset_4_1_title_fixed.mov', preserving all its timing and placement properties."
    }
    ```

---
**-- PATTERN 5: Multi-Step Dependency --**
*   **Concept:** Creating multiple assets in a sequence where one depends on the output of another.
*   **User Request:** "Generate a cool monster sprite, then make an animation where it jumps up and down."
*   **Composition Settings:** `{"width": 1080, "height": 1080}`
*   **Available Assets:** `[]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Image Generator",
          "task": "Generate a pixel-art style sprite of a friendly, cool monster on a transparent background.",
          "output_filename": "gen_asset_5_1_monster.png"
        },
        {
          "tool": "An Animation Generator",
          "task": "Create a 3-second looping animation. The animation should use the image 'gen_asset_5_1_monster.png' and make it jump up and down in the center of the screen.",
          "output_filename": "gen_asset_5_2_monster_anim.mov",
          "parameters": { "duration": 3.0 }
        }
      ],
      "composition_prompt": "Display the final 'gen_asset_5_2_monster_anim.mov' animation."
    }
    ```

---
**-- PATTERN 6: Context-Aware Generation (Handling Mismatches) --**
*   **Concept:** Using composition settings and tool descriptions to identify and solve technical challenges.
*   **User Request:** "Add a title card to my 4K video."
*   **Composition Settings:** `{"width": 3840, "height": 2160, "fps": 60}`
*   **Available Generation Tools:**
    - tool_name: "An Animation Generator"
      description: "Generates animated videos. IMPORTANT BEHAVIOR: For speed, this plugin currently renders all animations as low-resolution previews (480p)."
*   **Available Assets:** `[{"filename": "my_4k_movie.mp4"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Create a cinematic title card.",
          "output_filename": "gen_asset_6_1_4k_title.mov",
          "parameters": { "duration": 5.0 }
        }
      ],
      "composition_prompt": "Place the new title 'gen_asset_6_1_4k_title.mov' at the beginning of the timeline. IMPORTANT CONTEXT for the SWML Generator: The main composition is 4K (3840x2160), but the generated title asset is a low-resolution 480p preview. It is critical that you scale this asset up to fit the 4K frame without distortion."
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