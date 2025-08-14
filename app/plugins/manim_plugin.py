# app/plugins/manim_plugin.py

import logging
import os
import shutil
import subprocess
import time
import json
from typing import Dict, Optional, List

import google.generativeai as genai
from google import genai as vertex_genai
from google.genai import types
from google.genai.types import HttpOptions

from .base import ToolPlugin

# --- Configuration ---
MANIM_CODE_MODEL = "gemini-2.5-flash"
MAX_CODE_GEN_RETRIES = 3

# Check if we should use Vertex AI
USE_VERTEX_AI = os.getenv("USE_VERTEX_AI", "false").lower() == "true"

# --- Custom Exception ---
class ManimGenerationError(Exception):
    """Custom exception for errors during Manim asset generation."""
    pass

# --- Plugin Definition ---
class ManimAnimationGenerator(ToolPlugin):
    """
    A plugin that generates animated videos using Manim.
    It creates a companion .meta.json file for each generated asset,
    containing the source code needed for future amendments.
    """

    def __init__(self):
        super().__init__()
        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError("GOOGLE_API_KEY environment variable not found or not set.")
        
        if USE_VERTEX_AI:
            self.vertex_client = vertex_genai.Client(
                vertexai=True,
                project=os.getenv("VERTEX_PROJECT_ID"),
                location=os.getenv("VERTEX_LOCATION", "us-central1")
            )
            self.model = None  # We'll use the client directly
        else:
            genai.configure(api_key=api_key)
            self.model = genai.GenerativeModel(MANIM_CODE_MODEL)

    @property
    def name(self) -> str:
        return "Manim Animation Generator"

    @property
    def description(self) -> str:
        return (
            "Generates animated videos from a text description (e.g., titles, explainers). "
            "The output is a .mov file with configurable background (transparent, colored, or image-based). "
            "IMPORTANT BEHAVIOR: For speed, this plugin currently renders all animations as low-resolution previews (e.g., 480p). "
            "The composition step will need to scale these assets up to fit the final video frame."
        )

    def execute_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        prompt = task_details["task"]
        output_filename = task_details["output_filename"] 
        
        # Extract session files and parameters
        session_files = task_details.get("session_files", [])
        reference_assets = task_details.get("reference_assets", [])
        parameters = task_details.get("parameters", {})
        duration = parameters.get("duration")
        background_color = parameters.get("background_color")  # New parameter
        unit_id = task_details.get("unit_id")
        
        run_logger.info(f"MANIM PLUGIN: Starting task for unit '{unit_id}' - '{prompt[:100]}...'.")
        
        if session_files:
            run_logger.info(f"MANIM PLUGIN: Session files available: {session_files}")
        if reference_assets:
            run_logger.info(f"MANIM PLUGIN: Reference assets available: {reference_assets}")
        if duration:
            run_logger.info(f"MANIM PLUGIN: Target duration: {duration} seconds")
        if background_color:
            run_logger.info(f"MANIM PLUGIN: Background color specified: {background_color}")

        # Copy session files and reference assets to working directory
        available_files = self._copy_session_files_to_working_dir(
            session_files, reference_assets, asset_unit_path, run_logger
        )

        last_error = None
        generated_code = None
        
        # Amendment data is now passed directly by the orchestrator
        original_code = task_details.get("original_plugin_data", {}).get("source_code")
        if original_code:
             run_logger.info(f"MANIM PLUGIN: Amendment mode detected. Using provided source code.")

        for attempt in range(MAX_CODE_GEN_RETRIES):
            run_logger.info(f"MANIM PLUGIN: Code generation attempt {attempt + 1}/{MAX_CODE_GEN_RETRIES}.")
            try:
                generated_code = self._generate_manim_code(
                    prompt=prompt,
                    original_code=original_code,
                    last_generated_code=generated_code,
                    last_error=last_error,
                    available_files=available_files,
                    duration=duration,
                    background_color=background_color,
                    run_logger=run_logger
                )
            except Exception as e:
                run_logger.error(f"MANIM PLUGIN: LLM code generation failed: {e}", exc_info=True)
                raise ManimGenerationError(f"LLM call for Manim code generation failed: {e}") from e

            # Script is now created inside the asset unit directory
            script_filename = f"render_script_attempt{attempt+1}.py"
            script_path = os.path.join(asset_unit_path, script_filename)
            with open(script_path, "w") as f:
                f.write(generated_code)

            try:
                run_logger.info(f"MANIM PLUGIN: Executing Manim script: {script_filename} in {asset_unit_path}")
                # The CWD for Manim is now the asset unit's own directory
                self._run_manim_script(script_filename, asset_unit_path, background_color, run_logger)

                # The video will be generated inside asset_unit_path/media/...
                found_video_path = self._find_latest_video(asset_unit_path)
                if found_video_path:
                    run_logger.info(f"MANIM PLUGIN: Found generated video at '{found_video_path}'.")
                    final_output_path = os.path.join(asset_unit_path, output_filename)
                    shutil.move(found_video_path, final_output_path)
                    
                    manim_plugin_data = {"source_code": generated_code}
                    self._create_metadata_file(task_details, asset_unit_path, [output_filename], manim_plugin_data)
                    
                    self._cleanup(asset_unit_path)
                    run_logger.info(f"MANIM PLUGIN: Successfully generated asset '{output_filename}' in unit '{task_details.get('unit_id')}'.")
                    return [output_filename]
                else:
                    last_error = "Manim execution finished, but no video file was found in the output directory."
                    run_logger.warning(f"MANIM PLUGIN: {last_error}")

            except subprocess.CalledProcessError as e:
                last_error = f"Manim execution failed with exit code {e.returncode}.\nStderr:\n{e.stderr}"
                run_logger.warning(f"MANIM PLUGIN: Manim execution failed. Error:\n{e.stderr}")
            finally:
                if os.path.exists(script_path):
                    os.remove(script_path)


        final_error_msg = f"MANIM PLUGIN: Failed to generate a valid Manim animation after {MAX_CODE_GEN_RETRIES} attempts. Last error: {last_error}"
        run_logger.error(final_error_msg)
        raise ManimGenerationError(final_error_msg)

    def _copy_session_files_to_working_dir(self, session_files: List[str], reference_assets: List[str], 
                                         asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        """
        Copy session files and reference assets to the working directory so Manim can access them.
        Returns a list of filenames (not paths) that are available in the working directory.
        """
        available_files = []
        
        # Copy session files
        for file_path in session_files:
            # If file_path is just a filename, we need to construct the full path
            # Session files are typically in the session directory
            if not os.path.isabs(file_path):
                # Extract session ID from asset_unit_path
                # asset_unit_path format: .../sessions/{session_id}/assets/{unit_id}
                session_dir = os.path.dirname(os.path.dirname(asset_unit_path))  # Go up two levels
                full_file_path = os.path.join(session_dir, file_path)
            else:
                full_file_path = file_path
            
            run_logger.debug(f"MANIM PLUGIN: Checking session file path: '{full_file_path}'")
            
            if os.path.exists(full_file_path):
                # Create a meaningful filename that preserves context
                # For 'assets/stronger_blurred_wallpaper/image.jpg' -> 'stronger_blurred_wallpaper_image.jpg'
                if file_path.startswith('assets/'):
                    # Extract the asset name and original filename
                    path_parts = file_path.split('/')
                    if len(path_parts) >= 3:  # assets/asset_name/filename
                        asset_name = path_parts[1]
                        original_filename = path_parts[-1]
                        name_part, ext_part = os.path.splitext(original_filename)
                        filename = f"{asset_name}_{name_part}{ext_part}"
                    else:
                        filename = os.path.basename(full_file_path)
                else:
                    filename = os.path.basename(full_file_path)
                
                dest_path = os.path.join(asset_unit_path, filename)
                try:
                    shutil.copy2(full_file_path, dest_path)
                    available_files.append(filename)
                    run_logger.info(f"MANIM PLUGIN: Copied session file '{full_file_path}' to working directory as '{filename}'")
                except Exception as e:
                    run_logger.warning(f"MANIM PLUGIN: Failed to copy session file '{full_file_path}': {e}")
            else:
                run_logger.warning(f"MANIM PLUGIN: Session file not found: '{full_file_path}' (original: '{file_path}')")
        
        # Copy reference assets  
        for asset_path in reference_assets:
            if os.path.exists(asset_path):
                filename = os.path.basename(asset_path)
                dest_path = os.path.join(asset_unit_path, filename)
                try:
                    shutil.copy2(asset_path, dest_path)
                    available_files.append(filename)
                    run_logger.info(f"MANIM PLUGIN: Copied reference asset '{asset_path}' to working directory as '{filename}'")
                except Exception as e:
                    run_logger.warning(f"MANIM PLUGIN: Failed to copy reference asset '{asset_path}': {e}")
            else:
                run_logger.warning(f"MANIM PLUGIN: Reference asset not found: '{asset_path}'")
        
        return available_files

    def _generate_manim_code(self, prompt: str, original_code: Optional[str], last_generated_code: Optional[str], 
                           last_error: Optional[str], available_files: List[str], duration: Optional[float], 
                           background_color: Optional[str], run_logger: logging.Logger) -> str:
        system_prompt = """
You are an expert Manim developer. Your task is to write a complete, self-contained Python script to generate a single Manim animation.

CRITICAL RULES:
1.  The script must import all necessary components from `manim`.
2.  The script must define a single class named `GeneratedScene` that inherits from `manim.Scene`.
3.  All animation logic MUST be inside the `construct(self)` method of the `GeneratedScene` class.
4.  **AESTHETICS & LAYOUT:** Strive for clean, modern animations. All text and primary visual elements MUST be placed and scaled to be fully visible within the video frame. Use alignment methods like `.move_to(ORIGIN)` or `.to_edge()` to ensure proper composition.
5.  **TEXT HANDLING:** Choose the appropriate text class and strategy based on content length and readability:
    - Use `Text()` class for titles, labels, single words, and headers
    - **MANUAL LINE BREAKS:** For longer text content, manually split sentences/phrases into separate `Text()` objects and arrange them in a `VGroup` with `.arrange(DOWN, buff=0.4)` - this maintains font readability
    - **AVOID WIDTH SCALING:** NEVER use `set_width()` on text objects as it scales down font size making text unreadable
    - **FONT SIZE PRIORITY:** Always use large, readable font sizes (28-36pt minimum). Split content across multiple lines rather than shrinking fonts
    - **MULTI-SLIDE LOGIC:** If text content is extremely long (>300 characters), split it into multiple sequential slides with smooth transitions (see Example 18)
    - **READABILITY FIRST:** Prioritize readability over fitting everything on one slide - split content into multiple lines or slides rather than making fonts too small
6.  **BACKGROUND:** You will be provided with a specific background_color instruction. If specified, add `self.camera.background_color = <COLOR>` at the start of the `construct` method using the exact color provided. If no background_color is specified, DO NOT set any background color (it will render transparently).
7.  Do NOT include any code to render the scene (e.g., `if __name__ == "__main__"`)
8.  If you need to use an external asset like an image, its filename will be provided. Assume it exists in the same directory where the script is run. Use `manim.ImageMobject("filename.png")`.
9.  Your entire response MUST be just the Python code, with no explanations, markdown, or other text.

CRITICAL ERROR PREVENTION RULES:
10. **RATE FUNCTIONS:** ALWAYS use rate functions with the `rate_functions.` prefix (e.g., `rate_functions.smooth`, `rate_functions.ease_out_bounce`). Available options include: `rate_functions.smooth`, `rate_functions.rush_from`, `rate_functions.ease_out_bounce`, `rate_functions.there_and_back`, `rate_functions.ease_in_out_sine`. NEVER use rate functions directly without the prefix (e.g., DON'T use `ease_in_out_sine`, use `rate_functions.ease_in_out_sine`).
11. **OBJECT ATTRIBUTES:** Do NOT assume objects have attributes that aren't shown in examples. For graphs created with `axes.plot()`, use `x_range` parameters from the function call, NOT `graph.x_range` which doesn't exist.
12. **ANIMATION METHODS:** Only use animation methods exactly as shown in examples. For arrows, use `Create()` or `DrawBorderThenFill()` instead of `GrowArrow()` which has parameter compatibility issues.
13. **METHOD PARAMETERS:** Only use parameters that are demonstrated in the examples. For line methods like `get_vertical_line()` and `get_horizontal_line()`, use `.set_stroke()` or `.set_color()` on the returned object instead of passing `stroke_opacity` directly.
14. **DATA TYPES:** Ensure all parameters match expected data types. Points must be proper 3D arrays [x, y, 0], colors must be valid Manim colors, and numeric values must be appropriate ranges.
15. **COORDINATE SYSTEMS:** When working with axes and coordinate systems, always use proper coordinate conversion methods like `axes.coords_to_point(x, y)` instead of assuming direct coordinate access.

To guide your code generation, you must study the following examples of high-quality, correct Manim code. Adhere to the patterns, styles, and classes shown in these examples to ensure your output is valid. **These examples serve as a strict reference for valid Manim syntax and animation patterns; however, the creative content and specific visual design of your animation must be driven solely by the user's request.**

Example 1: CoreAnimationsShowcase
This scene demonstrates the fundamental patterns for creating, transforming, and animating objects, including Write, FadeIn, Uncreate, basic .animate syntax, Transform, and group animations. It provides a solid foundation for understanding core Manim animation techniques.

from manim import *

class ManimCoreAnimationsShowcase(Scene):
    A showcase of the most essential Manim animations.
    This example demonstrates the core patterns for creating, transforming,
    and animating objects, providing a lean and effective reference.
    def construct(self):
        # 1. --- Introduction and Setup ---
        # Create a title and a few basic shapes to work with throughout the scene.
        title = Text("Core Manim Animations", font_size=36).to_edge(UP, buff=0.5)
        self.play(Write(title))

        # Create a VGroup for easy management of our shapes
        shapes = VGroup(
            Circle(color=BLUE, fill_opacity=0.7),
            Square(color=GREEN, fill_opacity=0.7),
            Triangle(color=YELLOW, fill_opacity=0.7)
        ).arrange(RIGHT, buff=1)
        self.add(shapes)
        self.wait(1)

        # 2. --- Creation and Destruction Animations ---
        # The most fundamental ways to make objects appear and disappear.
        
        # Create a new shape to demonstrate with
        star = Star(color=RED, fill_opacity=0.7).move_to(shapes[0].get_center())
        
        # Use FadeIn for a smooth appearance
        self.play(FadeIn(star, scale=0.5))
        self.wait(0.5)

        # Use Uncreate to make it disappear
        self.play(Uncreate(star))
        self.wait(0.5)
        
        # 3. --- The .animate Syntax ---
        # The most common and flexible way to animate property changes.

        # Animate movement using .shift()
        self.play(shapes[0].animate.shift(UP * 1.5))
        self.wait(0.5)

        # Animate scaling using .scale()
        self.play(shapes[1].animate.scale(1.5))
        self.wait(0.5)
        
        # Animate rotation using .rotate()
        self.play(shapes[2].animate.rotate(PI / 2))
        self.wait(0.5)

        # Chain multiple .animate calls for a combined effect
        self.play(
            shapes[0].animate.shift(DOWN * 1.5).set_color(PURPLE),
            shapes[1].animate.scale(1/1.5).set_color(ORANGE),
            shapes[2].animate.rotate(-PI / 2).set_color(PINK)
        )
        self.wait(1)

        # 4. --- Transformation Animations ---
        # Morphing one object into another.

        # Transform the square into a star
        new_star = Star(n=12, color=GREEN, fill_opacity=0.7).move_to(shapes[1].get_center())
        self.play(Transform(shapes[1], new_star))
        self.wait(1)

        # 5. --- Group Animations ---
        # Animating multiple objects as a single unit using VGroup.

        # Animate the entire group
        self.play(shapes.animate.to_edge(DOWN, buff=1).scale(0.9))
        self.wait(1)

        # 6. --- Text-Specific Animations ---
        # Animations designed specifically for text objects.
        
        final_text = Text("Animation Complete!", font_size=42)
        # Use Write for a "drawing" effect
        self.play(Write(final_text))
        self.wait(1)
        
        # Fade out all elements to end the scene cleanly
        self.play(
            FadeOut(title),
            FadeOut(shapes),
            FadeOut(final_text)
        )
        self.wait(0.5)

Example 3: WaveOverlay
A dynamic overlay animation demonstrating continuous motion with updaters. This scene visualizes the concept of harmonic interference by layering multiple, differently colored sine waves that move and evolve over time, creating a hypnotic, fluid background effect.

from manim import *
import numpy as np

class WaveOverlay(Scene):
    def construct(self):
        # Create background
        background = Rectangle(width=14, height=8, fill_color=DARK_BLUE, fill_opacity=0.3)
        self.add(background)
        
        # Create multiple sine waves with different properties
        axes = Axes(
            x_range=[-4, 4, 1],
            y_range=[-2, 2, 1],
            axis_config={"color": BLUE_A, "stroke_opacity": 0.3}
        )
        
        # Wave functions
        wave1 = axes.plot(lambda x: np.sin(x), color=YELLOW, stroke_width=4)
        wave2 = axes.plot(lambda x: 0.7 * np.sin(2*x), color=PINK, stroke_width=4)
        wave3 = axes.plot(lambda x: 0.5 * np.cos(3*x), color=GREEN, stroke_width=4)
        
        # Animated overlay waves
        def update_wave1(mob, dt):
            new_wave = axes.plot(
                lambda x: np.sin(x + self.renderer.time * 2), 
                color=YELLOW, 
                stroke_width=4
            )
            mob.become(new_wave)
            
        def update_wave2(mob, dt):
            new_wave = axes.plot(
                lambda x: 0.7 * np.sin(2*x - self.renderer.time * 3), 
                color=PINK, 
                stroke_width=4
            )
            mob.become(new_wave)
            
        def update_wave3(mob, dt):
            new_wave = axes.plot(
                lambda x: 0.5 * np.cos(3*x + self.renderer.time * 1.5), 
                color=GREEN, 
                stroke_width=4
            )
            mob.become(new_wave)
        
        # Add waves with updaters
        wave1.add_updater(update_wave1)
        wave2.add_updater(update_wave2)
        wave3.add_updater(update_wave3)
        
        # Title overlay
        title = Text("Harmonic Wave Interference", font_size=48, color=WHITE)
        title.to_edge(UP)
        
        # Animate everything
        self.play(Create(axes), run_time=2)
        self.play(Write(title))
        self.add(wave1, wave2, wave3)
        self.wait(8)


Example 4: TextOverlayEffect
An eye-catching text overlay featuring a dynamic 'glitch' effect. This scene combines a floating particle background with a central title that is rapidly distorted using a Succession of quick shift and color-change animations. It demonstrates how to create complex, fast-paced effects by sequencing simple animations.

class TextOverlayEffect(Scene):
    def construct(self):
        # ... (all the setup code for bg_rects, particles, titles is the same) ...
        # Background with gradient-like effect
        bg_rects = VGroup()
        for i in range(20):
            rect = Rectangle(
                width=0.8, height=10,
                fill_color=interpolate_color(DARK_BLUE, PURPLE, i/19),
                fill_opacity=0.3,
                stroke_width=0
            )
            rect.move_to([-7.6 + i * 0.8, 0, 0])
            bg_rects.add(rect)
        
        self.add(bg_rects)
        
        # Main title
        main_title = Text("DYNAMIC", font_size=72, color=WHITE, weight=BOLD)
        main_title.move_to(UP * 1.5)
        
        subtitle = Text("overlay effects", font_size=36, color=YELLOW)
        subtitle.move_to(DOWN * 0.5)
        
        # Animated background elements
        particles = VGroup()
        for i in range(30):
            dot = Dot(radius=0.02, color=WHITE, fill_opacity=0.6)
            dot.move_to([
                np.random.uniform(-7, 7),
                np.random.uniform(-4, 4),
                0
            ])
            particles.add(dot)
        
        def float_particles(mob, dt):
            for particle in mob:
                pos = particle.get_center()
                new_pos = pos + np.array([
                    0.5 * np.sin(self.renderer.time + pos[1]) * dt,
                    0.3 * np.cos(self.renderer.time * 0.7 + pos[0]) * dt,
                    0
                ])
                
                if new_pos[0] > 8: new_pos[0] = -8
                elif new_pos[0] < -8: new_pos[0] = 8
                
                particle.move_to(new_pos)
                
                opacity = 0.3 + 0.3 * np.sin(self.renderer.time * 2 + pos[0] + pos[1])
                particle.set(fill_opacity=opacity)
        
        particles.add_updater(float_particles)
        
        # Add everything to scene
        self.add(particles)
        
        self.play(
            Write(main_title, run_time=2),
            Write(subtitle, run_time=1.5)
        )
        
        # --- RECOMMENDED GLITCH EFFECT IMPLEMENTATION ---
        # Build a list of animations to be played in sequence.
        
        # We define a glitch duration that is at least 1 frame long to avoid warnings.
        # This makes the code robust to different frame rates.
        glitch_run_time = 1 / self.camera.frame_rate
        
        glitch_sequence = []
        for _ in range(15): # More glitches for a smoother feel
            displacement = np.array([
                np.random.uniform(-0.1, 0.1),
                np.random.uniform(-0.05, 0.05),
                0
            ])
            colors = [RED, GREEN, BLUE, YELLOW, PINK]
            new_color = np.random.choice(colors)

            # Animation to glitch "on"
            anim_on = main_title.animate.shift(displacement).set_color(new_color)
            
            # Animation to glitch "off" (revert)
            anim_off = main_title.animate.shift(-displacement).set_color(WHITE)
            
            # Add the sequence: ON -> OFF -> WAIT
            glitch_sequence.append(anim_on)
            glitch_sequence.append(anim_off)
            glitch_sequence.append(Wait(np.random.uniform(0.1, 0.3)))

        # Play the entire sequence in one go.
        # We set the run_time for the shifting animations inside the Succession.
        self.play(
            Succession(
                *glitch_sequence, 
                run_time_per_animation=glitch_run_time
            ),
        )
        self.wait() # Add a final wait to see the result

Example 5: LowerThirds
A professional 'Lower Thirds' graphic designed for video overlays. This complex, multi-stage animation demonstrates how to build sophisticated information graphics with sleek design, including layered elements, text reveals, and accent animations. It's a practical example for content creators. Note: This example uses transparent background since it's designed as an overlay element.
    
from manim import *

class LowerThirds(Scene):
    def construct(self):
        # Transparent background for overlay use (no background color set)
        # If this were a standalone graphic, you could set: self.camera.background_color = BLACK
        
        # Create the main background bar - sleek black
        main_bar = Rectangle(
            width=9, 
            height=1.4, 
            fill_color=BLACK,
            fill_opacity=0.9,
            stroke_width=2,
            stroke_color=WHITE,
            stroke_opacity=0.3
        )
        
        # Create blue accent bar
        accent_bar = Rectangle(
            width=0.5,
            height=1.4, 
            fill_color=BLUE,
            fill_opacity=1.0,
            stroke_width=0
        )
        
        # Create a subtle shadow
        shadow_bar = Rectangle(
            width=9.05,
            height=1.45,
            fill_color=BLACK,
            fill_opacity=0.4,
            stroke_width=0
        )
        
        # Position everything in the lower third - better positioning
        shadow_bar.to_edge(DOWN, buff=1.2).to_edge(LEFT, buff=0.7)
        shadow_bar.shift(DOWN * 0.03 + RIGHT * 0.03)
        
        main_bar.to_edge(DOWN, buff=1.2).to_edge(LEFT, buff=0.7)
        accent_bar.align_to(main_bar, LEFT).align_to(main_bar, UP).align_to(main_bar, DOWN)
        
        # Create text with proper contrast
        name_text = Text(
            "JOHN SMITH",
            font_size=38,
            color=WHITE,
            weight=BOLD
        )
        
        title_text = Text(
            "Senior Software Engineer",
            font_size=26,
            color="#CCCCCC",  # Light gray for contrast
            weight=NORMAL
        )
        
        # Position text INSIDE the main bar with proper alignment
        text_container = VGroup(name_text, title_text)
        text_container.arrange(DOWN, buff=0.15, aligned_edge=LEFT)
        
        # --- THIS IS THE CORRECTED PART ---
        # Position the text group relative to the accent bar for clean left alignment
        text_container.next_to(accent_bar, RIGHT, buff=0.4)
        
        # Create white decorative line
        deco_line = Line(
            start=ORIGIN,
            end=RIGHT * 1.5,
            color=WHITE,
            stroke_width=3,
            stroke_opacity=0.8
        )
        deco_line.next_to(title_text, DOWN, buff=0.1)
        deco_line.align_to(title_text, LEFT)
        
        # Create animated dots in blue
        dots = VGroup()
        for i in range(3):
            dot = Dot(radius=0.05, color=BLUE, fill_opacity=1.0)
            dots.add(dot)
        dots.arrange(RIGHT, buff=0.12)
        dots.next_to(deco_line, RIGHT, buff=0.3)
        dots.align_to(deco_line, DOWN)
        
        # Group everything for animations
        all_bars = VGroup(shadow_bar, main_bar, accent_bar)
        all_text = VGroup(name_text, title_text, deco_line)
        
        # ANIMATIONS - Start BARS off-screen left
        all_bars.shift(LEFT * 15)
        
        # 1. Slide in shadow first
        self.play(
            shadow_bar.animate.shift(RIGHT * 15),
            rate_func=rush_from,
            run_time=0.5
        )
        
        # 2. Main bar and accent slide in together
        self.play(
            main_bar.animate.shift(RIGHT * 15),
            accent_bar.animate.shift(RIGHT * 15),
            rate_func=smooth,
            run_time=0.8
        )
        
        # 3. Text appears with typewriter effect
        self.play(
            Write(name_text),
            run_time=1.0
        )
        
        # 4. Title fades in
        self.play(
            FadeIn(title_text, shift=UP * 0.2),
            run_time=0.6
        )
        
        # 5. Decorative line draws in
        self.play(
            Create(deco_line),
            run_time=0.8
        )
        
        # 6. Dots appear one by one
        for dot in dots:
            self.play(
                GrowFromCenter(dot),
                run_time=0.15
            )
        
        # 7. Accent bar pulse effect
        self.play(
            accent_bar.animate.set_fill(color="#4A90E2"),  # Lighter blue
            run_time=0.4
        )
        self.play(
            accent_bar.animate.set_fill(color=BLUE),  # Back to original
            run_time=0.4
        )
        
        # 8. Subtle dot animation
        dot_wave = AnimationGroup(
            *[
                Succession(
                    dot.animate.scale(1.3).set_fill(opacity=0.6),
                    dot.animate.scale(1.0).set_fill(opacity=1.0),
                    rate_func=smooth
                )
                for dot in dots
            ],
            lag_ratio=0.3
        )
        
        self.play(dot_wave, run_time=1.2)
        
        # Hold on screen
        self.wait(2.5)
        
        # 9. Clean exit - everything slides out left
        everything = VGroup(all_bars, all_text, dots)
        self.play(
            everything.animate.shift(LEFT * 15),
            rate_func=smooth,
            run_time=1.2
        )

Example 8: Proper Text Formatting Example
The example demonstrates various techniques for effectively displaying text in Manim, focusing on readability and fitting content within screen boundaries. It covers automatic line breaks with Paragraph, manual line splitting, responsive title/subtitle layouts, and bullet points.

from manim import *

class ProperTextFormattingExample(Scene):
    def construct(self):
        
        Exemplar: How to properly format text in Manim.
        - Break long content into chunks
        - Use Paragraph for wrapping
        - Keep sizes consistent across methods
        - Arrange elements with VGroup instead of manual shifting
        - Incorporate images effectively with text
        

        # -------------------------
        # Helper function
        # -------------------------
        def create_paragraph(text, font_size=36, width=8, line_spacing=1.1, align="center"):
            Create a nicely formatted paragraph.
            para = Paragraph(
                text,
                font_size=font_size,
                line_spacing=line_spacing,
                alignment=align
            )
            para.set_width(width)  # reasonable width so font isn't tiny
            return para

        # -------------------------
        # Method 1: Manual line breaks
        # -------------------------
        manual_lines = [
            "When dealing with very long content,",
            "break it into digestible chunks",
            "that fit comfortably on screen",
            "and are easy to read"
        ]
        manual_group = VGroup(
            *[Text(line, font_size=36) for line in manual_lines]
        ).arrange(DOWN, buff=0.5).move_to(ORIGIN)

        for line in manual_group:
            self.play(FadeIn(line), run_time=0.6)
        self.wait(1.5)
        self.play(FadeOut(manual_group))

        # -------------------------
        # Method 2: Bullet points
        # -------------------------
        bullets = [
            "• Keep text within 8–10 units width",
            "• Use font sizes between 28–54 for readability",
            "• Leave margins on all sides",
            "• Break long content into multiple scenes"
        ]
        bullet_group = VGroup(
            *[Text(point, font_size=34) for point in bullets]
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.5).move_to(ORIGIN)

        if bullet_group.height > 7.5:
            bullet_group.scale_to_fit_height(7.0)

        for bullet in bullet_group:
            self.play(FadeIn(bullet), run_time=0.5)
        self.wait(2)
        self.play(FadeOut(bullet_group))

        # -------------------------
        # Method 3: Best Practices Summary
        # -------------------------
        best_title = Text("Best Practices Summary", font_size=48, weight=BOLD)
        practices = [
            "Start with larger font sizes (28–36pt minimum)",
            "Use multiple shorter paragraphs instead of one long one",
            "Avoid aggressive scaling — split content instead",
            "Test readability at your target resolution"
        ]
        practices_group = VGroup(
            *[Text(p, font_size=30) for p in practices]
        ).arrange(DOWN, buff=0.5)

        final_group = VGroup(best_title, practices_group).arrange(DOWN, buff=0.8)
        final_group.move_to(ORIGIN)

        self.play(Write(best_title))
        for p in practices_group:
            self.play(FadeIn(p), run_time=0.6)
        self.wait(2)
        self.play(FadeOut(final_group))

        # -------------------------
        # Method 4: Incorporating Images with Text
        # -------------------------
        
        # Title for image section
        image_title = Text("Incorporating Images", font_size=48, weight=BOLD)
        self.play(Write(image_title))
        self.wait(1)
        self.play(image_title.animate.to_edge(UP, buff=0.5))

        # Example 1: Side-by-side layout
        example1_title = Text("Side-by-Side Layout", font_size=36, weight=BOLD)
        example1_title.next_to(image_title, DOWN, buff=0.8)
        
        # Create placeholder image (rectangle with text)
        placeholder_img = Rectangle(width=3, height=2, color=BLUE, fill_opacity=0.3)
        img_label = Text("Image", font_size=24).move_to(placeholder_img.get_center())
        image_placeholder = VGroup(placeholder_img, img_label)
        
        # Text content for side-by-side
        side_text = VGroup(
            Text("• Images should complement text", font_size=28),
            Text("• Maintain consistent spacing", font_size=28),
            Text("• Keep proportions balanced", font_size=28),
            Text("• Use appropriate image sizes", font_size=28)
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.3)
        
        # Arrange side by side
        side_by_side = VGroup(image_placeholder, side_text).arrange(RIGHT, buff=1.0)
        side_by_side.next_to(example1_title, DOWN, buff=0.5)
        
        self.play(Write(example1_title))
        self.play(FadeIn(image_placeholder), run_time=0.8)
        for text_line in side_text:
            self.play(FadeIn(text_line), run_time=0.4)
        self.wait(2)
        
        # Clear for next example
        self.play(FadeOut(VGroup(example1_title, side_by_side)))

        # Example 2: Text wrapping around image
        example2_title = Text("Text with Integrated Images", font_size=36, weight=BOLD)
        example2_title.next_to(image_title, DOWN, buff=0.8)
        
        # Small image in corner
        small_img = Rectangle(width=2, height=1.5, color=GREEN, fill_opacity=0.3)
        small_label = Text("Chart", font_size=20).move_to(small_img.get_center())
        small_image = VGroup(small_img, small_label)
        
        # Main text content
        main_text = VGroup(
            Text("When incorporating charts or diagrams,", font_size=32),
            Text("position them strategically within your layout.", font_size=32),
            Text("This creates visual flow and maintains", font_size=32),
            Text("reader engagement throughout the content.", font_size=32)
        ).arrange(DOWN, buff=0.4)
        
        # Position elements
        small_image.to_edge(RIGHT, buff=1.0)
        main_text.to_edge(LEFT, buff=1.0)
        
        content_group = VGroup(main_text, small_image)
        content_group.next_to(example2_title, DOWN, buff=0.6)
        
        self.play(Write(example2_title))
        self.play(FadeIn(small_image), run_time=0.8)
        for text_line in main_text:
            self.play(FadeIn(text_line), run_time=0.5)
        self.wait(2)
        
        # Clear for final example
        self.play(FadeOut(VGroup(example2_title, content_group)))

        # Example 3: Image guidelines
        guidelines_title = Text("Image Integration Guidelines", font_size=36, weight=BOLD)
        guidelines_title.next_to(image_title, DOWN, buff=0.8)
        
        guidelines = [
            "• Scale images to 2–4 units width for clarity",
            "• Leave 0.5–1.0 unit buffer around images",
            "• Use VGroup to manage text-image relationships",
            "• Consider image aspect ratios in your layout",
            "• Test visibility at your target resolution"
        ]
        
        guidelines_group = VGroup(
            *[Text(guideline, font_size=30) for guideline in guidelines]
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.4)
        guidelines_group.next_to(guidelines_title, DOWN, buff=0.6)
        
        # Add a visual example - positioned to stay within frame
        example_img = Rectangle(width=2.5, height=1.8, color=YELLOW, fill_opacity=0.2)
        example_label = Text("Well-sized\nImage", font_size=20).move_to(example_img.get_center())
        example_visual = VGroup(example_img, example_label)
        
        # Position the entire layout to ensure everything fits
        main_content = VGroup(guidelines_group, example_visual).arrange(RIGHT, buff=0.8)
        main_content.next_to(guidelines_title, DOWN, buff=0.6)
        
        # Ensure the whole group fits within frame bounds
        if main_content.width > 12:  # Manim's default frame width is ~14
            main_content.scale_to_fit_width(11)
        
        self.play(Write(guidelines_title))
        self.play(FadeIn(example_visual), run_time=0.8)
        for guideline in guidelines_group:
            self.play(FadeIn(guideline), run_time=0.4)
        self.wait(3)
        
        # Clear for next examples
        self.play(FadeOut(VGroup(guidelines_title, main_content)))
        
        # Clear the main "Incorporating Images" title as well
        self.play(FadeOut(image_title))

        # Example 4: Large image in top-right corner
        example4_title = Text("Large Corner Image - Top Right", font_size=32, weight=BOLD)
        example4_title.to_edge(UP, buff=0.5).to_edge(LEFT, buff=1.0)
        
        # Large image in top-right corner
        large_img_tr = Rectangle(width=3.5, height=2.5, color=RED, fill_opacity=0.3)
        large_label_tr = Text("Large\nDiagram", font_size=20).move_to(large_img_tr.get_center())
        large_image_tr = VGroup(large_img_tr, large_label_tr)
        large_image_tr.to_corner(UR, buff=0.7)
        
        # Text content positioned clearly below title and away from image
        corner_text_tr = VGroup(
            Text("When using large corner images,", font_size=28),
            Text("adjust your text layout accordingly.", font_size=28),
            Text("Leave sufficient white space", font_size=28),
            Text("to prevent visual crowding.", font_size=28)
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.4)
        
        # Position text safely below title and to the left
        corner_text_tr.next_to(example4_title, DOWN, buff=0.8)
        corner_text_tr.to_edge(LEFT, buff=1.0)
        
        self.play(Write(example4_title))
        self.play(FadeIn(large_image_tr), run_time=1.0)
        for text_line in corner_text_tr:
            self.play(FadeIn(text_line), run_time=0.4)
        self.wait(2.5)
        
        # Clear for next example
        self.play(FadeOut(VGroup(example4_title, large_image_tr, corner_text_tr)))

        # Example 5: Large image in bottom-left corner
        example5_title = Text("Large Corner Image - Bottom Left", font_size=32, weight=BOLD)
        example5_title.to_edge(UP, buff=0.5).to_edge(LEFT, buff=1.0)
        
        # Large image in bottom-left corner
        large_img_bl = Rectangle(width=3.0, height=2.0, color=PURPLE, fill_opacity=0.3)
        large_label_bl = Text("Process\nFlow", font_size=18).move_to(large_img_bl.get_center())
        large_image_bl = VGroup(large_img_bl, large_label_bl)
        large_image_bl.to_corner(DL, buff=0.8)
        
        # Text content positioned in the available space
        corner_text_bl = VGroup(
            Text("Bottom corner placement works well", font_size=28),
            Text("for process flows or diagrams.", font_size=28),
            Text("Text flows naturally above the image,", font_size=28),
            Text("creating logical reading patterns.", font_size=28)
        ).arrange(DOWN, aligned_edge=LEFT, buff=0.4)
        
        # Position text below title, ensuring it doesn't overlap with bottom image
        corner_text_bl.next_to(example5_title, DOWN, buff=0.8)
        corner_text_bl.to_edge(LEFT, buff=1.0)
        
        self.play(Write(example5_title))
        self.play(FadeIn(large_image_bl), run_time=1.0)
        for text_line in corner_text_bl:
            self.play(FadeIn(text_line), run_time=0.4)
        self.wait(2.5)
        
        # Clear for next example
        self.play(FadeOut(VGroup(example5_title, large_image_bl, corner_text_bl)))

        # Example 6: Large centered image with text underneath
        example6_title = Text("Centered Image with Text Below", font_size=32, weight=BOLD)
        example6_title.to_edge(UP, buff=0.5)
        
        # Large centered image
        large_img_center = Rectangle(width=4.5, height=3.0, color=ORANGE, fill_opacity=0.3)
        large_label_center = Text("Main\nVisualization", font_size=22).move_to(large_img_center.get_center())
        large_image_center = VGroup(large_img_center, large_label_center)
        large_image_center.move_to(ORIGIN).shift(UP * 0.5)
        
        # Text content positioned underneath the image
        center_text = VGroup(
            Text("Centered layouts work excellently for presentations", font_size=28),
            Text("where the image is the primary focus.", font_size=28),
            Text("Supporting text flows naturally below,", font_size=28),
            Text("creating a clear visual hierarchy.", font_size=28)
        ).arrange(DOWN, buff=0.4)
        
        # Position text below the centered image
        center_text.next_to(large_image_center, DOWN, buff=0.8)
        
        # Ensure the whole layout fits within frame bounds
        full_layout = VGroup(example6_title, large_image_center, center_text)
        if full_layout.height > 7.5:
            full_layout.scale_to_fit_height(7.0)
            full_layout.move_to(ORIGIN)
        
        self.play(Write(example6_title))
        self.play(FadeIn(large_image_center), run_time=1.0)
        for text_line in center_text:
            self.play(FadeIn(text_line), run_time=0.4)
        self.wait(2.5)
        
        # Final clear
        self.play(FadeOut(VGroup(example6_title, large_image_center, center_text)))



Example 9: BAD Text Formatting Examples
The example explicitly demonstrates common pitfalls and bad practices when formatting text in Manim, leading to readability issues and content overflowing screen boundaries.

from manim import *
import numpy as np

class BadTextFormattingExamples(Scene):
    def construct(self):
        # ❌ BAD EXAMPLE 1: Using Text() with long strings without width constraints
        long_text = "This is a very long piece of text that will definitely overflow the screen boundaries because we're not using Paragraph or setting any width constraints whatsoever and it will just keep going off the edge of the screen making it completely unreadable and unprofessional looking."
        
        bad_text1 = Text(long_text, font_size=36)  # ❌ NO width constraint!
        # This will extend far beyond screen boundaries
        
        self.add(bad_text1)
        self.wait(1)
        self.remove(bad_text1)
        
        # ❌ BAD EXAMPLE 2: Font size too large for content amount
        medium_text = "Here is some text that could fit if we used appropriate sizing but we're using way too large font size"
        
        bad_text2 = Text(medium_text, font_size=72)  # ❌ Font too big!
        # Even medium-length text becomes problematic with huge fonts
        
        self.add(bad_text2)
        self.wait(1)
        self.remove(bad_text2)
        
        # ❌ BAD EXAMPLE 3: No line spacing consideration with multiple Text objects
        lines = [
            "Line one of text that is quite long",
            "Line two of text that is also quite long", 
            "Line three continues the pattern",
            "Line four keeps going",
            "Line five is still here",
            "Line six doesn't stop"
        ]
        
        bad_text_group = VGroup()
        for i, line in enumerate(lines):
            text_obj = Text(line, font_size=40)  # ❌ Too big font
            text_obj.shift(UP * (3 - i * 0.3))  # ❌ Lines too close together!
            bad_text_group.add(text_obj)
        
        # ❌ No checking if it fits on screen!
        self.add(bad_text_group)
        self.wait(1)
        self.remove(bad_text_group)
        
        # ❌ BAD EXAMPLE 4: Ignoring screen boundaries entirely
        title = Text("A Very Long Title That Might Go Off Screen", font_size=48)
        subtitle = Text("And a subtitle that definitely will", font_size=36)
        
        title.to_edge(UP, buff=0.1)  # ❌ Too close to edge!
        subtitle.to_edge(DOWN, buff=0.1)  # ❌ Too close to edge!
        # ❌ No width checking for either!
        
        self.add(title)
        self.add(subtitle)
        self.wait(2)


Example 10: Smart Multi-Slide Text Handling
This example demonstrates intelligent text handling for very long content, including automatic font sizing, content splitting across multiple slides, and smooth transitions between slides.

from manim import *
import numpy as np

class SmartMultiSlideText(Scene):
    def construct(self):
        # Very long text that needs intelligent handling
        very_long_text = "This is an example of very long text content that would be impossible to fit on a single screen with readable font sizes. When dealing with such extensive content, the best approach is to intelligently split it into multiple slides or sections, ensuring each part is clearly readable and properly formatted. This maintains viewer engagement while presenting all the necessary information in a digestible format. Each slide should flow naturally into the next, creating a cohesive narrative experience."
        
        # Method 1: Intelligent text splitting into multiple slides
        # Split the long text into logical chunks (sentences or phrases)
        text_chunks = [
            "This is an example of very long text content that would be impossible to fit on a single screen with readable font sizes.",
            "When dealing with such extensive content, the best approach is to intelligently split it into multiple slides or sections.",
            "This ensures each part is clearly readable and properly formatted, maintaining viewer engagement.",
            "Each slide should flow naturally into the next, creating a cohesive narrative experience."
        ]
        
        # Create title for the series
        main_title = Text("Smart Text Presentation", font_size=48, color=BLUE, weight=BOLD)
        main_title.to_edge(UP, buff=1)
        self.play(Write(main_title), run_time=1.5)
        self.wait(0.5)
        
        # Present each chunk as a separate slide with transitions
        for i, chunk in enumerate(text_chunks):
            # Create slide indicator
            slide_indicator = Text(f"({i+1}/{len(text_chunks)})", font_size=20, color=GRAY)
            slide_indicator.to_corner(UR, buff=0.3)
            
            # Create the text content with optimal font size
            content = Paragraph(
                chunk,
                font_size=28,  # Start with readable size
                line_spacing=1.3,
                alignment="center"
            ).set_width(11)  # Ensure it fits horizontally
            
            # Scale down if still too tall
            if content.height > 5.5:  # Leave room for title and indicator
                content.scale_to_fit_height(5.5)
                # But don't let font get too small
                if content.height < 3:  # If we had to scale down a lot
                    content.scale_to_fit_height(3)
            
            content.move_to(ORIGIN)
            
            # Animate slide appearance
            if i == 0:
                self.play(
                    Write(content),
                    FadeIn(slide_indicator),
                    run_time=2
                )
            else:
                # Smooth transition from previous slide
                self.play(
                    Transform(previous_content, content),
                    Transform(previous_indicator, slide_indicator),
                    run_time=1.5
                )
            
            self.wait(2.5)  # Give time to read
            
            # Store references for next transition
            previous_content = content
            previous_indicator = slide_indicator
        
        # Clear everything
        self.play(
            FadeOut(main_title),
            FadeOut(previous_content),
            FadeOut(previous_indicator),
            run_time=1
        )
        
        # Method 2: Progressive text revelation (for medium-long text)
        medium_text = "This approach works well for medium-length content where we want to build up information progressively rather than showing everything at once."
        
        title2 = Text("Progressive Revelation", font_size=40, color=ORANGE, weight=BOLD)
        title2.to_edge(UP, buff=1.5)
        self.play(Write(title2), run_time=1)
        
        # Split into progressive parts
        progressive_parts = [
            "This approach works well",
            "for medium-length content",
            "where we want to build up",
            "information progressively",
            "rather than showing everything at once."
        ]
        
        text_objects = []
        for i, part in enumerate(progressive_parts):
            text_obj = Text(part, font_size=32, color=WHITE)
            text_obj.shift(UP * (1.5 - i * 0.6))  # Stack vertically
            text_objects.append(text_obj)
        
        # Center the group
        text_group = VGroup(*text_objects)
        text_group.move_to(ORIGIN)
        
        # Reveal progressively
        for text_obj in text_objects:
            self.play(FadeIn(text_obj, shift=UP*0.3), run_time=0.8)
            self.wait(0.5)
        
        self.wait(2)
        self.play(FadeOut(VGroup(title2, text_group)), run_time=1)


Example 11: Comprehensive Text Animation Techniques in Manim
The provided Manim code showcases a variety of animation techniques specifically applied to text, including basic appearances, scaling and transformations, letter-by-letter reveals, and various movement effects like sliding, rotating, and bouncing.

from manim import *
import numpy as np

class BasicTextEffects(Scene):
    Basic text appearance effects
    def construct(self):
        # Effect 1: Classic Write animation
        title1 = Text("Classic Write Effect", font_size=40, color=BLUE)
        self.play(Write(title1), run_time=2)
        self.wait(1)
        self.play(FadeOut(title1))
        
        # Effect 2: FadeIn
        title2 = Text("Smooth Fade In", font_size=40, color=GREEN)
        self.play(FadeIn(title2), run_time=1.5)
        self.wait(1)
        self.play(FadeOut(title2))
        
        # Effect 3: DrawBorderThenFill
        title3 = Text("Draw Border Then Fill", font_size=40, color=RED, stroke_width=2)
        self.play(DrawBorderThenFill(title3), run_time=2.5)
        self.wait(1)
        self.play(FadeOut(title3))

class AdvancedTextEffects(Scene):
    Advanced text animations
    def construct(self):
        # Scale animation
        title = Text("Scale Animation", font_size=40, color=PURPLE)
        title.scale(0.1)
        self.play(title.animate.scale(10), run_time=1.5)
        self.wait(1)
        self.play(FadeOut(title))
        
        # Transform effect
        text_a = Text("Transform Me", font_size=36, color=ORANGE)
        text_b = Text("Into Something Else", font_size=36, color=PINK)
        self.play(Write(text_a))
        self.wait(0.5)
        self.play(Transform(text_a, text_b), run_time=2)
        self.wait(1)
        self.play(FadeOut(text_a))

class LetterByLetterEffects(Scene):
    Letter-by-letter and line-by-line animations
    def construct(self):
        # Letter by letter
        letters = VGroup(*[Text(char, font_size=48, color=YELLOW) for char in "AMAZING"])
        letters.arrange(RIGHT, buff=0.1)
        
        for letter in letters:
            self.play(FadeIn(letter, shift=UP*0.5), run_time=0.3)
        self.wait(1)
        
        for letter in letters:
            self.play(FadeOut(letter, shift=DOWN*0.5), run_time=0.2)

class MovementEffects(Scene):
    Sliding, rotating, and bouncing effects
    def construct(self):
        # Sliding effect
        title1 = Text("Slide From Left", font_size=36, color=MAROON)
        title1.shift(LEFT * 10)
        self.play(title1.animate.shift(RIGHT * 10), run_time=1.5)
        self.play(title1.animate.shift(RIGHT * 10), run_time=1)
        
        # Rotating entrance
        title2 = Text("Spinning Text", font_size=40, color=TEAL)
        title2.rotate(PI * 2)
        self.play(Rotate(title2, -PI * 2), FadeIn(title2), run_time=2)
        self.wait(1)
        self.play(FadeOut(title2))
        
        # Bouncy effect
        title3 = Text("Bouncy!", font_size=40, color=GOLD)
        title3.shift(UP * 5)
        self.play(
            title3.animate.shift(DOWN * 5),
            rate_func=rate_functions.ease_out_bounce,
            run_time=2
        )
        self.wait(1)
        self.play(FadeOut(title3))


CRITICAL USAGE CONSTRAINT: The Sandbox Principle
You must treat the 11 examples below as your only source of truth and your entire available library for Manim. Your knowledge is strictly limited to the classes, functions, and methods demonstrated in these specific examples.
This means:
DO NOT use any Manim class (Square, Circle, Text, etc.) that is not present in at least one of the examples.
DO NOT use any method (.shift(), .to_edge(), .set_color(), etc.) that is not present in at least one of the examples.
DO NOT import any external Python libraries other than numpy and os, as they are the only ones used in the examples.
Your task is to be creative within this sandbox. You should combine and compose these allowed building blocks in novel ways to fulfill the user's request. This does not mean you should copy an example verbatim.
These examples serve as a strict reference for valid Manim syntax and animation patterns; however, the creative content and specific visual design of your animation must be driven solely by the user's request.
By strictly adhering to this 'sandbox' of demonstrated features, you will AVOID generating code with hallucinated or incorrect features and produce reliable, high-quality animations.

COMMON ERROR PATTERNS TO AVOID:
- NEVER use rate functions without the rate_functions prefix! Use `rate_functions.smooth`, `rate_functions.ease_out_bounce`, etc. - NOT just `smooth` or `ease_out_bounce`
- NEVER use rate functions not available in Manim (like ease_out_sine, ease_in_out_quad) - stick to rate_functions.smooth, rate_functions.rush_from, rate_functions.ease_out_bounce, rate_functions.there_and_back, rate_functions.ease_in_out_sine
- NEVER assume objects have attributes like .x_range unless explicitly shown in examples
- NEVER use deprecated animation methods or parameters not demonstrated in examples
- NEVER pass parameters to methods unless those exact parameters are shown in the examples
- ALWAYS use proper 3D coordinate arrays [x, y, 0] for positions
- ALWAYS use demonstrated color names (RED, BLUE, GREEN, etc.) or valid hex colors
- ALWAYS use .set_stroke() and .set_color() methods on objects rather than passing style parameters directly to constructors when not shown in examples"""
        user_content = []
        if original_code and not last_error:
            user_content.append("You are modifying an existing animation. Here is the original Manim script:")
            user_content.append(f"--- ORIGINAL SCRIPT ---\n{original_code}\n--- END ORIGINAL SCRIPT ---")
            user_content.append(f"\nYour task is to modify this script based on the following instruction:\nInstruction: '{prompt}'")
        elif last_error:
            user_content.append("You are fixing a script that failed to execute. Here is the code that failed:")
            user_content.append(f"--- FAILED SCRIPT ---\n{last_generated_code}\n--- END FAILED SCRIPT ---")
            user_content.append(f"\nIt failed with the following error:\n--- ERROR MESSAGE ---\n{last_error}\n--- END ERROR MESSAGE ---")
            user_content.append(f"\nPlease fix the script to resolve the error while still fulfilling the original request:\nOriginal Request: '{prompt}'")
        else:
            user_content.append(f"Your task is to write a new Manim script based on the following instruction:\nInstruction: '{prompt}'")
            
            # Add available files information
            if available_files:
                available_files_info = f"\n📁 AVAILABLE FILES IN WORKING DIRECTORY:\n"
                for file in available_files:
                    available_files_info += f"- {file}\n"
                available_files_info += "These files can be loaded or referenced in your Manim script using relative paths (e.g., 'background.png', 'logo.svg')."
                user_content.append(available_files_info)
            
            # Add duration information
            if duration:
                duration_info = f"\n⏱️ TARGET DURATION: {duration} seconds\n"
                duration_info += f"- Plan your animation timing to match this target duration\n"
                duration_info += f"- Use appropriate run_time values for animations and wait() calls\n"
                duration_info += f"- Total animation should be approximately {duration}s when rendered"
                user_content.append(duration_info)
            
            # Add background color information
            if background_color:
                bg_info = f"\n🎨 BACKGROUND COLOR: {background_color}\n"
                bg_info += f"- Set the background using: self.camera.background_color = \"{background_color}\"\n"
                bg_info += f"- Place this line at the very start of your construct() method\n"
                bg_info += f"- Use the exact color value provided: \"{background_color}\""
                user_content.append(bg_info)
            else:
                user_content.append("\n🎨 BACKGROUND: Transparent (no background color specified)")
            
            # Add specific guidance for long text content
            text_char_count = len(prompt)
            text_word_count = len(prompt.split())
            
            if text_char_count > 300 or text_word_count > 50:  # Long text detected
                user_content.append("\n🎯 LONG TEXT DETECTED - SMART HANDLING REQUIRED:")
                user_content.append(f"- Text length: {text_char_count} characters, {text_word_count} words")
                user_content.append("- RECOMMENDED: Use multi-slide approach (Example 19)")
                user_content.append("- Split content into 3-4 logical chunks/sentences")
                user_content.append("- Use smooth transitions between slides")
                user_content.append("- Each slide should be readable with font_size >= 28")
            elif text_char_count > 150 or text_word_count > 25:  # Medium text
                user_content.append("\n🎯 MEDIUM TEXT - SINGLE SLIDE LINE SPLITTING:")
                user_content.append(f"- Text length: {text_char_count} characters - PERFECT for line splitting on ONE slide")
                user_content.append("- SPLIT into separate Text() objects for each sentence/phrase")
                user_content.append("- Create a list of strings, then [Text(line, font_size=30) for line in lines]")
                user_content.append("- Arrange using VGroup(*text_objects).arrange(DOWN, buff=0.4)")
                user_content.append("- Use font_size=28-32 for readability - DO NOT scale down!")
                user_content.append("- NEVER use set_width() - it makes fonts tiny")
                user_content.append("- This is a SINGLE slide with multiple lines, not multiple slides!")
            elif text_char_count > 50:  # Short-medium text  
                user_content.append("\n🎯 TEXT FORMATTING GUIDANCE:")
                user_content.append("- Split longer sentences into multiple Text() objects (Example 18)")
                user_content.append("- Use appropriate font_size (32-40 for readability)")
        
        user_content.append("\nRemember, your response must be only the complete, corrected Python code for the `GeneratedScene` class.")
        final_prompt = f"{system_prompt}\n\n{''.join(user_content)}"
        run_logger.debug(f"--- MANIM PLUGIN LLM PROMPT (Content Only) ---\n{''.join(user_content)}\n--- END ---")
        
        if USE_VERTEX_AI:
            thinking_budget = int(os.getenv("MANIM_THINKING_BUDGET", "0"))
            response = self.vertex_client.models.generate_content(
                model=MANIM_CODE_MODEL,
                contents=final_prompt,
                config=types.GenerateContentConfig(
                    thinking_config=types.ThinkingConfig(
                        thinking_budget=thinking_budget
                    )
                )
            )
            cleaned_code = response.text.strip()
        else:
            response = self.model.generate_content(final_prompt)
            cleaned_code = response.text.strip()
            
        if cleaned_code.startswith("```python"): cleaned_code = cleaned_code[9:]
        if cleaned_code.startswith("```"): cleaned_code = cleaned_code[3:]
        if cleaned_code.endswith("```"): cleaned_code = cleaned_code[:-3]
        return cleaned_code.strip()

    def _run_manim_script(self, script_filename: str, asset_unit_path: str, background_color: Optional[str], run_logger: logging.Logger):
        command = ["manim", "-q", "l", "--format", "mov"]
        
        # Only add transparent flag if no background color is specified
        if not background_color:
            command.append("-t")  # Transparent background
            
        command.extend([script_filename, "GeneratedScene"])
        
        run_logger.debug(f"MANIM PLUGIN: Executing command: {' '.join(command)} in CWD: {asset_unit_path}")
        # CWD is now the specific asset unit path
        subprocess.run(
            command, cwd=asset_unit_path, capture_output=True, text=True, check=True, timeout=300
        )

    def _find_latest_video(self, asset_unit_path: str) -> Optional[str]:
        # Manim generates video in a /media subdir relative to the CWD
        search_dir = os.path.join(asset_unit_path, "media", "videos")
        if not os.path.isdir(search_dir): return None
        
        found_video_path, newest_time = None, 0
        for root, _, files in os.walk(search_dir):
            for file in files:
                if file.lower().endswith('.mov'):
                    file_path = os.path.join(root, file)
                    file_mod_time = os.path.getmtime(file_path)
                    if file_mod_time > newest_time:
                        newest_time, found_video_path = file_mod_time, file_path
        return found_video_path
            
    def _cleanup(self, asset_unit_path: str):
        # Cleans up the media directory created by Manim inside the asset unit path
        media_dir = os.path.join(asset_unit_path, "media")
        if os.path.exists(media_dir):
            shutil.rmtree(media_dir)
        
        # The render script is also cleaned up
        for file in os.listdir(asset_unit_path):
            if file.startswith("render_script_attempt"):
                os.remove(os.path.join(asset_unit_path, file))