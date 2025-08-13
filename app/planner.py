# app/planner.py

import logging
import google.generativeai as genai
from google import genai as vertex_genai
from google.genai import types
from google.genai.types import HttpOptions
import json
import os
from typing import List, Dict, Any, Optional
from .plugins.base import ToolPlugin
from .utils import Timer

logger = logging.getLogger(__name__)

PLANNER_MODEL_NAME = "gemini-2.5-flash"

# Check if we should use Vertex AI
USE_VERTEX_AI = os.getenv("USE_VERTEX_AI", "false").lower() == "true"

if USE_VERTEX_AI:
    vertex_client = vertex_genai.Client(
        vertexai=True,
        project=os.getenv("VERTEX_PROJECT_ID"),
        location=os.getenv("VERTEX_LOCATION", "us-central1")
    )
    planner_model = None  # We'll use the client directly
else:
    api_key = os.getenv("GOOGLE_API_KEY")
    if not api_key:
        raise ValueError("GOOGLE_API_KEY environment variable not found or not set.")
    genai.configure(api_key=api_key)
    planner_model = genai.GenerativeModel(PLANNER_MODEL_NAME)

FEW_SHOT_PLANNER_PROMPT = """
You are an expert AI video production planner. Your primary goal is to create editing plans for a specific composition engine called the **Swimlane Engine**. You must operate *strictly* within the documented capabilities of this engine. If a task cannot be accomplished using the engine's features, you MUST delegate it to a generation tool. **DO NOT HALLUCINATE or assume any capabilities not explicitly listed below.**

---
### **Swimlane Composition Engine Capabilities (CRITICAL CONTEXT)**
---
The Swimlane Engine is a declarative renderer using a JSON format (SWML). Your `composition_prompt` must describe changes that are possible *only* within this system. Your knowledge of this engine is limited to the features listed here and nothing more.

**CAPABILITIES (Compositional Tasks):**
*   **Static Transforms:** You can set a clip's `position`, `size` (scale), `anchor` point, and `flip` (horizontal/vertical mirroring). These are *static* properties for the entire duration of the clip.
*   **Visual Effects:** You can apply color grading (brightness, contrast, saturation, gamma, hue, RGB channel adjustments), rotation, and LUT (Look-Up Table) effects to clips.
*   **Timing:** You can set a clip's `start_time`, `end_time`, and `source_start` (trimming).
*   **Layering:** Clips can be layered on different tracks. Tracks can be of type "video", "audio", or "audiovideo".
*   **Transitions:** The engine supports `fade`, `wipe`, and `dissolve` transitions *between clips*. A fade on a single clip (in or out) is also possible.
*   **Audio:** You can adjust a clip's `volume` and apply `fade_in` or `fade_out`.
*   **Background Color:** You can set a `background_color` for the entire composition as an RGB array (values 0.0-1.0), which renders as a full-screen color strip behind all content.

**LIMITATIONS (Requires Generation Task):**
*   **NO KEYFRAME ANIMATION:** The engine **cannot** animate properties over time (e.g., animate position, scale, or rotation). Any request for an animated transform MUST be a `generation_task`. **Note:** Static effects (color grading, rotation angles, LUT application) are supported but cannot be animated over time.
*   **NO BUILT-IN TEXT GENERATION:** The engine **cannot** create text. All text must be generated as a new asset (e.g., a PNG image with transparency) via a `generation_task`.
*   **STATIC EFFECTS ONLY:** While the engine supports color grading, rotation, and LUT effects, these are *static* for the entire duration of the clip. Dynamic effects (like animating brightness over time) require a `generation_task`.
*   **ABSOLUTE RULE:** If a requested capability is not explicitly in the `CAPABILITIES` list above, you MUST assume it is a limitation and create a `generation_task`.

---
### **Your Core Principles (CRITICAL):**
1.  **Composition First Principle:** For any request that involves changing the static timing, position, scale, flip, color grading, rotation, LUT effects, or adding a supported transition to *existing* clips, you MUST default to a composition-only solution. `generation_tasks` must be `[]`.
2.  **The Generation Rule:** Any request that cannot be fulfilled by the Swimlane Engine's documented capabilities MUST be delegated as a `generation_task`.
3.  **Unique Unit ID:** For each task in `generation_tasks`, you MUST provide a unique `unit_id`. This ID should be a descriptive, snake-case string that represents the asset being created (e.g., `main_title_animation`, `intro_narration_s1`).
4.  **Clean Generation Tasks:** Instructions for NEW assets must be pure and self-contained. The `output_filename` should be a simple, generic name like `asset.mov` or `image.png`, as it will be placed inside a unique directory named after the `unit_id`.
5.  **Session File Integration:** When relevant files from the current session are available, include them in generation tasks using the `session_files` parameter. This enables plugins (especially Manim) to create comprehensive solutions by combining multiple elements in a single generation rather than requiring manual composition.
6.  **JSON Output:** Your entire response MUST be a single, valid JSON object.
7.  **Strict Adherence to Limitations:** Your `composition_prompt` can ONLY describe operations that are explicitly listed in the `Swimlane Composition Engine Capabilities`.
8.  **Tool Specialization Mandate:** You MUST adhere to the following tool assignments:
    *   **Text Generation:** For any request to create standalone text, titles, or captions, you MUST **ALWAYS** use the `Manim Animation Generator`.
    *   **Image Generation:** The `Imagen Generator` is for creating static images like backgrounds, textures, or non-text graphics. It MUST NOT be used for generating standalone text.
    *   **Video Generation:** The `Veo Video Generator` is ONLY for creating photorealistic or cinematic video clips. The `Manim Animation Generator` is for all other types of animation (abstract, graphical, text-based).
    *   **Video Processing:** The `FFmpeg Processor` is for advanced transformations that exceed Swimlane's built-in capabilities (complex filters, format conversion, audio extraction, advanced cropping, etc.). For basic color adjustments (brightness, contrast, saturation, hue), prefer Swimlane's built-in effects system over FFmpeg processing.
    *   **Voiceover Generation:** For text-to-speech, assume a rate of 2.7 words per second at default speed for estimating output duration.

---
### **Duration Parameter Guidelines (CRITICAL):**
---
**Intelligently calculate duration parameters for Manim-generated assets based on content and context:**

1. **Manim Animation Generator Only (Context-Aware Duration Calculation):**

   **A) Content-Based Duration Analysis:**
   - **Text Reading Time:** Calculate based on word count: (word_count ÷ 3.5 words/second) + 1.5s buffer
   - **Character Count Fallback:** For short text: (character_count ÷ 15 chars/second) + 2s minimum
   - **Multi-line Text:** Add 0.8s per additional line for eye movement and comprehension
   - **Complex Content:** Technical terms, numbers, or dense information → add 30-50% extra time

   **B) Contextual Timeline Awareness:**
   - **Available Gap Analysis:** If timeline has a specific gap (e.g., 4.2s), match that duration precisely
   - **Overlay Timing:** If overlaying existing content, match or slightly exceed the underlying content duration
   - **Sequential Content:** If following another element, ensure smooth pacing (not too rushed/slow)
   - **User Intent Signals:** "quick intro" → minimal viable reading time, "detailed explanation" → extended duration

   **C) Professional Timing Baselines (Use as minimums, not fixed values):**
   - **Simple titles:** Minimum 3-4s, extend based on content length
   - **Lower thirds:** Minimum 4-5s, extend for longer names/titles  
   - **Full explanations:** Minimum 6-8s, scale significantly with content complexity
   - **Background elements:** Match primary content duration + 1-2s buffer

2. **Other Tools (No Duration Parameters):**
   - **Veo Video Generator:** Generates natural cinematic timing (typically 5-8 seconds)
   - **Imagen Generator:** Static images - duration handled by composition layer
   - **AI Music Generator:** Generates natural loop durations (typically 30 seconds)
   - **FFmpeg Processor:** Maintains original source duration
   - **Voiceover Generator:** Auto-calculated from text length (word_count ÷ 2.7 words/second)

3. **Duration Calculation Examples (Manim Only):**
   - **"Welcome"** → 3.5s (1 word ÷ 3.5 + 1.5s buffer = 2.9s → round up to 3.5s)
   - **"Breaking News Alert"** → 4.5s (3 words ÷ 3.5 + 1.5s = 2.4s → minimum viable + buffer = 4.5s)
   - **"The Revolutionary New Product Launch Event"** → 7.0s (6 words ÷ 3.5 + 1.5s = 3.2s → complex content +30% = 4.2s → professional minimum = 7.0s)
   - **Multi-line lower third:** "Dr. Sarah Johnson\nChief Technology Officer\nInnovative Solutions Inc." → 8.5s (9 words ÷ 3.5 + 1.5s + 1.6s for extra lines = 6.7s → round up = 8.5s)

**CRITICAL RULES:** 
- Only pass duration parameters to Manim Animation Generator
- ALWAYS analyze actual content length and context before setting duration
- Never use fixed duration ranges - calculate based on actual requirements
- Consider timeline gaps, overlay timing, and user intent signals

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
      "composition_prompt": "This is a composition-only change. Move the clip using 'assets/welcome_title/title.png' source to the top-right corner of the frame. Do not generate any new assets."
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
      "composition_prompt": "This is a composition-only change. Add a fade transition at the end of the timeline for the 'final_clip.mov'. The transition should have a duration of 1 second to create a fade-to-black."
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
          "tool": "Manim Animation Generator",
          "unit_id": "hello_world_title_v2",
          "task": "Change the text to 'Hello World' while keeping the same animation style, font, timing, and visual effects.",
          "output_filename": "asset.mov",
          "parameters": { "duration": 6.0 },
          "original_asset_path": "assets/title_animation/asset.mov"
        }
      ],
      "composition_prompt": "This is an amendment. Replace the clip using 'assets/title_animation/asset.mov' with the new asset 'assets/hello_world_title_v2/asset.mov'. Preserve all timing and transform properties."
    }
    ```

**-- PATTERN 5: Multi-Element Integration (Session Files) --**
*   **Concept:** Using session files to create comprehensive solutions in a single generation rather than manual composition.
*   **User Request:** "Add a nice, abstract blue background and put the text 'Final Chapter' on top of it."
*   **Session Files Available:** `["background_template.png", "logo.png"]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "unit_id": "final_chapter_composite",
          "task": "Create a comprehensive animation with: 1) An abstract blue background (similar style to background_template.png if available), 2) The text 'Final Chapter' positioned prominently on top. Handle all layout, timing, and visual harmony in a single cohesive animation.",
          "output_filename": "animation.mov",
          "parameters": { "duration": 7.0 },
          "session_files": ["background_template.png"]
        }
      ],
      "composition_prompt": "Add the comprehensive animation 'assets/final_chapter_composite/animation.mov' to the timeline. No additional positioning or layering needed as all elements are integrated."
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
          "output_filename": "asset.mov",
          "parameters": { "duration": 5.0 }
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
          "task": "Change the text color to red while preserving the animation logic, text content, font, and timing exactly as they are.",
          "output_filename": "asset.mov",
          "parameters": { "duration": 6.0 },
          "original_asset_path": "assets/title_animation/asset.mov"
        }
      ],
      "composition_prompt": "This is an amendment. Replace the clip using 'assets/title_animation/asset.mov' with the new asset 'assets/title_animation_red_v2/asset.mov'. Preserve all timing and transform properties."
    }
    ```

**-- PATTERN 10: Session-Aware Enhancement --**
*   **Concept:** Using existing session files to enhance new content generation.
*   **User Request:** "Add the text 'My Cool Product' right underneath the blue box, but make it match the style of our logo."
*   **Available Assets:** `[{"filename": "assets/blue_box/asset.mov"}]`
*   **Session Files Available:** `["company_logo.png", "brand_colors.json"]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "unit_id": "styled_product_text",
          "task": "Create a text animation for 'My Cool Product' that matches the styling and branding of the company logo. Position it appropriately underneath the blue box element. Incorporate brand colors and typography consistent with the logo design.",
          "output_filename": "asset.mov",
          "parameters": { "duration": 6.0 },
          "session_files": ["company_logo.png", "brand_colors.json"]
        }
      ],
      "composition_prompt": "Add the new branded text animation 'assets/styled_product_text/asset.mov' to a new video track. The animation is already positioned relative to the blue box, so no additional placement needed."
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
      "composition_prompt": "This is an amendment. Replace the clip using 'main_video.mp4' with the new processed asset 'assets/bright_contrasty_main_video/video.mp4'. Preserve all timing and transform properties."
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
      "composition_prompt": "This is an amendment. Replace the clip using 'background_footage.mp4' with the new processed asset 'assets/blurred_background_video/video.mp4'. Preserve all timing and transform properties."
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
      "composition_prompt": "This is an amendment. Replace the clip using 'main_speaker_video.mp4' with the new processed asset 'assets/cropped_speaker_video/video.mp4'. Preserve all timing and transform properties."
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
      "composition_prompt": "This is an amendment. Replace the clip using 'assets/sunset_image/image.png' with the new processed asset 'assets/black_and_white_sunset_image/image.png'. Preserve all timing and transform properties."
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
          "task": "Keep the existing black and white conversion, but also increase the brightness by 0.3 and apply a subtle blur effect.",
          "output_filename": "image.png",
          "input_file": "assets/black_and_white_sunset_image/image.png",
          "original_asset_path": "assets/black_and_white_sunset_image/image.png"
        }
      ],
      "composition_prompt": "This is an amendment. Replace the clip using 'assets/black_and_white_sunset_image/image.png' with the new processed asset 'assets/enhanced_black_and_white_sunset_image/image.png'. Preserve all timing and transform properties."
    }
    ```

**-- PATTERN 21: Background Color Setting (Composition-Only) --**
*   **Concept:** Setting a background color is a composition-level change that doesn't require asset generation.
*   **User Request:** "Set the background to a dark blue color."
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. Set the composition background color to dark blue. This will render as a full-screen color strip behind all other content."
    }
    ```

**-- PATTERN 22: Track Type Specification (Composition-Only) --**
*   **Concept:** Organizing content using appropriate track types for better audio/video management.
*   **User Request:** "I want to add that background music and put the main video on a separate track."
*   **Available Assets:** `[{"filename": "background_music.mp3"}, {"filename": "main_video.mp4"}]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. Create two tracks: Track 10 with type 'audiovideo' for the main_video.mp4 clip, and Track 20 with type 'audio' for the background_music.mp3 clip. This ensures proper audio mixing and track organization."
    }
    ```

**-- PATTERN 23: Color Grading Effects (Composition-Only) --**
*   **Concept:** Applying color adjustments to existing clips using the new effects system.
*   **User Request:** "Make the main video brighter and more contrasty with a warmer look."
*   **Available Assets:** `[{"filename": "main_video.mp4"}]`
*   **Current SWML State:** Shows a clip using source_id "main_video"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. Apply color grading effects to the clip using 'main_video.mp4' source: increase brightness by 20%, increase contrast by 30%, and add a warm look by enhancing reds and reducing blues."
    }
    ```

**-- PATTERN 24: LUT Preset Application (Composition-Only) --**
*   **Concept:** Applying cinematic looks using built-in LUT presets.
*   **User Request:** "Give the video a cinematic teal and orange look."
*   **Available Assets:** `[{"filename": "video_clip.mp4"}]`
*   **Current SWML State:** Shows a clip using source_id "video_clip"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. Apply a cinematic LUT effect to the clip using 'video_clip.mp4' source: use the 'cinema' preset at 80% strength to create a modern teal and orange cinematic look."
    }
    ```

**-- PATTERN 25: Rotation Effect (Composition-Only) --**
*   **Concept:** Applying static rotation to clips using the new effects system.
*   **User Request:** "Rotate the title by 15 degrees clockwise."
*   **Available Assets:** `[{"filename": "assets/title_card/title.mov"}]`
*   **Current SWML State:** Shows a clip using source_id "title_card_title"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. Apply a rotation effect to the clip using 'assets/title_card/title.mov' source: rotate clockwise by 15 degrees."
    }
    ```

**-- PATTERN 26: Complex Effects Combination (Composition-Only) --**
*   **Concept:** Combining multiple effects (color grading, LUT, rotation) on a single clip.
*   **User Request:** "Make the video black and white, slightly rotate it, and add a vintage look."
*   **Available Assets:** `[{"filename": "retro_footage.mp4"}]`
*   **Current SWML State:** Shows a clip using source_id "retro_footage"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. Apply multiple effects to the clip using 'retro_footage.mp4' source: 1) remove all color saturation to make it black and white, 2) apply a subtle counter-clockwise rotation of 3 degrees, and 3) add a vintage LUT preset at 60% strength for an aged look."
    }
    ```

**-- PATTERN 27: Flip Transform (Composition-Only) --**
*   **Concept:** Applying horizontal or vertical flip to clips using static transforms.
*   **User Request:** "Flip the video horizontally to mirror it."
*   **Available Assets:** `[{"filename": "main_video.mp4"}]`
*   **Current SWML State:** Shows a clip using source_id "main_video"
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [],
      "composition_prompt": "This is a composition-only change. Apply a horizontal flip transform to the clip using 'main_video.mp4' source to create a mirrored effect."
    }
    ```

**-- PATTERN 28: Multi-Asset Integration (Session Files) --**
*   **Concept:** Creating complex animations by integrating multiple existing assets and session files.
*   **User Request:** "Create an intro sequence that combines our logo, the sunset video, and adds a welcome message."
*   **Available Assets:** `[{"filename": "sunset_video.mp4"}]`
*   **Session Files Available:** `["company_logo.png", "brand_colors.json", "intro_music.mp3"]`
*   **Your JSON Output:**
    ```json
    {
      "generation_tasks": [
        {
          "tool": "Manim Animation Generator",
          "unit_id": "complete_intro_sequence",
          "task": "Create a comprehensive intro sequence that: 1) Incorporates the sunset video as a background element, 2) Features the company logo with appropriate branding, 3) Displays a welcome message with brand-consistent typography, 4) Coordinates all elements with proper timing and transitions. Use the brand colors for consistency.",
          "output_filename": "asset.mov",
          "parameters": { "duration": 8.0 },
          "session_files": ["company_logo.png", "brand_colors.json"],
          "reference_assets": ["sunset_video.mp4"]
        }
      ],
      "composition_prompt": "Replace or enhance the timeline with the comprehensive intro sequence 'assets/complete_intro_sequence/asset.mov'. The animation integrates all requested elements, so minimal additional composition is needed."
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
    current_swml_data: Optional[Dict[str, Any]] = None,
    session_files: Optional[List[str]] = None
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
        session_files: A list of file paths from the current session that can be referenced by plugins.

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

    # Add session files section
    session_files_section = ""
    if session_files:
        session_files_section = f"""*   **Session Files Available for Reference:**
{', '.join(session_files)}

"""

    final_prompt = f"""{FEW_SHOT_PLANNER_PROMPT}
*   **Edit Index:** {edit_index}
*   **User Request:** "{prompt}"
*   **Composition Settings:**
{composition_section}
{current_swml_section}{session_files_section}
{tools_description}
*   **Available Assets:**
{assets_metadata_section}
*   **Your JSON Output:**
"""

    with Timer(run_logger, "Planner LLM Call & Parsing"):
        run_logger.debug(f"--- PLANNER PROMPT ---\n{final_prompt}\n--- END ---")
        response_text = ""
        try:
            if USE_VERTEX_AI:
                response = vertex_client.models.generate_content(
                    model=PLANNER_MODEL_NAME,
                    contents=final_prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json"
                    )
                )
                response_text = response.text
            else:
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