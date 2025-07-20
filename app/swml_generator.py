import logging
import google.generativeai as genai
import json
from typing import Dict, Any, List, Optional

from .utils import Timer

logger = logging.getLogger(__name__)
GENERATOR_MODEL_NAME = "gemini-2.5-flash"
swml_model = genai.GenerativeModel(GENERATOR_MODEL_NAME)

def generate_swml(
    prompt: str,
    current_swml: Dict[str, Any],
    prompt_history: List[str],
    run_logger: logging.Logger,
    last_error: Optional[str] = None,
    last_warnings: Optional[str] = None,
    available_assets_metadata: Optional[str] = None
) -> Dict[str, Any]:
    run_logger.info("=" * 20 + " SWML GENERATION " + "=" * 20)
    
    # The system instruction for this LLM call
    system_prompt = """
You are an expert AI assistant that generates and edits declarative video compositions in a JSON format called SWML.
Your task is to take a user's editing request, an existing SWML JSON object, and optionally feedback from a previous attempt,
and produce a new, **modified** SWML JSON object that reflects the user's desired changes.

**CRITICAL RULES:**
1.  Respond ONLY with a single, complete, valid JSON object representing the new SWML. Do not include any explanations, markdown, or other text.
2.  **You are updating an existing SWML.** Preserve IDs of existing sources, tracks, and clips unless the user explicitly asks to remove or replace them.
3.  Work from the provided "Current SWML". Your output must be the full, new version of the SWML, not just a snippet.
4.  Ensure all paths in the `sources` list are just filenames, not full paths.
5.  If "Feedback from Previous Attempt" is provided, **PRIORITIZE fixing any errors or addressing warnings** while still fulfilling the user's "New Composition Instruction". Do not deviate from the core user request.
6.  **ADHERE STRICTLY to the SWML Specification provided below.** Do not invent new fields, use incorrect types, or deviate from the specified structure and validation rules.

--- SWML SPECIFICATION ---

**Top-Level Structure:**
SWML is a JSON object with these top-level keys: `composition`, `sources`, `tracks`.

1.  **`composition` Object:** Defines the overall video properties.
    *   `width`: (Number, Integer) Video width in pixels. Required. Must be > 0.
    *   `height`: (Number, Integer) Video height in pixels. Required. Must be > 0.
    *   `fps`: (Number, Integer) Frames per second. Required. Must be > 0.
    *   `duration`: (Number, Float/Integer) Total length of the composition in seconds. **Optional.** If omitted, calculated from latest clip. If provided but invalid, defaults to 10.0.
    *   `output_format`: (String) Output video format. Optional. Allowed: "mp4", "mov", "webm". Default: "mp4".

    *Example:*
    ```json
    "composition": {
        "width": 1920, "height": 1080, "fps": 30, "duration": 60.0, "output_format": "mp4"
    }
    ```

2.  **`sources` Array of Objects:** Defines all media assets available for use.
    *   Each object represents one source:
        *   `id`: (String) Unique identifier for the source (e.g., "my_video", "background_music"). Required.
        *   `path`: (String) Filename only (e.g., "input.mp4", "image.png"). Required. Must exist on disk.

    *Example:*
    ```json
    "sources": [
        { "id": "intro_vid", "path": "intro.mp4" },
        { "id": "bg_music", "path": "music.mp3" }
    ]
    ```

3.  **`tracks` Array of Objects:** Defines parallel layers of video/audio.
    *   Each object represents one track:
        *   `id`: (Number, Integer) **Unique numeric ID for layering (lower = background). Required.**
        *   `type`: (String) Track type. Optional. Allowed: "video", "audio". Default: "video".
        *   `clips`: (Array of Clip Objects) List of clips on this track. Optional.
        *   `transitions`: (Array of Transition Objects) List of transitions on this track. Optional.

    *Example:*
    ```json
    "tracks": [
        { "id": 10, "type": "video", "clips": [ ... ], "transitions": [ ... ] },
        { "id": 20, "type": "audio", "clips": [ ... ] }
    ]
    ```

4.  **`clips` Array of Objects (within a Track):** Defines a piece of a source on a track.
    *   Each object represents one clip:
        *   `id`: (String) Unique identifier for the clip within its track. Required.
        *   `source_id`: (String) References an `id` from the `sources` array. Required.
        *   `start_time`: (Number, Float/Integer) Start time of the clip *on its track* in seconds. **Optional.** Default: 0.0.
        *   `end_time`: (Number, Float/Integer) End time of the clip *on its track* in seconds. **Optional.**
            *   **Defaults:** For images, `start_time + 5.0`. For video/audio, `start_time + (source_duration - source_start)`.
            *   If `end_time <= start_time`, duration becomes minimum (1 frame).
        *   `source_start`: (Number, Float/Integer) Start time within the *source asset* in seconds. **Optional.** Default: 0.0. (Clamped to source duration if too large).
        *   `transform`: (Transform Object) For position, scale, rotation. Optional. **Only for video tracks.** Ignored for audio.
        *   `volume`: (Number, Float/Integer) Volume multiplier (0.0-1.0+). Optional. Default: 1.0. **Only for audio tracks.**
        *   `fade_in`: (Number, Float/Integer) Fade-in duration in seconds. Optional. Default: 0.0. **Only for audio tracks.**
        *   `fade_out`: (Number, Float/Integer) Fade-out duration in seconds. Optional. Default: 0.0. **Only for audio tracks.**

    *Example Clip:*
    ```json
    {
        "id": "clip_i_1", "source_id": "i", "start_time": 0.0, "end_time": 5.0, "source_start": 0.0,
        "transform": { "x": 0.5, "y": 0.5, "scaleX": 1.0, "scaleY": 1.0 },
        "audio": { "volume": 0.8, "fade_in": 1.0 }
    }
    ```

5.  **`transform` Object (within a Clip):**
    *   `size`: (Object) Defines clip size. Optional.
        *   `pixels`: (Array [width, height] of Numbers) Size in pixels.
        *   `scale`: (Array [scale_x, scale_y] of Numbers) Scaling factor (1.0 is original size). Values clamped to minimum 0.001.
    *   `position`: (Object) Defines clip position. Optional. Defaults to center of composition.
        *   `pixels`: (Array [x, y] of Numbers) Position from top-left of composition.
        *   `cartesian`: (Array [x, y] of Numbers) Position from -1.0 to 1.0 (center is 0,0). **`cartesian` takes precedence over `pixels` if both present.**
    *   `anchor`: (Object) Defines the anchor point for transformations. Optional. Defaults to center of clip.
        *   `pixels`: (Array [x, y] of Numbers) Position from top-left of clip.
        *   `cartesian`: (Array [x, y] of Numbers) Position from -1.0 to 1.0 relative to clip. **`cartesian` takes precedence over `pixels` if both present.**
    *   `rotation`: (Number, Float/Integer) Rotation in degrees. Optional.

    *Example Transform:*
    ```json
    "transform": {
        "size": { "scale": [0.5, 0.5] },
        "position": { "cartesian": [0.25, 0.25] },
        "rotation": 45.0
    }
    ```

6.  **`transitions` Array of Objects (within a Track):**
    *   Each object represents one transition:
        *   `from_clip`: (String) ID of the outgoing clip. Optional (but one of `from_clip` or `to_clip` must be present).
        *   `to_clip`: (String) ID of the incoming clip. Optional (but one of `from_clip` or `to_clip` must be present).
        *   `duration`: (Number, Float/Integer) Transition duration in seconds. **Required.** Default: 1.0. Clamped to minimum 1 frame duration, and to actual overlap for cross-transitions.
        *   `effect`: (String) Transition effect. Optional. Allowed: "fade", "dissolve", "wipe". Default: "fade".
        *   `direction`: (String) Required only if `effect` is "wipe". Allowed: "left_to_right", "right_to_left", "top_to_bottom", "bottom_to_top".

    *Example Transition (Cross-fade):*
    ```json
    { "from_clip": "clip_a", "to_clip": "clip_b", "duration": 1.0, "effect": "fade" }
    ```
    *Example Transition (Fade-in):*
    ```json
    { "to_clip": "clip_c", "duration": 0.5 }
    ```

--- END SWML SPECIFICATION ---
"""
    # Create a history of prompts for context
    formatted_history = "\n".join([f"- '{p}'" for p in prompt_history]) if prompt_history else "This is the initial version or no prior prompts exist."

    # Prepare feedback sections
    feedback_section = ""
    if last_error:
        feedback_section += f"ERROR: The previous SWML failed to render with the following issue:\n```\n{last_error}\n```\n"
    if last_warnings:
        feedback_section += f"WARNINGS: The previous render generated these warnings:\n```\n{last_warnings}\n```\n"
    if feedback_section:
        feedback_section = "\n**Feedback from Previous Attempt:**\n" + feedback_section
    else:
        feedback_section = "\nNo specific errors or warnings from the previous attempt.\n"

    # Prepare available assets metadata section
    assets_metadata_section = ""
    if available_assets_metadata:
        assets_metadata_section = f"""
**Available Assets Details (Metadata for files in the 'sources' list):**
```json
{available_assets_metadata}
```
"""

    user_prompt = f"""
Full Project History (Previous User Prompts):
{formatted_history}

Current SWML State (The base you are modifying):

{json.dumps(current_swml, indent=2)}
{assets_metadata_section}
{feedback_section}

New Composition Instruction:
"{prompt}"

Your Task:
Generate the new, complete SWML file that incorporates the new composition instruction, taking into account the current state and any feedback.
Your new SWML (JSON only):
"""

    with Timer(run_logger, "SWML Generation LLM Call & Parsing"):
        run_logger.debug(f"--- SWML GEN PROMPT ---\n{user_prompt}\n--- END ---")
        try:
            # Use generation_config to force JSON output
            response = swml_model.generate_content(
                f"{system_prompt}\n{user_prompt}",
                generation_config={"response_mime_type": "application/json"}
            )
            
            # With response_mime_type="application/json", response.text is guaranteed to be valid JSON
            new_swml = json.loads(response.text)

            run_logger.info("SWML_GEN: Successfully generated and parsed new SWML.")
            return new_swml
        except (json.JSONDecodeError, ValueError) as e:
            # This block should be hit less often with response_mime_type, but good for robustness
            raw_response_text = response.text if 'response' in locals() else 'N/A (No response object)'
            run_logger.error(f"SWML Generator failed to create a valid plan. Error: {e}. Raw response:\n{raw_response_text}")
            raise ValueError(f"The SWML Generator failed to create valid JSON. Error: {e}")
        except Exception as e:
            run_logger.error(f"An unexpected error occurred in the SWML generator: {e}", exc_info=True)
            raise