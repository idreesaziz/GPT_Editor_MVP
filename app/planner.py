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
You are an expert AI video production planner. Your job is to analyze a user's request, the project's composition settings, and the available assets/tools, then deconstruct it into a detailed JSON plan. The plan has two parts:

1.  `generation_tasks`: A list of new media assets that need to be created. If none are needed, this MUST be an empty list `[]`.
2.  `composition_prompt`: A clear, conceptual, and descriptive instruction in plain English for a separate AI (the SWML Generator) that will arrange all assets on a timeline. This prompt MUST also include any important context or warnings the SWML Generator needs to properly compose the scene.

**CRITICAL RULES:**
*   Read the tool descriptions carefully to understand their behaviors (e.g., preview resolutions).
*   Use the `Composition Settings` to inform your plan.
*   If a tool's behavior (like generating a low-res preview) conflicts with the desired output (a high-res composition), add a specific instruction in the `composition_prompt` for the SWML Generator to correct it (e.g., "you must scale this asset up").
*   Your entire response MUST be a single, valid JSON object.

---
### **FEW-SHOT EXAMPLES**
---

**-- EXAMPLE 1 --**
*   **Edit Index:** 2
*   **User Request:** "Start with a cool title that says 'Our Japan Adventure', then just show that video I uploaded of Tokyo."
*   **Available Assets:**
    ```json
    [
      { "id": "v1", "filename": "tokyo_footage.mp4", "type": "video", "metadata": { "duration": 45.8 } },
      { "id": "a1", "filename": "background_music.mp3", "type": "audio", "metadata": { "duration": 180.0 } }
    ]
    ```
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "task": "Create an animated title with the text 'Our Japan Adventure'.",
          "output_filename": "gen_asset_2_1_japantitle.mov",
          "parameters": { "duration": 5.0, "style_description": "Modern, clean, perhaps with a subtle red sun or cherry blossom motif." }
        }
      ],
      "composition_prompt": "Start with the new title animation 'gen_asset_2_1_japantitle.mov'. Immediately after it finishes, play the 'tokyo_footage.mp4' video. The 'background_music.mp3' should play underneath everything from the very beginning."
    }
    ```

---
**-- EXAMPLE 2 --**
*   **Edit Index:** 3
*   **User Request:** "Can you put my company logo on the corner of the whole video? Just make it semi-transparent."
*   **Available Assets:**
    ```json
    [
      { "id": "img1", "filename": "company_logo.png", "type": "image", "metadata": { "width": 400, "height": 400 } },
      { "id": "v1", "filename": "promo_video_v1.mp4", "type": "video", "metadata": { "duration": 60.0 } }
    ]
    ```
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "Use 'promo_video_v1.mp4' as the main video content. On a layer above it, place the 'company_logo.png' image. The logo should be scaled down, positioned in the bottom-right corner, and be semi-transparent. It should remain visible for the entire duration of the video."
    }
    ```

---
**-- EXAMPLE 3 --**
*   **Edit Index:** 5
*   **User Request:** "The title animation is too plain. Make it more exciting, maybe with some flashes or something."
*   **Available Assets:**
    ```json
    [
      { "id": "gen_4_1", "filename": "gen_asset_4_1_title.mov", "type": "video", "metadata": { "duration": 5.0 } },
      { "id": "v1", "filename": "main_video.mp4", "type": "video", "metadata": { "duration": 55.0 } }
    ]
    ```
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "task": "Redo the title animation to be more exciting and dynamic, possibly with light flashes or faster text movement.",
          "output_filename": "gen_asset_5_1_title_v2.mov",
          "original_asset_filename": "gen_asset_4_1_title.mov",
          "parameters": { "duration": 5.0, "style_description": "High-energy, dynamic, exciting." }
        }
      ],
      "composition_prompt": "Replace the old title 'gen_asset_4_1_title.mov' with the new, improved title 'gen_asset_5_1_title_v2.mov'. Then, play 'main_video.mp4' as before."
    }
    ```

---
**-- EXAMPLE 4 --**
*   **Edit Index:** 8
*   **User Request:** "I need a video for my new product. Generate an image of a 'sleek, futuristic water bottle'. Then, create an animation where that bottle slowly rotates on a clean, white background for about 10 seconds."
*   **Available Assets:** `[]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Imagen Image Generator",
          "task": "Generate a photorealistic image of a sleek, futuristic water bottle on a plain background.",
          "output_filename": "gen_asset_8_1_bottle.png"
        },
        {
          "tool": "Manim Animation Generator",
          "task": "Create a 10-second animation. Take the image 'gen_asset_8_1_bottle.png' and make it slowly rotate 360 degrees in the center of the screen. The background of the animation should be solid white.",
          "output_filename": "gen_asset_8_2_bottle_anim.mov",
          "parameters": { "duration": 10.0 }
        }
      ],
      "composition_prompt": "Show only the final animation of the rotating bottle, 'gen_asset_8_2_bottle_anim.mov'."
    }
    ```

---
**-- EXAMPLE 5 --**
*   **Edit Index:** 4
*   **User Request:** "Just put the two clips I uploaded together, the interview first."
*   **Available Assets:**
    ```json
    [
      { "id": "v1", "filename": "broll_footage.mp4", "type": "video", "metadata": { "duration": 15.0 } },
      { "id": "v2", "filename": "interview_main.mp4", "type": "video", "metadata": { "duration": 122.0 } }
    ]
    ```
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "Create a sequence of the two videos. Start with 'interview_main.mp4' and follow it immediately with 'broll_footage.mp4'."
    }
    ```

---
**-- EXAMPLE 6 --**
*   **Edit Index:** 6
*   **User Request:** "This is for a YouTube short. I need a title card that says 'My Top 5 Gadgets' over the main video."
*   **Available Assets:**
    ```json
    [
      { "id": "v1", "filename": "gadget_review.mp4", "type": "video", "metadata": { "duration": 59.0 } }
    ]
    ```
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "task": "Create an animated title card with the text 'My Top 5 Gadgets'. The animation should be punchy and attention-grabbing for a short-form video.",
          "output_filename": "gen_asset_6_1_gadget_title.mov",
          "parameters": { "duration": 4.0, "style_description": "Bright, bold, modern, sans-serif font." }
        }
      ],
      "composition_prompt": "Use 'gadget_review.mp4' as the background video. On a layer above it, display the new title animation 'gen_asset_6_1_gadget_title.mov' for its full duration at the beginning of the video."
    }
    ```

---
**-- EXAMPLE 7 --**
*   **Edit Index:** 11
*   **User Request:** "The voiceover is 2 minutes long. Make an explainer video with a blue background and some simple shapes moving around for the whole time."
*   **Available Assets:**
    ```json
    [
      { "id": "a1", "filename": "explainer_voiceover.mp3", "type": "audio", "metadata": { "duration": 120.0 } }
    ]
    ```
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "task": "Create a long animation with a solid blue background. Feature various simple shapes (circles, squares) appearing, moving around the screen, and fading out in a continuous, gentle motion. The animation should not be distracting.",
          "output_filename": "gen_asset_11_1_explainer_bg.mov",
          "parameters": { "duration": 120.0, "style_description": "Minimalist, abstract, gentle motion." }
        }
      ],
      "composition_prompt": "Use the new animation 'gen_asset_11_1_explainer_bg.mov' as the visual track. Use the 'explainer_voiceover.mp3' as the audio track, synchronized to start at the same time."
    }
    ```

---
**-- EXAMPLE 8 --**
*   **Edit Index:** 9
*   **User Request:** "That last animation of the square was good, but make it a hexagon instead."
*   **Available Assets:**
    ```json
    [
      { "id": "gen_8_1", "filename": "gen_asset_8_1_spinning_square.mov", "type": "video", "metadata": { "duration": 10.0 } }
    ]
    ```
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "task": "Modify the animation to use a hexagon instead of a square, keeping all other properties like color and rotation the same.",
          "output_filename": "gen_asset_9_1_spinning_hexagon.mov",
          "original_asset_filename": "gen_asset_8_1_spinning_square.mov"
        }
      ],
      "composition_prompt": "Show only the newly generated animation 'gen_asset_9_1_spinning_hexagon.mov'."
    }
    ```

---
**-- EXAMPLE 9: Handling Resolution Mismatch --**
*   **Edit Index:** 12
*   **User Request:** "I need a big, impressive title for my 4K video."
*   **Composition Settings:** `{"width": 3840, "height": 2160, "fps": 30}`
*   **Available Generation Tools:**
    - tool_name: "Manim Animation Generator"
      description: "Generates animated videos... IMPORTANT BEHAVIOR: For speed, this plugin currently renders all animations as low-resolution previews (e.g., 480p)."
*   **Available Assets:** `[]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "task": "Create a big, impressive title animation.",
          "output_filename": "gen_asset_12_1_4k_title.mov",
          "parameters": { "duration": 7.0, "style_description": "Cinematic, high-impact, bold font." }
        }
      ],
      "composition_prompt": "Place the new title 'gen_asset_12_1_4k_title.mov' in the center of the composition. IMPORTANT CONTEXT: The main composition is 4K (3840x2160), but the generated title asset is a low-resolution preview. It is critical that you scale the title asset up to properly fill the 4K frame."
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