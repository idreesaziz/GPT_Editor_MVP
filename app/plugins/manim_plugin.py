import os
import subprocess
import sys
import ast
import logging
import json
from typing import Tuple, Optional
import shutil

from .base import ToolPlugin

logger = logging.getLogger(__name__)

MANIM_TIMEOUT = 90 # Manim can be slow, even for simple scenes

def _is_video_readable(file_path: str) -> bool:
    """Checks if a video file is readable by ffprobe without errors."""
    if not os.path.exists(file_path):
        return False
    command = ['ffprobe', '-v', 'error', '-show_entries', 'stream=codec_type', file_path]
    try:
        # Use a short timeout for this check
        subprocess.run(command, check=True, capture_output=True, text=True, timeout=15)
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return False

class ManimPlugin(ToolPlugin):
    """Plugin for creating new video clips using Manim."""

    @property
    def name(self) -> str:
        return "Manim Animation Generator"

    @property
    def description(self) -> str:
        return (
            "A tool for creating new, high-quality animated video clips from a textual description. "
            "Use this for generating title cards, text overlays, animated diagrams, mathematical visualizations, and other graphic elements. "
            "This tool does NOT edit existing videos. It creates a new video clip which can then be combined with other videos "
            "using the 'FFmpeg Video Editor' tool in a subsequent step."
        )

    @property
    def prerequisites(self) -> str:
        return "None. This tool generates content from scratch. However, its output properties (like resolution) might be constrained by the requirements of subsequent steps."

    def get_system_instruction(self) -> str:
        """Provides the specific system prompt for generating Manim wrapper scripts."""
        return """
    You are an AI assistant that generates a self-contained Python script to create a video using Manim.
    Your script MUST follow this exact "wrapper script" pattern:
    1.  Define the full Manim scene code inside a multi-line string variable called `manim_script_content`.
    2.  Write this string to a temporary file named `temp_manim_scene.py`.
    3.  Use the `subprocess` module to execute the `manim` command-line tool.
    4.  After a successful render, find the output video in Manim's subdirectory structure.
    5.  Move the final video file to the main working directory.

    **CRITICAL SCRIPTING RULES & DOCUMENTATION:**
    - The scene class inside `manim_script_content` MUST be named `GeneratedScene`.
    - The `manim` command MUST include `--media_dir .` to ensure output files are saved relative to the current directory.
    - The `manim` command MUST include `--progress_bar none` to prevent interactive elements.
    - You MUST use the `-o` flag with the `manim` command to specify the exact output filename.

    **VIDEO PROPERTIES CUSTOMIZATION (CLI / PROGRAMMATIC):**
    - You **can** configure quality using standard quality flags:
    - `-ql` (low quality: 854x480 15fps)
    - `-qm` (medium quality: 1280x720 30fps)  
    - `-qh` (high quality: 1920x1080 60fps)
    - `-qp` (2k quality: 2560x1440 60fps)
    - `-qk` (4k quality: 3840x2160 60fps)
    - You **can** set custom resolution using:
    - `-r WIDTHxHEIGHT` (e.g., `-r 1920x1080`) - note the 'x' separator, NOT comma
    - You **can** set background color using:
    - `-c COLOR` (e.g., `-c BLACK`, `-c WHITE`, `-c "#FF0000"`)
    - You **can** set transparent background using:
    - `-t` flag (outputs .mov format with alpha channel)
    - You **can** set custom output filename using:
    - `-o filename.mp4`
    - **IMPORTANT**: There is NO `--fps` flag in Manim Community. Frame rate is tied to quality presets.
    - **IMPORTANT**: Direct CLI fps setting is not supported. Use quality flags or programmatic config.

    - You **can also** adjust settings programmatically using `tempconfig` inside the scene:
    ```python
    from manim import tempconfig
    # For custom resolution and frame rate:
    with tempconfig({"pixel_width": 1920, "pixel_height": 1080, "frame_rate": 30}):
        scene = GeneratedScene()
        scene.render()
    
    # Or use predefined quality settings:
    with tempconfig({"quality": "medium_quality"}):  # Valid: low_quality, medium_quality, high_quality, production_quality, fourk_quality, example_quality
        scene = GeneratedScene()
        scene.render()
    ```

    **ADVANCED TECHNIQUES:**
    - **Fading the background:** The background color itself cannot be animated directly. To fade the entire scene to a color (e.g., black), you must create a full-screen `Rectangle` of that color and `FadeIn` it as the final animation. Set its z_index high to ensure it's on top.
    Example: `fade_rect = Rectangle(width=100, height=100, fill_color=BLACK, fill_opacity=1, stroke_width=0).set_z_index(999); self.play(FadeIn(fade_rect))`

    **COMMON ERRORS TO AVOID:**
    - Do NOT use `-r WIDTH,HEIGHT` with comma separator - use `x` instead
    - Do NOT use `--fps` flag - it doesn't exist in Manim Community
    - Do NOT access `config.renderer.ffmpeg_args` directly - this attribute doesn't exist
    - Do NOT modify renderer settings outside of tempconfig context
    - Do NOT use `"custom"` as a quality value - use `pixel_width`/`pixel_height` for custom resolution
    - Valid quality values are: `low_quality`, `medium_quality`, `high_quality`, `production_quality`, `fourk_quality`, `example_quality`

    ---
    **EXAMPLE WRAPPER SCRIPT TEMPLATE:**

    import subprocess
    import os
    import shutil

    # --- Part 1: Define the Manim Scene Code ---
    manim_script_content = \"\"\"
    from manim import *

    class GeneratedScene(Scene):
        def construct(self):
            self.camera.background_color = BLACK
            hello_text = Text("Hello World", font_size=96)
            self.play(Write(hello_text))
            self.wait(2)
    \"\"\"

    # --- Part 2: Write, Execute, and Move the Output ---
    scene_script_file = "temp_manim_scene.py"
    try:
        with open(scene_script_file, "w", encoding="utf-8") as f:
            f.write(manim_script_content)

        output_filename = outputs['final_video']
        scene_class_name = "GeneratedScene"
        quality_flag = "-ql"  # Use quality flags, not custom fps

        command = [
            'manim', '--media_dir', '.', quality_flag,
            '-o', output_filename, '--progress_bar', 'none',
            scene_script_file, scene_class_name,
        ]

        result = subprocess.run(command, check=True, capture_output=True, text=True)

        # --- Part 3: Find and Move the final file ---
        # Manim saves files in a nested directory, e.g., ./videos/temp_manim_scene/480p15/
        # We must find the output file and move it to the current directory.
        found_path = None
        videos_dir = os.path.join('.', "videos")
        for root, dirs, files in os.walk(videos_dir):
            if output_filename in files:
                found_path = os.path.join(root, output_filename)
                break
        
        if found_path:
            # Move the file to the current working directory
            shutil.move(found_path, output_filename)
            # Clean up the (now likely empty) media directories
            shutil.rmtree(videos_dir)
        else:
            raise FileNotFoundError(f"Manim ran, but output file '{output_filename}' not found.")

    except Exception as e:
        raise RuntimeError(f"Error in Manim wrapper script: {e}")
    finally:
        if os.path.exists(scene_script_file):
            os.remove(scene_script_file)
    """

    def validate_script(self, script_code: str, sandbox_path: str, inputs: dict, outputs: dict) -> Tuple[bool, Optional[str]]:
        """Validates the complete wrapper script by executing it."""
        try:
            ast.parse(script_code)
        except SyntaxError as e:
            return False, f"[SyntaxError] Invalid Python syntax: {e}"

        inputs_def = f"inputs = {json.dumps(inputs)}"
        outputs_def = f"outputs = {json.dumps(outputs)}"
        full_test_script = f"{inputs_def}\n{outputs_def}\n\n{script_code}"

        script_path_in_sandbox = os.path.join(sandbox_path, "test_script.py")
        with open(script_path_in_sandbox, "w") as f:
            f.write(full_test_script)

        try:
            result = subprocess.run(
                [sys.executable, script_path_in_sandbox],
                cwd=sandbox_path,
                check=True,
                capture_output=True,
                text=True,
                timeout=MANIM_TIMEOUT
            )

            # The script is now responsible for moving the file, so we just check the root.
            output_filename = next(iter(outputs.values()))
            final_expected_path = os.path.join(sandbox_path, output_filename)

            if not os.path.exists(final_expected_path):
                return False, f"[SandboxError] Script ran but the output file '{output_filename}' was not moved to the sandbox root. Stdout: {result.stdout}"

            if not _is_video_readable(final_expected_path):
                 return False, f"[SandboxError] Script produced a corrupt or unreadable output video file: {output_filename}"

            return True, None
        except subprocess.TimeoutExpired:
            return False, f"[SandboxError] Script execution timed out after {MANIM_TIMEOUT} seconds."
        except subprocess.CalledProcessError as e:
            error_msg = f"[SandboxError] Script failed during execution.\n--- Stderr ---\n{e.stderr}"
            if e.stdout:
                error_msg += f"\n--- Stdout ---\n{e.stdout}"
            return False, error_msg
        except Exception as e:
            return False, f"[SandboxError] An unexpected error occurred during validation: {e}"