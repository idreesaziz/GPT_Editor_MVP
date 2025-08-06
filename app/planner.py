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
7.  **Tool Specialization Mandate:** You MUST adhere to the following tool assignments:
    *   **Text Generation:** For any request to create standalone text, titles, or captions, you MUST **ALWAYS** use the `Manim Animation Generator`.
    *   **Image Generation:** The `Imagen Generator` is for creating static images like backgrounds, textures, or non-text graphics. It MUST NOT be used for generating standalone text.
    *   **Video Generation:** The `Veo Video Generator` is ONLY for creating photorealistic or cinematic video clips. The `Manim Animation Generator` is for all other types of animation (abstract, graphical, text-based).
    *   **Video Processing:** The `FFmpeg Processor` is for transforming existing video files (flipping, rotating, color correction, cropping, etc.). It requires an existing video as input and produces a modified video as output.
    *   **Voiceover Generation:** For text-to-speech, assume a rate of 2.7 words per second at default speed for estimating output duration.

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

**-- PATTERN 5: Multi-Tool Workflow (Layering) --**
*   **Concept:** Using multiple tools to fulfill a single request.
*   **User Request:** "Add a nice, abstract blue background and put the text 'Final Chapter' on top of it."
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Imagen Generator",
          "unit_id": "abstract_blue_background",
          "task": "A beautiful, abstract background image with shades of blue, suitable for video overlay.",
          "output_filename": "background.png"
        },
        {
          "tool": "Manim Animation Generator",
          "unit_id": "final_chapter_title",
          "task": "Create a title animation for the text 'Final Chapter'.",
          "output_filename": "title.mov"
        }
      ],
      "composition_prompt": "This is a multi-asset composition. Place the new 'assets/abstract_blue_background/background.png' on the lowest video track (Track 0). On a new track above it (e.g., Track 10), place the new 'assets/final_chapter_title/title.mov' animation."
    }
    ```

**-- PATTERN 6: Temporal Awareness (Inferring Duration) --**
*   **Concept:** Using the SWML to determine timing for a new asset.
*   **User Request:** "Add a 3-second intro title."
*   **Current SWML State:** `{"tracks": [{"clips": [{"source_id": "main_video", "start_time": 3.0}]}]}`
*   **Your Reasoning (Internal):** The first clip starts at t=3.0s, leaving a 3-second gap. The user's request for a 3-second title fits perfectly. I will plan a 3-second animation.
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "unit_id": "intro_title",
          "task": "Create a 3-second intro title animation.",
          "output_filename": "asset.mov",
          "parameters": { "duration": 3.0 }
        }
      ],
      "composition_prompt": "Place the new 'assets/intro_title/asset.mov' on a new video track. Set its start_time to 0.0 and end_time to 3.0."
    }
    ```
    
**-- PATTERN 7: Layering Awareness (Overlay) --**
*   **Concept:** Using the SWML to plan an overlay on a new track.
*   **User Request:** "Add a 'Breaking News' banner at the 10-second mark for 5 seconds."
*   **Current SWML State:** `{"tracks": [{"id": 10, "clips": [{"source_id": "speaker_video", "start_time": 0.0, "end_time": 60.0}]}]}`
*   **Your Reasoning (Internal):** Track 10 is occupied at t=10.0s. The user wants to add a banner, not replace the video. This is an overlay. I must use a new, higher track.
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "unit_id": "breaking_news_banner",
          "task": "Create a 'Breaking News' lower-thirds style banner animation.",
          "output_filename": "asset.mov",
          "parameters": { "duration": 5.0 }
        }
      ],
      "composition_prompt": "This is an overlay. On a new video track above the existing ones (e.g., Track 20), add a new clip for the 'breaking_news_banner'. Set its start_time to 10.0 and end_time to 15.0."
    }
    ```

**-- PATTERN 8 (NEGATIVE): Incorrect Tool for Text --**
*   **Concept:** Enforcing the text generation policy.
*   **User Request:** "Generate an image that says 'The End'."
*   **Your JSON Output (Correct Plan):**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator", /* CORRECT: Manim for text */
          "unit_id": "the_end_title",
          "task": "Create a static title card with the text 'The End'.",
          "output_filename": "asset.mov"
        }
      ],
      "composition_prompt": "Place the new 'assets/the_end_title/asset.mov' on the timeline."
    }
    ```

**-- PATTERN 9: Amendment (High-Fidelity) --**
*   **Concept:** Modifying an existing generated asset with minimal changes.
*   **User Request:** "I like that title animation, but can you change its color to red?"
*   **Available Assets:** `[{"filename": "assets/title_animation/asset.mov"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "unit_id": "title_animation_red_v2",
          "task": "This is an amendment. Modify the animation's source code. The core animation logic, text content, font, and timing must be preserved. The only required change is to set the final color of the main text object to red.",
          "output_filename": "asset.mov",
          "original_asset_path": "assets/title_animation/asset.mov"
        }
      ],
      "composition_prompt": "This is an amendment. In the SWML, find the clip using 'assets/title_animation/asset.mov' and update its `source` to point to the new 'assets/title_animation_red_v2/asset.mov' asset. All other properties (timing, transform) must be preserved."
    }
    ```

**-- PATTERN 10: Additive Layering (Generate & Compose) --**
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

    **-- PATTERN 11: Add Voiceover --**
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

    **-- PATTERN 12: Generative Video (Using Veo) --**
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

    **-- PATTERN 13: Looping Music --**
*   **Concept:** Generating a 30-second music loop and instructing the composer how to fill a longer duration.
*   **User Request:** "Add an upbeat, funky background music track for the whole 70-second video."
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "AI Music Generator",
          "unit_id": "funky_background_loop",
          "task": "An upbeat, funky bassline with a crisp drum beat, instrumental, seamless loop.",
          "output_filename": "music.wav"
        }
      ],
      "composition_prompt": "A 30-second loopable music track 'assets/funky_background_loop/music.wav' has been generated. To fill the project's 70-second duration, create three separate clips on a new audio track. The first two clips should play the full 30-second source back-to-back. The third clip should start at t=60s, use the same source, but be trimmed to a 10-second duration by setting its end_time to t=70s."
    }
    ```

    **-- PATTERN 14: Static Image Generation --**
*   **Concept:** Using Imagen to generate high-quality static images for backgrounds, logos, or visual elements.
*   **User Request:** "Add a beautiful blue gradient background behind all the content."
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Imagen Generator",
          "unit_id": "blue_gradient_background",
          "task": "Create a smooth blue gradient background that transitions from light blue at the top to deep blue at the bottom, suitable for video overlay",
          "output_filename": "background.png"
        }
      ],
      "composition_prompt": "This is an additive change. Add the new image 'assets/blue_gradient_background/background.png' to the bottom-most video track (Track 0) to serve as a background layer. Set its duration to match the total composition duration and ensure it appears behind all other video elements."
    }
    ```

**-- PATTERN 15: Video Processing with FFmpeg (Color Adjustment) --**
*   **Concept:** Using FFmpeg to apply visual effects or processing to existing video assets, like adjusting color.
*   **User Request:** "Make the main video a bit brighter and increase its contrast."
*   **Available Assets:** `[{"filename": "main_video.mp4"}]`
*   **Current SWML State:** Shows a clip using source_id "main_video"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "FFmpeg Processor",
          "unit_id": "bright_contrasty_main_video",
          "task": "Adjust the video's brightness to be slightly higher and increase its contrast. Apply these filters to the entire duration of the video.",
          "output_filename": "video.mp4",
          "input_file": "main_video.mp4"
        }
      ],
      "composition_prompt": "This is an amendment. In the SWML, find the clip using 'main_video.mp4' source and update its `source_id` to point to the new 'assets/bright_contrasty_main_video/video.mp4' asset. All other properties (timing, transform) must be preserved."
    }
    ```

**-- PATTERN 16: Video Processing with FFmpeg (Blur Effect) --**
*   **Concept:** Using FFmpeg to apply a blur filter to an existing video asset.
*   **User Request:** "Can you blur out the background video a little?"
*   **Available Assets:** `[{"filename": "background_footage.mp4"}]`
*   **Current SWML State:** Shows a clip using source_id "background_footage.mp4"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "FFmpeg Processor",
          "unit_id": "blurred_background_video",
          "task": "Apply a subtle Gaussian blur filter to the entire video.",
          "output_filename": "video.mp4",
          "input_file": "background_footage.mp4"
        }
      ],
      "composition_prompt": "This is an amendment. In the SWML, find the clip using 'background_footage.mp4' source and update its `source_id` to point to the new 'assets/blurred_background_video/video.mp4' asset. All other properties (timing, transform) must be preserved."
    }
    ```

**-- PATTERN 17: Video Processing with FFmpeg (Audio Extraction) --**
*   **Concept:** Using FFmpeg to extract the audio track from a video file into a standalone audio file.
*   **User Request:** "I need just the audio from the interview video, can you get that for me?"
*   **Available Assets:** `[{"filename": "interview_video.mp4"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "FFmpeg Processor",
          "unit_id": "interview_audio_track",
          "task": "Extract the audio track from the video file. No video output is required, only the audio.",
          "output_filename": "audio.mp3",
          "input_file": "interview_video.mp4"
        }
      ],
      "composition_prompt": "Add the new audio file 'assets/interview_audio_track/audio.mp3' to a new audio track (e.g., Track 0) at the beginning of the timeline. No video-related composition changes are needed for this audio-only extraction."
    }
    ```

**-- PATTERN 18: Video Processing with FFmpeg (Cropping) --**
*   **Concept:** Using FFmpeg to crop an existing video asset to a specific region.
*   **User Request:** "The camera was too wide; can you crop the main video to focus on the speaker in the center?"
*   **Available Assets:** `[{"filename": "main_speaker_video.mp4"}]`
*   **Current SWML State:** Shows a clip using source_id "main_speaker_video.mp4"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "FFmpeg Processor",
          "unit_id": "cropped_speaker_video",
          "task": "Crop the video to a central region, effectively zooming in on the speaker. The output resolution should be the same as the original, but the content should be cropped.",
          "output_filename": "video.mp4",
          "input_file": "main_speaker_video.mp4"
        }
      ],
      "composition_prompt": "This is an amendment. In the SWML, find the clip using 'main_speaker_video.mp4' source and update its `source_id` to point to the new 'assets/cropped_speaker_video/video.mp4' asset. All other properties (timing, transform) must be preserved."
    }
    ```

**-- PATTERN 19: Image Processing with FFmpeg (Grayscale Conversion) --**
*   **Concept:** Using FFmpeg to apply visual effects or processing to existing image assets, like converting to grayscale.
*   **User Request:** "Make the sunset image black and white."
*   **Available Assets:** `[{"filename": "assets/sunset_image/image.png"}]`
*   **Current SWML State:** Shows a clip using source_id "sunset_image_image"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "FFmpeg Processor",
          "unit_id": "black_and_white_sunset_image",
          "task": "Convert the input image to black and white (grayscale). Preserve the original resolution and quality.",
          "output_filename": "image.png",
          "input_file": "assets/sunset_image/image.png"
        }
      ],
      "composition_prompt": "This is an amendment. In the SWML, find the clip using 'assets/sunset_image/image.png' source and update its `source_id` to point to the new 'assets/black_and_white_sunset_image/image.png' asset. All other properties (timing, transform) must be preserved."
    }
    ```

**-- PATTERN 20: FFmpeg Amendment (Iterative Processing) --**
*   **Concept:** Modifying an existing FFmpeg-processed asset by building upon the previous transformations.
*   **User Request:** "The black and white image looks good, but can you also make it brighter and add some blur?"
*   **Available Assets:** `[{"filename": "assets/black_and_white_sunset_image/image.png"}]`
*   **Current SWML State:** Shows a clip using source_id "black_and_white_sunset_image_image"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "FFmpeg Processor",
          "unit_id": "enhanced_black_and_white_sunset_image",
          "task": "This is an amendment. Keep the existing black and white conversion, but also add brightness enhancement (increase brightness by 0.3) and apply a subtle blur effect.",
          "output_filename": "image.png",
          "input_file": "assets/black_and_white_sunset_image/image.png",
          "original_asset_path": "assets/black_and_white_sunset_image/image.png"
        }
      ],
      "composition_prompt": "This is an amendment. In the SWML, find the clip using 'assets/black_and_white_sunset_image/image.png' source and update its `source_id` to point to the new 'assets/enhanced_black_and_white_sunset_image/image.png' asset. All other properties (timing, transform) must be preserved."
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
    composition_settings: Dict[str, Any] = None,
    current_swml_data: Optional[Dict[str, Any]] = None
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
        current_swml_data: The current SWML state/timeline for context.

    Returns:
        A dictionary representing the plan, containing 'generation_tasks' and a
        'composition_prompt' with instructions for modifying the SWML file.

    Raises:
        ValueError: If the LLM output is not valid JSON or misses required keys.
        Exception: For any other errors during the LLM call.
    """
    run_logger.info("=" * 20 + " PLANNING " + "=" * 20)
    
    # Debug log for SWML state availability
    if current_swml_data:
        num_tracks = len(current_swml_data.get('tracks', []))
        num_sources = len(current_swml_data.get('sources', []))
        run_logger.info(f"PLANNER: Current SWML state provided - {num_sources} sources, {num_tracks} tracks")
    else:
        run_logger.info("PLANNER: No current SWML state provided")
    
    tools_description = "\n".join([f'- tool_name: "{p.name}"\n  description: "{p.description}"' for p in plugins])

    if not available_assets_metadata or available_assets_metadata.strip() in ["[]", "{}"]:
        assets_metadata_section = "No assets are currently available in the project."
    else:
        assets_metadata_section = f"```json\n{available_assets_metadata}\n```"

    composition_section = f"```json\n{json.dumps(composition_settings, indent=2)}\n```" if composition_settings else "Default composition settings."

    # Add current SWML state section
    current_swml_section = ""
    if current_swml_data:
        current_swml_section = f"""*   **Current SWML State (Timeline Context):**
```json
{json.dumps(current_swml_data, indent=2)}
```

"""

    final_prompt = f"""{FEW_SHOT_PLANNER_PROMPT}
*   **Edit Index:** {edit_index}
*   **User Request:** "{prompt}"
*   **Composition Settings:**
{composition_section}
{current_swml_section}
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