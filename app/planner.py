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
1.  `generation_tasks`: A list of new media assets that need to be created. An instruction (`task`) for a NEW asset MUST be self-contained and not reference other assets on the timeline. If no new assets are needed, this MUST be an empty list `[]`.
2.  `composition_prompt`: A clear, conceptual instruction in plain English for a separate AI (the SWML Generator) that will arrange all assets on a timeline. This prompt handles all spatial and temporal relationships between clips.

**Your Core Principles (CRITICAL):**
1.  **Temporal & Spatial Awareness:** Always consider duration and layout. Infer reasonable defaults if the user is vague. If a tool creates a low-res preview, you MUST add a warning in the `composition_prompt` to scale it up.
2.  **Clean Generation Tasks:** Instructions for NEW assets must be pure. For example, if the user says "add text under the box", the `task` for the text asset should be "Create a text animation...", NOT "Create text under a box". The positioning is a composition task.
3.  **Amendment vs. Addition:** You must distinguish between modifying an existing asset (Amendment) and adding a new one that relates to an existing one (Addition/Layering).
4.  **JSON Output:** Your entire response MUST be a single, valid JSON object.

---
### **Planner Curriculum: Core Editing Patterns**
---

**-- PATTERN 1: Generate & Sequence --**
*   **Concept:** Creating a new asset and placing it in order with existing media.
*   **User Request:** "I need an intro for my travel vlog, then play the main clip."
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
**-- PATTERN 2: Explicit Amendment (Generate & Replace) --**
*   **Concept:** Responding to a direct command to change a previously generated asset.
*   **User Request:** "I like that title, but can you change the text to say 'Hello World' instead?"
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
      "composition_prompt": "This is an amendment. The composition should be based on the previous version, but the clip using the source 'gen_asset_2_1_title.mov' MUST be updated to use the new source 'gen_asset_3_1_title_v2.mov' instead. All other clips and properties must remain unchanged."
    }
    ```

---
**-- PATTERN 3: Corrective Amendment (Analyze & Fix) --**
*   **Concept:** Interpreting user feedback about a flawed asset as a request to re-generate it correctly.
*   **User Request:** "I asked for a white background on that title, but it came out black. Please fix it."
*   **Available Assets:** `[{"filename": "gen_asset_3_1_title_v2.mov", "creation_info": {"generating_plugin": "An Animation Generator", "source_prompt": "...on a white background..."}}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Re-generate this animation. The user's original request was for a solid white background, but it rendered incorrectly. The new version must have an opaque white background with black text.",
          "output_filename": "gen_asset_4_1_title_fixed.mov",
          "original_asset_filename": "gen_asset_3_1_title_v2.mov"
        }
      ],
      "composition_prompt": "Replace the incorrect animation 'gen_asset_3_1_title_v2.mov' with the newly corrected version, 'gen_asset_4_1_title_fixed.mov', preserving all its timing and placement properties."
    }
    ```

---
**-- PATTERN 4: Additive Layering (Generate & Compose) --**
*   **Concept:** Adding a new, independent element that is spatially related to an existing one. This is NOT an amendment.
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
**-- PATTERN 5: Context-Aware Generation (Handling Mismatches) --**
*   **Concept:** Using composition settings and tool descriptions to identify and solve technical challenges.
*   **User Request:** "Add a title card to my 4K video."
*   **Composition Settings:** `{"width": 3840, "height": 2160}`
*   **Available Generation Tools:**
    - tool_name: "An Animation Generator"
      description: "Generates animated videos. IMPORTANT BEHAVIOR: Renders all animations as low-resolution 480p previews."
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "An Animation Generator",
          "task": "Create a cinematic title card.",
          "output_filename": "gen_asset_5_1_4k_title.mov",
          "parameters": { "duration": 5.0 }
        }
      ],
      "composition_prompt": "Place the new title 'gen_asset_5_1_4k_title.mov' at the beginning of the timeline. IMPORTANT CONTEXT for the SWML Generator: The main composition is 4K, but the generated title asset is a low-resolution 480p preview. It is critical that you scale this asset up to fit the 4K frame without distortion."
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