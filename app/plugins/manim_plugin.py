# app/plugins/manim_plugin.py

import logging
import os
import shutil
import subprocess
import time
import json
from typing import Dict, Optional, List

import google.generativeai as genai

from .base import ToolPlugin

# --- Configuration ---
MANIM_CODE_MODEL = "gemini-2.5-flash"
MAX_CODE_GEN_RETRIES = 3

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
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel(MANIM_CODE_MODEL)

    @property
    def name(self) -> str:
        return "Manim Animation Generator"

    @property
    def description(self) -> str:
        return (
            "Generates animated videos from a text description (e.g., titles, explainers). "
            "The output is always a .mov file with a transparent background, suitable for overlays. "
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
        unit_id = task_details.get("unit_id")
        
        run_logger.info(f"MANIM PLUGIN: Starting task for unit '{unit_id}' - '{prompt[:100]}...'.")
        
        if session_files:
            run_logger.info(f"MANIM PLUGIN: Session files available: {session_files}")
        if reference_assets:
            run_logger.info(f"MANIM PLUGIN: Reference assets available: {reference_assets}")
        if duration:
            run_logger.info(f"MANIM PLUGIN: Target duration: {duration} seconds")

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
                self._run_manim_script(script_filename, asset_unit_path, run_logger)

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
            if os.path.exists(file_path):
                filename = os.path.basename(file_path)
                dest_path = os.path.join(asset_unit_path, filename)
                try:
                    shutil.copy2(file_path, dest_path)
                    available_files.append(filename)
                    run_logger.info(f"MANIM PLUGIN: Copied session file '{file_path}' to working directory as '{filename}'")
                except Exception as e:
                    run_logger.warning(f"MANIM PLUGIN: Failed to copy session file '{file_path}': {e}")
            else:
                run_logger.warning(f"MANIM PLUGIN: Session file not found: '{file_path}'")
        
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
                           run_logger: logging.Logger) -> str:
        # --- PROMPT OMITTED AS PER INSTRUCTION ---
        system_prompt = system_prompt = """
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
6.  **BACKGROUND:** If the user asks for a specific background color, add `self.camera.background_color = <COLOR>` at the start of the `construct` method. Otherwise, DO NOT set a background color, as it will be rendered transparently.
7.  Do NOT include any code to render the scene (e.g., `if __name__ == "__main__"`)
8.  If you need to use an external asset like an image, its filename will be provided. Assume it exists in the same directory where the script is run. Use `manim.ImageMobject("filename.png")`.
9.  Your entire response MUST be just the Python code, with no explanations, markdown, or other text.

To guide your code generation, you must study the following examples of high-quality, correct Manim code. Adhere to the patterns, styles, and classes shown in these examples to ensure your output is valid. **These examples serve as a strict reference for valid Manim syntax and animation patterns; however, the creative content and specific visual design of your animation must be driven solely by the user's request.**

Example 1: ManySimpleAnimations
A comprehensive showcase of Manim's most common animation classes. This scene serves as a visual dictionary, demonstrating everything from simple Create and FadeIn to more complex animations like TransformMatchingShapes, ApplyWave, and Wiggle. It is an excellent reference for understanding the range of built-in animations.

from manim import * 
import numpy as np

class MediumTextLineSplitting(Scene):
    def construct(self):
        # CRITICAL EXAMPLE: How to handle medium-length text (150-300 chars) with line splitting
        # Example: Medium text that should be split into lines, NOT multiple slides
        medium_text = "The line spacing parameter was the main culprit for the massive gaps between lines within each paragraph. A line_spacing of 1.3 means 130% of normal height, which creates those huge gaps."
        
        # ✅ CORRECT APPROACH: Split into separate Text objects on ONE slide
        title = Text("Medium Text Example", font_size=36, color=BLUE, weight=BOLD)
        title.to_edge(UP, buff=0.8)
        
        # Split the text into logical lines
        text_lines = [
            "The line spacing parameter was the main culprit",
            "for the massive gaps between lines within each paragraph.",
            "A line_spacing of 1.3 means 130% of normal height,",
            "which creates those huge gaps."
        ]
        
        # Create separate Text objects with LARGE, readable fonts
        text_objects = [Text(line, font_size=30, color=WHITE) for line in text_lines]
        
        # Arrange them in a VGroup - this is ONE slide, multiple lines
        text_group = VGroup(*text_objects).arrange(DOWN, buff=0.4)
        text_group.next_to(title, DOWN, buff=0.8)
        
        # Show everything on ONE slide
        self.play(Write(title))
        for line in text_objects:
            self.play(FadeIn(line), run_time=0.5)
        self.wait(3)
        self.play(FadeOut(VGroup(title, text_group)))

class ManySimpleAnimations(Scene):
    def construct(self):
        text = Text("Animations").shift(UP*2.5)
        self.play(Write(text))
        self.wait(1)

        self.play(Transform(text,Text("Create").shift(UP*2.5)), run_time=0.5)
        start = Star()
        self.play(Create(start))
        self.play(Transform(text,Text("Uncreate").shift(UP*2.5)), run_time=0.5)
        self.play(Uncreate(start))
        
        self.play(Transform(text,Text("AnimatedBoundary").shift(UP*2.5)), run_time=0.5)
        circle = Circle()
        animated_boundary = AnimatedBoundary(circle, cycle_rate=3, colors=[RED, GREEN, BLUE])
        self.add(circle, animated_boundary)
        self.wait(2)
        self.remove(circle, animated_boundary)

        self.play(Transform(text,Text("TracedPath").shift(UP*2.5)), run_time=0.5)
        dot = Dot(color=RED)
        trace = TracedPath(dot.get_center)
        self.add(dot, trace)
        self.wait(0.5)
        self.play(dot.animate.shift(UP), run_time=0.5)
        self.play(dot.animate.shift(LEFT), run_time=0.5)
        self.play(dot.animate.shift(DOWN+RIGHT), run_time=0.5)
        self.remove(dot, trace)
        
        self.play(Transform(text,Text("AddTextLetterByLetter").shift(UP*2.5)), run_time=0.5)
        some_text = Text("Here is a text")
        self.play(AddTextLetterByLetter(some_text))
        self.play(Transform(text,Text("RemoveTextLetterByLetter").shift(UP*2.5)), run_time=0.5)
        self.play(RemoveTextLetterByLetter(some_text))

        self.play(Transform(text,Text("Write").shift(UP*2.5)), run_time=0.5)
        some_text = Text("Here is more text")
        self.play(Write(some_text))
        self.play(Transform(text,Text("Unwrite").shift(UP*2.5)), run_time=0.5)
        self.play(Unwrite(some_text))
        # self.remove(some_text) # Unwrite already removes it

        self.play(Transform(text,Text("DrawBorderThenFill").shift(UP*2.5)), run_time=0.5)
        square = Square(color=BLUE, fill_opacity=1).set_fill(YELLOW)
        self.play(DrawBorderThenFill(square))
        self.remove(square)

        self.play(Transform(text,Text("ShowIncreasingSubsets").shift(UP*2.5)), run_time=0.5)
        circles = VGroup(
            Circle().shift(UP*0.5),
            Circle().shift((DOWN+LEFT)*0.5),
            Circle().shift((DOWN+RIGHT)*0.5)
        )
        self.play(ShowIncreasingSubsets(circles))
        self.wait()
        self.remove(circles)

        self.play(Transform(text,Text("ShowSubmobjectsOneByOne").shift(UP*2.5)), run_time=0.5)
        circles2 = VGroup(
            Circle().shift(UP*0.5),
            Circle().shift((DOWN+LEFT)*0.5),
            Circle().shift((DOWN+RIGHT)*0.5)
        )
        self.play(ShowSubmobjectsOneByOne(circles2))
        self.play(Uncreate(circles2))

        self.play(Transform(text,Text("FadeIn").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.play(FadeIn(square))
        self.play(Transform(text,Text("FadeOut").shift(UP*2.5)), run_time=0.5)
        self.play(FadeOut(square))
        # self.remove(square) # FadeOut already removes it

        self.play(Transform(text,Text("GrowArrow").shift(UP*2.5)), run_time=0.5)
        arrow = Arrow(ORIGIN, RIGHT)
        self.play(GrowArrow(arrow))
        self.remove(arrow)

        self.play(Transform(text,Text("GrowFromCenter").shift(UP*2.5)), run_time=0.5)
        triangle = Triangle()
        self.play(GrowFromCenter(triangle))
        self.remove(triangle)

        self.play(Transform(text,Text("GrowFromEdge - DOWN").shift(UP*2.5)), run_time=0.5)
        squares = [Square() for _ in range(4)]
        self.play(GrowFromEdge(squares[0], DOWN))
        self.remove(squares[0])
        self.play(Transform(text,Text("GrowFromEdge - RIGHT").shift(UP*2.5)), run_time=0.5)
        self.play(GrowFromEdge(squares[1], RIGHT))
        self.remove(squares[1])
        self.play(Transform(text,Text("GrowFromEdge - UP").shift(UP*2.5)), run_time=0.5)
        self.play(GrowFromEdge(squares[2], UP))
        self.remove(squares[2])
        self.play(Transform(text,Text("GrowFromEdge - LEFT").shift(UP*2.5)), run_time=0.5)
        self.play(GrowFromEdge(squares[3], LEFT))
        self.remove(squares[3])

        self.play(Transform(text,Text("GrowFromPoint").shift(UP*2.5)), run_time=0.5)
        dot = Dot().shift(UP+RIGHT*2)
        star = Star()
        self.add(dot)
        self.wait(0.5)
        self.play(GrowFromPoint(star, dot))
        self.remove(dot, star)

        self.play(Transform(text,Text("SpinInFromNothing").shift(UP*2.5)), run_time=0.5)
        triangle = Triangle()
        self.play(SpinInFromNothing(triangle))
        self.remove(triangle)

        self.play(Transform(text,Text("ApplyWave").shift(UP*2.5)), run_time=0.5)
        some_text = Text("Mathematical Animations")
        self.play(ApplyWave(some_text))
        self.play(ApplyWave(some_text, direction=RIGHT))
        self.remove(some_text)

        self.play(Transform(text,Text("Circumscribe").shift(UP*2.5)), run_time=0.5)
        some_text = Text("Look Here")
        self.add(some_text)
        self.play(Circumscribe(some_text))
        self.play(Circumscribe(some_text, Circle, fade_out=True))
        self.remove(some_text)

        self.play(Transform(text,Text("Flash").shift(UP*2.5)), run_time=0.5)
        some_text = Text("Ta Da").set_color(YELLOW)
        self.add(some_text)
        self.play(Flash(some_text))
        self.remove(some_text)

        self.play(Transform(text,Text("FocusOn").shift(UP*2.5)), run_time=0.5)
        some_text = Text("Here!")
        self.add(some_text)
        self.play(FocusOn(some_text))
        self.remove(some_text)

        self.play(Transform(text,Text("Indicate").shift(UP*2.5)), run_time=0.5)
        some_text = Text("This is important")
        self.add(some_text)
        self.play(Indicate(some_text))
        self.remove(some_text)

        self.play(Transform(text,Text("Wiggle").shift(UP*2.5)), run_time=0.5)
        some_text = Text("THIS")
        self.add(some_text)
        self.play(Wiggle(some_text))
        self.remove(some_text)

        self.play(Transform(text,Text("ShowPassingFlash").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.play(ShowPassingFlash(square.copy()))
        self.remove(square)

        self.play(Transform(text,Text("ShowPassingFlashWithThinningStrokeWidth").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.play(ShowPassingFlashWithThinningStrokeWidth(square.copy()))
        self.remove(square)

        self.play(Transform(text,Text("MoveAlongPath").shift(UP*2.5)), run_time=0.5)
        l1 = Line(LEFT+DOWN, RIGHT+UP)
        d1 = Dot().move_to(l1.get_start())
        self.add(l1, d1)
        self.play(MoveAlongPath(d1, l1), rate_func=linear)
        self.remove(l1,d1)

        self.play(Transform(text,Text("Rotate").shift(UP*2.5)), run_time=0.5)
        star = Star()
        self.add(star)
        self.play(Rotate(star, angle=PI))
        self.remove(star)

        self.play(Transform(text,Text("Rotating").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(Rotating(square))
        self.wait(1)
        self.play(Uncreate(square)) # Rotating is a continuous animation

        self.play(Transform(text,Text("Broadcast").shift(UP*2.5)), run_time=0.5)
        triangle = Triangle()
        self.play(Broadcast(triangle))
        self.remove(triangle)

        self.play(Transform(text,Text("ChangeSpeed").shift(UP*2.5)), run_time=0.5)
        d = Dot().shift(LEFT*4)
        self.add(d)
        self.play(ChangeSpeed(d.animate.shift(RIGHT*8), speedinfo={0.3: 1, 0.4: 0.1, 0.6: 0.1, 1: 1}, rate_func=linear))
        self.remove(d)

        self.play(Transform(text,Text("Transform").shift(UP*2.5)), run_time=0.5)
        square = Square()
        star = Star()
        self.play(Transform(square,star))
        self.remove(square,star)
        
        self.play(Transform(text,Text("ClockwiseTransform").shift(UP*2.5)), run_time=0.5)
        square = Square()
        star = Star()
        self.play(ClockwiseTransform(square,star))
        self.remove(square,star)

        self.play(Transform(text,Text("CounterclockwiseTransform").shift(UP*2.5)), run_time=0.5)
        square = Square()
        star = Star()
        self.play(CounterclockwiseTransform(square,star))
        self.remove(square,star)

        self.play(Transform(text,Text("CyclicReplace").shift(UP*2.5)), run_time=0.5)
        square = Square()
        star = Star()
        circle = Circle()
        triangle = Triangle()
        vg = VGroup(square,star,circle,triangle)
        vg.arrange(RIGHT)
        self.play(CyclicReplace(*vg))
        self.wait()
        self.remove(*vg)

        self.play(Transform(text,Text("FadeToColor").shift(UP*2.5)), run_time=0.5)
        square = Square(fill_opacity=1).set_fill(RED)
        self.add(square)
        self.play(FadeToColor(square,color=YELLOW))
        self.remove(square)

        self.play(Transform(text,Text("FadeTransform").shift(UP*2.5)), run_time=0.5)
        square = Square(fill_opacity=1).set_fill(BLUE)
        star = Star(fill_opacity=1).set_fill(YELLOW)
        self.play(FadeTransform(square,star))
        self.remove(square,star)

        self.play(Transform(text,Text("MoveToTarget").shift(UP*2.5)), run_time=0.5)
        circle = Circle().shift(LEFT)
        circle.generate_target()
        circle.target.move_to(RIGHT)
        self.add(circle)
        self.play(MoveToTarget(circle))
        self.remove(circle)

        self.play(Transform(text,Text("ReplacementTransform").shift(UP*2.5)), run_time=0.5)
        circle = Circle().shift(LEFT)
        square = Square().shift(RIGHT)
        self.play(ReplacementTransform(circle,square))
        self.remove(square)

        self.play(Transform(text,Text("Restore").shift(UP*2.5)), run_time=0.5)
        circle = Circle()
        square = Square(fill_opacity=1).set_fill(RED).shift(DOWN+RIGHT)
        self.play(Create(circle), run_time=0.5)
        circle.save_state()
        self.wait(0.5)
        self.play(Transform(circle,square), run_time=0.3)
        self.play(circle.animate.shift(RIGHT), run_time=0.3)
        self.play(circle.animate.rotate(0.5), run_time=0.4)
        self.wait(0.5)
        self.play(Restore(circle))
        self.wait(0.2)
        self.remove(circle,square)

        self.play(Transform(text,Text("ScaleInPlace").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(ScaleInPlace(square, scale_factor=2))
        self.remove(square)

        self.play(Transform(text,Text("ShrinkToCenter").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.play(ShrinkToCenter(square))

        self.play(Transform(text,Text("TransformMatchingShapes").shift(UP*2.5)), run_time=0.5)
        source_text = Text("tom marvolo riddle")
        dest_text = Text("i am lord voldemort")
        self.play(Write(source_text))
        self.wait(0.5)
        self.play(TransformMatchingShapes(source_text, dest_text, path_arc=PI/2))
        self.wait(0.5)
        self.remove(source_text,dest_text)

        self.play(Transform(text,Text("TransformMatchingTex").shift(UP*2.5)), run_time=0.5)
        eq1 = MathTex("{{a}}^2", "+", "{{b}}^2", "=", "{{c}}^2")
        eq2 = MathTex("{{a}}^2", "=", "{{c}}^2", "-", "{{b}}^2")
        self.add(eq1)
        self.wait(0.5)
        self.play(TransformMatchingTex(eq1, eq2, path_arc=PI/2))
        self.wait(0.5)
        self.remove(eq1,eq2)

        self.play(Transform(text,Text("animate.shift").shift(UP*2.5)), run_time=0.5)
        circle = Circle()
        self.add(circle)
        self.play(circle.animate.shift(UP), run_time=0.5)
        self.play(circle.animate.shift(DOWN), run_time=0.5)
        self.play(circle.animate.shift(LEFT), run_time=0.5)
        self.play(circle.animate.shift(RIGHT), run_time=0.5)
        self.remove(circle)

        self.play(Transform(text,Text("animate.set_fill").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.set_fill(RED, opacity=1))
        self.remove(square)

        self.play(Transform(text,Text("animate.rotate").shift(UP*2.5)), run_time=0.5)
        triangle = Triangle()
        self.add(triangle)
        self.play(triangle.animate.rotate(PI))
        self.remove(triangle)

        self.play(Transform(text,Text("animate.scale").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.scale(1.5))
        self.remove(square)

        self.play(Transform(text,Text("animate.rotate (about point)").shift(UP*2.5)), run_time=0.5)
        star = Star().shift(RIGHT*2)
        self.add(star)
        self.play(star.animate.rotate(PI, about_point=ORIGIN))
        self.remove(star)

        self.play(Transform(text,Text("animate.flip").shift(UP*2.5)), run_time=0.5)
        triangle = Triangle()
        self.add(triangle)
        self.play(triangle.animate.flip())
        self.remove(triangle)

        self.play(Transform(text,Text("animate.stretch").shift(UP*2.5)), run_time=0.5)
        circle = Circle()
        self.add(circle)
        self.play(circle.animate.stretch(2, dim=1)) # Stretch in y-direction
        self.remove(circle)

        self.play(Transform(text,Text("Wiggle").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(Wiggle(square))
        self.remove(square)

        self.play(Transform(text,Text("animate.set_angle").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.set_angle(PI/4))
        self.remove(square)

        self.play(Transform(text,Text("animate.center").shift(UP*2.5)), run_time=0.5)
        square = Square().shift(LEFT*2)
        self.add(square)
        self.play(square.animate.center())
        self.remove(square)

        self.play(Transform(text,Text("animate.align_to").shift(UP*2.5)), run_time=0.5)
        dot = Dot(color=YELLOW).shift(RIGHT*2)
        square = Square().shift(LEFT*2)
        self.add(dot, square)
        self.play(square.animate.align_to(dot, direction=UP))
        self.remove(square, dot)

        self.play(Transform(text,Text("animate.to_corner").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.to_corner(UL))
        self.remove(square)

        self.play(Transform(text,Text("animate.to_edge").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.to_edge(DOWN))
        self.remove(square)

        self.play(Transform(text,Text("animate.next_to").shift(UP*2.5)), run_time=0.5)
        dot = Dot().shift((RIGHT+UP)*2)
        square = Square()
        self.add(dot, square)
        self.play(square.animate.next_to(dot))
        self.remove(square,dot)

        self.play(Transform(text,Text("animate.scale_to_fit_width").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.scale_to_fit_width(5))
        self.remove(square)

        self.play(Transform(text,Text("animate.stretch_to_fit_width").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.stretch_to_fit_width(5))
        self.remove(square)

        self.play(Transform(text,Text("animate.scale_to_fit_height").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.scale_to_fit_height(3))
        self.remove(square)

        self.play(Transform(text,Text("animate.stretch_to_fit_height").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.stretch_to_fit_height(3))
        self.remove(square)

        self.play(Transform(text,Text("animate.set_x").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.set_x(-1))
        self.remove(square)

        self.play(Transform(text,Text("animate.set_y").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.set_y(-1))
        self.remove(square)

        self.play(Transform(text,Text("animate.space_out_submobjects").shift(UP*2.5)), run_time=0.5)
        s1 = Square()
        s2 = Star()
        vg = VGroup(s1, s2).arrange(RIGHT, buff=0.1)
        self.add(vg)
        self.play(vg.animate.space_out_submobjects(factor=3))
        self.remove(vg)

        self.play(Transform(text,Text("animate.move_to").shift(UP*2.5)), run_time=0.5)
        circle = Circle()
        self.add(circle)
        self.play(circle.animate.move_to(RIGHT+UP))
        self.remove(circle)

        self.play(Transform(text,Text("animate.replace").shift(UP*2.5)), run_time=0.5)
        circle = Circle().shift(LEFT)
        star = Star().shift(RIGHT)
        self.add(circle, star)
        self.play(circle.animate.replace(star))
        self.remove(circle,star)

        self.play(Transform(text,Text("animate.surround").shift(UP*2.5)), run_time=0.5)
        circle = Circle(color=YELLOW).shift(LEFT)
        star = Star().shift(RIGHT)
        self.add(star, circle)
        self.play(circle.animate.surround(star))
        self.remove(circle,star)

        # FINAL FIX: Use the BackgroundRectangle mobject
        self.play(Transform(text,Text("BackgroundRectangle").shift(UP*2.5)), run_time=0.5)
        square = Square()
        bg_rect = BackgroundRectangle(square, color=BLUE, fill_opacity=0.5)
        self.add(square)
        self.play(Create(bg_rect))
        self.wait(0.5)
        self.remove(bg_rect, square)

        self.play(Transform(text,Text("animate.set_color").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.set_color(BLUE))
        self.remove(square)

        self.play(Transform(text,Text("animate.set_color_by_gradient").shift(UP*2.5)), run_time=0.5)
        square = Square()
        self.add(square)
        self.play(square.animate.set_color_by_gradient(RED,BLUE,YELLOW))
        self.remove(square)

        self.play(Transform(text,Text("animate.fade_to").shift(UP*2.5)), run_time=0.5)
        square = Square(fill_opacity=1).set_fill(RED)
        self.add(square)
        self.play(square.animate.fade_to(GREEN, 0.5))
        self.remove(square)

        self.play(Transform(text,Text("animate.fade").shift(UP*2.5)), run_time=0.5)
        square = Square(fill_opacity=1).set_fill(RED)
        self.add(square)
        self.play(square.animate.fade(0.7)) # Fades to 30% opacity
        self.remove(square)

        self.play(Transform(text,Text("animate.match_color").shift(UP*2.5)), run_time=0.5)
        circle = Circle(fill_opacity=1).set_fill(RED).shift(LEFT*2)
        square = Square(fill_opacity=1).shift(RIGHT*2)
        self.add(circle, square)
        self.play(square.animate.match_color(circle))
        self.remove(square,circle)
        
        self.play(Transform(text,Text("animate.match_width").shift(UP*2.5)), run_time=0.5)
        circle = Circle().scale(2)
        square = Square()
        self.add(circle,square)
        self.play(square.animate.match_width(circle))
        self.remove(square,circle)

        self.play(Transform(text,Text("animate.match_height").shift(UP*2.5)), run_time=0.5)
        circle = Circle().scale(2)
        square = Square()
        self.add(circle,square)
        self.play(square.animate.match_height(circle))
        self.remove(square,circle)

        self.play(Transform(text,Text("animate.match_x").shift(UP*2.5)), run_time=0.5)
        dot = Dot().shift((LEFT+UP)*2)
        star = Star()
        self.add(dot,star)
        self.play(star.animate.match_x(dot))
        self.remove(star,dot)

        self.play(Transform(text,Text("animate.match_y").shift(UP*2.5)), run_time=0.5)
        dot = Dot().shift((LEFT+UP)*2)
        star = Star()
        self.add(dot,star)
        self.play(star.animate.match_y(dot))
        self.remove(star,dot)

        self.play(Transform(text,Text("animate.arrange").shift(UP*2.5)), run_time=0.5)
        t1 = Text("3").shift(LEFT)
        t2 = Text("1")
        t3 = Text("2").shift(RIGHT)
        vg = VGroup(t1,t2,t3)
        self.add(vg)
        self.wait(0.5)
        self.play(vg.animate.arrange(buff=1.0))
        self.remove(vg)

        self.play(Transform(text,Text("animate.arrange_in_grid").shift(UP*2.5)), run_time=0.5)
        boxes=VGroup(*[Square().scale(0.5) for s in range(0,6)])
        boxes.arrange(buff=1.0)
        self.add(boxes)
        self.wait(0.5)
        self.play(boxes.animate.arrange_in_grid(rows=2, buff=0.5))
        self.remove(boxes)

        self.play(Transform(text,Text("animate.become").shift(UP*2.5)), run_time=0.5)
        circ = Circle(fill_color=RED, fill_opacity=0.8).shift(RIGHT*1.5)
        square = Square(fill_color=BLUE, fill_opacity=0.2).shift(LEFT*1.5)
        self.add(circ,square)
        self.wait(0.5)
        self.play(circ.animate.become(square))
        self.remove(circ,square)

        self.play(Transform(text,Text("animate.match_points").shift(UP*2.5)), run_time=0.5)
        circ = Circle(fill_color=RED, fill_opacity=0.8).shift(RIGHT*1.5)
        square = Square(fill_color=BLUE, fill_opacity=0.2).shift(LEFT*1.5)
        self.add(circ,square)
        self.wait(0.5)
        self.play(circ.animate.match_points(square))
        self.wait(0.5)
        self.play(FadeOut(circ),FadeOut(square))

        self.wait(0.5)
        self.play(FadeOut(text))
        self.wait()


Example 2: MinimalisticIntro
A clean, elegant, and minimalistic intro animation. This example focuses on typography and spacing, using a monochrome color scheme (black on white) and simple geometric lines to create a professional and modern title card. It primarily uses FadeIn and GrowFromCenter for a subtle and sophisticated effect.
from manim import *
import os

class MinimalisticIntro(Scene):
    A minimalistic intro scene with text and simple geometric shapes.
    def construct(self):
        # Set background to white
        self.camera.background_color = WHITE
        
        # Create main title text
        title = Text("YOUR NAME", font_size=48, color=BLACK, font="Arial")
        subtitle = Text("Professional Content", font_size=24, color=BLACK, font="Arial")
        subtitle.next_to(title, DOWN, buff=0.3)
        
        # Create geometric elements
        line1 = Line(LEFT * 3, RIGHT * 3, color=BLACK, stroke_width=2)
        line2 = Line(LEFT * 2, RIGHT * 2, color=BLACK, stroke_width=1)
        line1.next_to(title, UP, buff=0.8)
        line2.next_to(subtitle, DOWN, buff=0.8)
        
        dots = VGroup(*[Dot(color=BLACK, radius=0.05) for _ in range(3)])
        dots.arrange(RIGHT, buff=0.2).next_to(line2, DOWN, buff=0.5)
        
        # Animation sequence
        self.wait(0.5)
        self.play(GrowFromCenter(line1), run_time=1.2)
        self.play(FadeIn(title, shift=UP*0.3), run_time=1.0)
        self.wait(0.3)
        self.play(FadeIn(subtitle), run_time=0.8)
        self.wait(0.3)
        self.play(GrowFromCenter(line2), run_time=1.0)
        self.play(LaggedStart(*[FadeIn(dot) for dot in dots], lag_ratio=0.5), run_time=1)
        self.wait(1.5)
        
        # Exit animation
        all_elements = VGroup(title, subtitle, line1, line2, dots)
        self.play(FadeOut(all_elements), run_time=1.2)
        self.wait(0.5)


Example 3: MinimalisticIntroWithLogo
A robust intro scene that demonstrates practical error handling. It attempts to load an image logo from a file path and, if the file is not found, gracefully falls back to a procedurally generated geometric logo. This example highlights best practices for incorporating external assets while ensuring the animation can always run

class MinimalisticIntroWithLogo(Scene):
    
    An intro that tries to load an image logo, but creates a
    geometric one as a fallback if the image is not found.
    
    def construct(self):
        self.camera.background_color = WHITE
        
        logo = None
        is_image_logo = False
        
        logo_path = "assets/your_logo.png" 

        try:
            if not os.path.exists(logo_path):
                raise FileNotFoundError(f"Logo file not found at: {logo_path}")

            logo = ImageMobject(logo_path).scale_to_fit_height(1.5)
            is_image_logo = True
            print("Image logo loaded successfully.")

        except Exception as e:
            print(f"Error loading logo image: {e}")
            print("Using geometric fallback logo instead.")
            outer_circle = Circle(radius=0.8, color=BLACK, stroke_width=3)
            inner_circle = Circle(radius=0.3, color=BLACK, fill_opacity=1)
            logo = VGroup(outer_circle, inner_circle)
            is_image_logo = False

        title = Text("BRAND NAME", font_size=42, color=BLACK, weight=BOLD)
        tagline = Text("Excellence in Motion", font_size=18, color=BLACK)
        
        logo.to_edge(UP, buff=1.5)
        title.next_to(logo, DOWN, buff=0.8)
        tagline.next_to(title, DOWN, buff=0.3)
        accent_line = Line(LEFT * 1.5, RIGHT * 1.5, color=BLACK, stroke_width=1)
        accent_line.next_to(tagline, DOWN, buff=0.5)
        
        self.wait(0.3)
        if is_image_logo:
            self.play(FadeIn(logo, scale=0.8), run_time=1.2)
        else:
            self.play(Create(logo[0]), run_time=1.0)
            self.play(FadeIn(logo[1]), run_time=0.6)
        
        self.wait(0.4)
        self.play(Write(title), run_time=1.2)
        self.wait(0.3)
        self.play(FadeIn(tagline), run_time=0.8)
        self.wait(0.3)
        self.play(GrowFromCenter(accent_line), run_time=0.8)
        self.wait(2.0)
        
        # Use Group instead of VGroup for ImageMobject compatibility
        if is_image_logo:
            all_elements = Group(logo, title, tagline, accent_line)
        else:
            all_elements = VGroup(logo, title, tagline, accent_line)
        self.play(FadeOut(all_elements), run_time=1.0)
        self.wait(0.3)


Example 4: MinimalisticIntroWithImageLogo
A modern intro template featuring a placeholder geometric logo. This scene is designed as a starting point for branding, where a custom logo can be easily designed using Manim's shape and text objects. It showcases clean typography and a simple, effective animation sequence.

class MinimalisticIntroWithImageLogo(Scene):
    
    A clean, modern intro with a geometric logo placeholder.
    
    def construct(self):
        self.camera.background_color = WHITE
        
        # Create a simple geometric logo
        outer_circle = Circle(radius=0.6, color=BLUE, stroke_width=3, fill_opacity=0.1)
        logo_text = Text("LOGO", font_size=20, color=BLUE, weight=BOLD)
        logo = VGroup(outer_circle, logo_text)
        
        title = Text("YOUR BRAND", font_size=42, color=BLACK, weight=BOLD)
        tagline = Text("Tagline Goes Here", font_size=18, color=BLACK)
        
        logo.to_edge(UP, buff=1.5)
        title.next_to(logo, DOWN, buff=0.8)
        tagline.next_to(title, DOWN, buff=0.3)
        
        # Animation sequence
        self.wait(0.5)
        self.play(FadeIn(logo, scale=0.8), run_time=1.2, rate_func=smooth)
        self.wait(0.4)
        self.play(Write(title), run_time=1.5)
        self.wait(0.2)
        self.play(FadeIn(tagline, shift=UP*0.2), run_time=1.0)
        self.wait(2.5)
        
        # Use VGroup since we're using geometric elements
        all_elements = VGroup(logo, title, tagline)
        self.play(FadeOut(all_elements), run_time=1.0)
        self.wait(0.5)


Example 5: MinimalisticIntroWithRealImageLogo
A professionally implemented intro scene featuring a logo loaded from an image file. This example demonstrates the correct way to handle external image assets, including robust try-except error handling and using the appropriate Group class for an ImageMobject to ensure compatibility with other animated elements.

class MinimalisticIntroWithRealImageLogo(Scene):
    
    A properly implemented image logo scene with correct error handling.
    
    def construct(self):
        self.camera.background_color = WHITE
        
        logo_path = "logo.png"
        logo = None
        is_image_logo = False
        
        try:
            if os.path.exists(logo_path):
                logo = ImageMobject(logo_path).scale_to_fit_height(1.5)
                is_image_logo = True
                print("Image logo loaded successfully.")
            else:
                raise FileNotFoundError("Logo file not found")
                
        except Exception as e:
            print(f"Using geometric fallback logo: {e}")
            # Create geometric fallback
            outer_circle = Circle(radius=0.6, color=BLUE, stroke_width=3, fill_opacity=0.1)
            inner_text = Text("LOGO", font_size=20, color=BLUE, weight=BOLD)
            logo = VGroup(outer_circle, inner_text)
            is_image_logo = False

        title = Text("YOUR BRAND", font_size=42, color=BLACK, weight=BOLD)
        tagline = Text("Professional Excellence", font_size=18, color=BLACK)
        
        logo.to_edge(UP, buff=1.5)
        title.next_to(logo, DOWN, buff=0.8)
        tagline.next_to(title, DOWN, buff=0.3)
        
        # Animation sequence
        self.wait(0.5)
        self.play(FadeIn(logo, scale=0.8), run_time=1.2, rate_func=smooth)
        self.wait(0.4)
        self.play(Write(title), run_time=1.5)
        self.wait(0.2)
        self.play(FadeIn(tagline, shift=UP*0.2), run_time=1.0)
        self.wait(2.5)
        
        # Use appropriate grouping based on logo type
        if is_image_logo:
            all_elements = Group(logo, title, tagline)  # Group for ImageMobject
        else:
            all_elements = VGroup(logo, title, tagline)  # VGroup for VMobjects
            
        self.play(FadeOut(all_elements), run_time=1.0)
        self.wait(0.5)

Example 6: WaveOverlay
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

Example 7: ParticleSystem
A physics-based particle simulation overlay. This example uses an updater function to apply simple physics rules—like gravity, friction, and boundary collisions—to a group of particles. Each particle has its own velocity and evolves independently, creating a chaotic yet mesmerizing visual.

class ParticleSystem(Scene):
    def construct(self):
        # Background gradient effect
        background = Rectangle(width=16, height=10, fill_color=BLACK, fill_opacity=1)
        self.add(background)
        
        # Create particle system
        particles = VGroup()
        
        for i in range(50):
            particle = Dot(
                radius=0.05,
                color=random_bright_color(),
                fill_opacity=0.8
            )
            # Random starting position
            particle.move_to([
                np.random.uniform(-6, 6),
                np.random.uniform(-3, 3),
                0
            ])
            particles.add(particle)
        
        # Particle updater function
        def update_particles(mob, dt):
            for particle in mob:
                # Get current position
                pos = particle.get_center()
                
                # Add some physics - gravity and random motion
                velocity = getattr(particle, 'velocity', np.array([
                    np.random.uniform(-2, 2),
                    np.random.uniform(-2, 2),
                    0
                ]))
                
                # Apply forces
                velocity[1] -= 2 * dt  # gravity
                velocity *= 0.99  # friction
                
                # Random force
                velocity += np.array([
                    np.random.uniform(-0.5, 0.5) * dt,
                    np.random.uniform(-0.5, 0.5) * dt,
                    0
                ])
                
                # Update position
                new_pos = pos + velocity * dt
                
                # Boundary conditions
                if new_pos[0] < -7 or new_pos[0] > 7:
                    velocity[0] *= -0.8
                if new_pos[1] < -4:
                    velocity[1] *= -0.8
                    new_pos[1] = -4
                if new_pos[1] > 4:
                    new_pos[1] = 4
                    velocity[1] *= -0.8
                
                particle.move_to(new_pos)
                particle.velocity = velocity
                
                # Color cycling
                particle.set_color(interpolate_color(
                    particle.get_color(),
                    random_bright_color(),
                    0.02
                ))
        
        particles.add_updater(update_particles)
        
        # Overlay text that pulses
        title = Text("Particle Physics Simulation", font_size=40, color=WHITE)
        title.to_edge(UP)
        
        # Add everything
        self.add(particles)
        self.play(Write(title))
        self.wait(10)


Example 8: TextOverlayEffect
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

Example 9: LowerThirds
A professional 'Lower Thirds' graphic designed for video overlays, featuring a transparent background. This complex, multi-stage animation demonstrates how to build sophisticated information graphics with sleek design, including layered elements, text reveals, and accent animations. It's a practical example for content creators.
    
from manim import *

class LowerThirds(Scene):
    def construct(self):
        # Set transparent background
        self.camera.background_color = "#00000000"
        
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

Example 10: LogoReveal
A simple and elegant logo reveal animation on a transparent background. Ideal for a clean brand sting or video intro/outro, this example uses Write and FadeIn/FadeOut to present text in a professional and polished manner.

from manim import *

# Configuration for transparent background
config.background_color = "#00000000"

class LogoReveal(Scene):
    Elegant logo/text reveal animation
    def construct(self):
        # Create elegant text
        title = Text("BRAND", font="Arial", font_size=72, weight=BOLD)
        subtitle = Text("Professional Solutions", font="Arial", font_size=24, weight=NORMAL)
        
        # Position elements
        title.move_to(ORIGIN)
        subtitle.next_to(title, DOWN, buff=0.3)
        
        # Set colors
        title.set_color(WHITE)
        subtitle.set_color("#888888")
        
        # Animation sequence
        self.play(
            Write(title, run_time=2),
            rate_func=smooth
        )
        self.wait(0.5)
        self.play(
            FadeIn(subtitle, shift=UP*0.3),
            run_time=1.5
        )
        self.wait(2)
        
        # Elegant exit
        self.play(
            FadeOut(title, shift=UP*0.5),
            FadeOut(subtitle, shift=UP*0.5),
            run_time=1.5
        )

Example 11: ParticleFlow
A subtle, looping particle flow animation on a transparent background. This scene is perfect for creating ambient background visuals for videos. It uses an updater to create an endless stream of particles flowing across the screen, adding gentle motion without distracting from the main content.

class ParticleFlow(Scene):
    Subtle particle flow animation
    def construct(self):
        # Create flowing particles
        particles = VGroup()
        
        for i in range(20):
            particle = Dot(radius=0.05, color=WHITE, fill_opacity=0.7)
            particle.move_to(
                LEFT*6 + UP*np.random.uniform(-3, 3) + RIGHT*np.random.uniform(0, 2)
            )
            particles.add(particle)
        
        # Animate particle flow
        def update_particles(mob, dt):
            for particle in mob:
                particle.shift(RIGHT*dt*2)
                if particle.get_center()[0] > 6:
                    particle.move_to(LEFT*6 + UP*np.random.uniform(-3, 3))
        
        particles.add_updater(update_particles)
        
        self.add(particles)
        self.wait(5)
        
        particles.clear_updaters()
        self.play(FadeOut(particles))

Example 12: TextAnimation
A dynamic text animation for displaying a sequence of words on a transparent background. This example loops through a list of strings, animating each one in and out. It's useful for highlighting keywords, presenting chapter titles, or creating a kinetic typography effect.
    
class TextAnimation(Scene):
    Professional text animation
    def construct(self):
        # Create text elements
        words = ["INNOVATION", "EXCELLENCE", "GROWTH"]
        
        for word in words:
            text = Text(word, font="Arial", font_size=48, weight=BOLD, color=WHITE)
            text.move_to(ORIGIN)
            
            # Letter-by-letter reveal
            self.play(
                Write(text, run_time=1.5),
                rate_func=smooth
            )
            self.wait(1)
            
            # Elegant fade out
            self.play(
                FadeOut(text, shift=UP*0.5),
                run_time=1
            )
            self.wait(0.5)

Example 13: ComplexColorfulIntro
A spectacular, multi-stage intro sequence demonstrating a wide array of advanced and colorful Manim effects. This example is structured into distinct methods for clarity and showcases complex particle systems, geometric transformations, mathematical visualizations with MathTex, and a dramatic logo reveal. It's a testament to Manim's capabilities for creating visually stunning, cinematic animations.
    
from manim import *
import numpy as np

class ComplexColorfulIntro(Scene):
    def construct(self):
        # Set background to deep space color
        self.camera.background_color = "#0a0a1a"
        
        # Create particle system background
        self.create_particle_background()
        
        # Main title sequence
        self.animated_title_sequence()
        
        # Geometric morphing patterns
        self.geometric_transformations()
        
        # Mathematical visualization
        self.mathematical_showcase()
        
        # Final logo reveal
        self.logo_reveal()
        
        # Closing effects
        self.closing_effects()

    def create_particle_background(self):
        Create animated particle background
        particles = VGroup()
        
        for _ in range(50):
            particle = Dot(
                point=[
                    np.random.uniform(-8, 8),
                    np.random.uniform(-4.5, 4.5),
                    0
                ],
                radius=np.random.uniform(0.02, 0.08),
                color=interpolate_color(RED, BLUE, np.random.random())
            )
            particles.add(particle)
        
        # Animate particles floating
        animations = []
        for particle in particles:
            end_point = [
                particle.get_center()[0] + np.random.uniform(-2, 2),
                particle.get_center()[1] + np.random.uniform(-1, 1),
                0
            ]
            
            animations.append(
                particle.animate.move_to(end_point).set_opacity(0.3)
            )
        
        self.play(
            *animations,
            rate_func=rate_functions.ease_in_out_sine,
            run_time=4
        )

    def animated_title_sequence(self):
        Create animated title with spectacular effects
        # Create main title
        title = Text("MANIM", font_size=120, weight=BOLD)
        subtitle = Text("Mathematical Animation Engine", font_size=36)
        
        # Position elements
        title.move_to(ORIGIN + UP * 0.5)
        subtitle.move_to(ORIGIN + DOWN * 1.2)
        
        # Create rainbow gradient for title
        title.set_color_by_gradient(RED, ORANGE, YELLOW, GREEN, BLUE, PURPLE)
        subtitle.set_color(GRAY_B)
        
        # Spectacular entrance animation
        self.play(
            Write(title, stroke_width=4, stroke_color=WHITE),
            run_time=3,
            rate_func=rate_functions.ease_out_bounce
        )
        
        # Add glowing effect
        glow = title.copy().set_stroke(WHITE, width=8, opacity=0.5)
        self.add(glow)
        
        self.play(
            FadeIn(subtitle, shift=UP * 0.5),
            glow.animate.set_stroke(opacity=0.8),
            run_time=1.5
        )
        
        # Pulsing effect
        self.play(
            title.animate.scale(1.1).set_stroke(width=6),
            glow.animate.scale(1.1).set_stroke(width=12),
            run_time=0.5,
            rate_func=there_and_back
        )
        
        self.wait(1)
        
        # Title exit with explosion effect
        explosion_circles = VGroup()
        for i in range(12):
            circle = Circle(radius=0.1, color=interpolate_color(RED, PURPLE, i/11))
            circle.move_to(title.get_center())
            explosion_circles.add(circle)
        
        self.add(explosion_circles)
        
        self.play(
            *[circle.animate.scale(20).set_opacity(0) for circle in explosion_circles],
            FadeOut(title, glow, subtitle),
            run_time=2
        )

    def geometric_transformations(self):
        Create complex geometric morphing patterns
        # Start with a simple square
        shape = Square(side_length=2, color=BLUE, fill_opacity=0.7)
        shape.set_stroke(WHITE, width=3)
        
        self.play(Create(shape), run_time=1)
        
        # Transform through various shapes
        shapes_sequence = [
            RegularPolygon(n=6, color=GREEN, fill_opacity=0.7),
            Circle(radius=1.5, color=RED, fill_opacity=0.7),
            Star(n=8, outer_radius=2, color=YELLOW, fill_opacity=0.7),
            RegularPolygon(n=3, color=PURPLE, fill_opacity=0.7),
        ]
        
        for new_shape in shapes_sequence:
            new_shape.set_stroke(WHITE, width=3)
            self.play(
                Transform(shape, new_shape),
                Rotate(shape, PI/3),
                run_time=1.5
            )
            self.wait(0.5)
        
        # Create kaleidoscope effect
        kaleidoscope = VGroup()
        for i in range(8):
            copy = shape.copy()
            copy.rotate(i * PI/4)
            copy.scale(0.3)
            copy.move_to(2 * RIGHT * np.cos(i * PI/4) + 2 * UP * np.sin(i * PI/4))
            kaleidoscope.add(copy)
        
        self.play(
            FadeOut(shape),
            *[Create(piece) for piece in kaleidoscope],
            run_time=2
        )
        
        # Spinning kaleidoscope
        self.play(
            Rotate(kaleidoscope, 2*PI, about_point=ORIGIN),
            kaleidoscope.animate.set_color_by_gradient(RED, ORANGE, YELLOW, GREEN, BLUE, PURPLE),
            run_time=3
        )
        
        self.play(FadeOut(kaleidoscope), run_time=1)

    def mathematical_showcase(self):
        Showcase mathematical concepts with beautiful visualizations
        # Fourier series visualization
        axes = Axes(
            x_range=[-4, 4, 1],
            y_range=[-3, 3, 1],
            x_length=8,
            y_length=6,
            axis_config={"color": GRAY}
        )
        
        self.play(Create(axes), run_time=1)
        
        # Create complex sine wave with harmonics
        def fourier_func(x, n_terms=5):
            result = 0
            for n in range(1, n_terms + 1):
                result += (1/n) * np.sin(n * x)
            return result
        
        # Animate building up the Fourier series
        curves = VGroup()
        colors = [RED, ORANGE, YELLOW, GREEN, BLUE]
        
        for i in range(1, 6):
            curve = axes.plot(
                lambda x, terms=i: fourier_func(x, terms),
                color=colors[i-1],
                stroke_width=3
            )
            curves.add(curve)
            
            self.play(Create(curve), run_time=1)
        
        # Add equation
        equation = MathTex(
            r"f(x) = \sum_{n=1}^{\infty} \frac{1}{n} \sin(nx)",
            font_size=48,
            color=WHITE
        ).move_to(UP * 3)
        
        self.play(Write(equation), run_time=2)
        
        # Animate the curves
        self.play(
            *[curve.animate.set_stroke(opacity=0.7) for curve in curves],
            equation.animate.set_color_by_gradient(RED, BLUE),
            run_time=2
        )
        
        self.wait(1)
        self.play(FadeOut(axes, curves, equation), run_time=1)

    def logo_reveal(self):
        Create a stunning logo reveal
        # Create geometric logo design
        outer_circle = Circle(radius=2.5, color=WHITE, stroke_width=4)
        inner_circles = VGroup()
        
        for i in range(6):
            angle = i * PI/3
            circle = Circle(
                radius=0.5,
                color=interpolate_color(RED, BLUE, i/5),
                fill_opacity=0.8
            )
            circle.move_to(1.5 * RIGHT * np.cos(angle) + 1.5 * UP * np.sin(angle))
            inner_circles.add(circle)
        
        # Center element
        center_star = Star(n=6, outer_radius=0.8, color=GOLD, fill_opacity=1)
        
        logo = VGroup(outer_circle, inner_circles, center_star)
        
        # Dramatic reveal
        self.play(
            DrawBorderThenFill(outer_circle),
            run_time=2
        )
        
        self.play(
            *[FadeIn(circle, scale=0.1) for circle in inner_circles],
            run_time=1.5,
            lag_ratio=0.2
        )
        
        self.play(
            Create(center_star),
            Flash(center_star.get_center(), line_length=1, num_lines=16),
            run_time=1.5
        )
        
        # Rotation animation
        self.play(
            Rotate(inner_circles, 2*PI),
            Rotate(center_star, -2*PI),
            run_time=3,
            rate_func=rate_functions.ease_in_out_sine
        )
        
        self.wait(1)
        self.play(FadeOut(logo), run_time=1)

    def closing_effects(self):
        Create spectacular closing effects
        # Create spiral of mathematical symbols
        symbols = ["\\sum", "\\int", "\\partial", "\\nabla", "\\infty", "\\pi", "\\phi", "\\theta", "\\lambda", "\\Omega"]
        symbol_mobjects = VGroup()
        
        for i, symbol in enumerate(symbols):
            mob = MathTex(symbol, font_size=72, color=interpolate_color(RED, PURPLE, i/9))
            angle = i * 2*PI/len(symbols)
            radius = 3
            mob.move_to(radius * RIGHT * np.cos(angle) + radius * UP * np.sin(angle))
            symbol_mobjects.add(mob)
        
        # Animate symbols spiraling inward
        self.play(
            *[Create(symbol) for symbol in symbol_mobjects],
            run_time=2,
            lag_ratio=0.1
        )
        
        # Spiral animation
        self.play(
            *[
                symbol.animate.move_to(ORIGIN).scale(0.1).set_opacity(0)
                for symbol in symbol_mobjects
            ],
            Rotate(symbol_mobjects, 4*PI),
            run_time=3
        )
        
        # Final flash
        final_flash = Circle(radius=0.1, color=WHITE, fill_opacity=1)
        self.add(final_flash)
        
        self.play(
            final_flash.animate.scale(50).set_opacity(0),
            Flash(ORIGIN, line_length=2, num_lines=20, color=WHITE),
            run_time=1.5
        )
        
        self.wait(1)

Example 14: Complex3DIntro
A demonstration of Manim's 3D capabilities. This scene uses ThreeDScene to create and animate a mathematical surface. It showcases 3D object creation, rotation around an axis, and the use of an ambient rotating camera to provide a dynamic, multi-angled view of the 3D space.

# Additional scene with 3D elements
class Complex3DIntro(ThreeDScene):
    def construct(self):
        # Set 3D camera
        self.set_camera_orientation(phi=75 * DEGREES, theta=45 * DEGREES)
        
        # Create 3D mathematical surface
        surface = Surface(
            lambda u, v: np.array([
                u,
                v,
                0.5 * np.sin(u) * np.cos(v)
            ]),
            u_range=[-3, 3],
            v_range=[-3, 3],
            resolution=(15, 15)
        )
        
        surface.set_fill_by_checkerboard(BLUE, GREEN, opacity=0.7)
        surface.set_stroke(WHITE, width=1, opacity=0.8)
        
        self.play(Create(surface), run_time=3)
        
        # Rotate the surface
        self.play(
            Rotate(surface, 2*PI, axis=UP),
            run_time=4,
            rate_func=rate_functions.ease_in_out_sine
        )
        
        # Add rotating camera movement
        self.begin_ambient_camera_rotation(rate=0.3)
        self.wait(3)
        self.stop_ambient_camera_rotation()
        
        self.play(FadeOut(surface), run_time=2)

Example 15: ComplexColorfulAnimation
A highly complex and colorful animation focused on creating a 'living' ecosystem of coordinated motion. This scene uses multiple updaters to simultaneously animate particles, morphing shapes, flowing ribbons, and pulsing orbs, each following its own set of rules. It exemplifies how to build intricate, continuously evolving procedural animations.

from manim import *
import numpy as np

class ComplexColorfulAnimation(Scene):
    def construct(self):
        # Set background to deep space black
        self.camera.background_color = "#0a0a0a"
        
        # Create particle system
        particles = self.create_particle_system()
        
        # Create morphing geometric shapes
        shapes = self.create_morphing_shapes()
        
        # Create flowing ribbons
        ribbons = self.create_flowing_ribbons()
        
        # Create pulsing orbs
        orbs = self.create_pulsing_orbs()
        
        # Create text that transforms
        text_group = self.create_transforming_text()
        
        # Add all elements to scene
        self.add(*particles, *shapes, *ribbons, *orbs)
        
        # Main animation sequence
        self.play_main_sequence(particles, shapes, ribbons, orbs, text_group)
    
    def create_particle_system(self):
        particles = []
        colors = [PINK, PURPLE, BLUE, TEAL, GREEN, YELLOW, ORANGE, RED]
        
        for i in range(80):
            particle = Dot(radius=0.05)
            # Random position in a circle
            angle = i * TAU / 80 + np.random.random() * 0.5
            radius = 2 + np.random.random() * 3
            particle.move_to([
                radius * np.cos(angle),
                radius * np.sin(angle),
                0
            ])
            particle.set_color(np.random.choice(colors))
            particles.append(particle)
        
        return particles
    
    def create_morphing_shapes(self):
        shapes = []
        
        # Central morphing shape
        shape1 = RegularPolygon(n=6, radius=1.5, color=PURPLE, fill_opacity=0.3)
        shape1.set_stroke(PINK, width=3)
        shapes.append(shape1)
        
        # Orbiting triangles
        for i in range(3):
            triangle = Triangle(color=BLUE, fill_opacity=0.4)
            triangle.set_stroke(TEAL, width=2)
            triangle.scale(0.3)
            angle = i * TAU / 3
            triangle.move_to([2.5 * np.cos(angle), 2.5 * np.sin(angle), 0])
            shapes.append(triangle)
        
        # Floating squares
        for i in range(4):
            square = Square(side_length=0.4, color=GREEN, fill_opacity=0.5)
            square.set_stroke(YELLOW, width=2)
            angle = i * TAU / 4 + PI/4
            square.move_to([4 * np.cos(angle), 4 * np.sin(angle), 0])
            shapes.append(square)
        
        return shapes
    
    def create_flowing_ribbons(self):
        ribbons = []
        
        for i in range(5):
            # Create curved path
            points = []
            for t in np.linspace(0, TAU, 50):
                x = 3 * np.cos(t + i * PI/3)
                y = 2 * np.sin(2*t + i * PI/3)
                points.append([x, y, 0])
            
            ribbon = VMobject()
            ribbon.set_points_smoothly(points)
            ribbon.set_stroke(
                color=[ORANGE, RED, PINK, PURPLE][i % 4],
                width=8,
                opacity=0.7
            )
            ribbons.append(ribbon)
        
        return ribbons
    
    def create_pulsing_orbs(self):
        orbs = []
        positions = [
            [-3, 2, 0], [3, 2, 0], [-3, -2, 0], [3, -2, 0],
            [0, 3, 0], [0, -3, 0]
        ]
        colors = [BLUE, GREEN, RED, YELLOW, PURPLE, TEAL]
        
        for i, (pos, color) in enumerate(zip(positions, colors)):
            orb = Circle(radius=0.3, color=color, fill_opacity=0.6)
            orb.set_stroke(WHITE, width=2)
            orb.move_to(pos)
            orbs.append(orb)
        
        return orbs
    
    def create_transforming_text(self):
        text1 = Text("DYNAMIC", font_size=48, color=PINK)
        text2 = Text("ENERGY", font_size=48, color=TEAL)
        text3 = Text("MOTION", font_size=48, color=YELLOW)
        
        text1.move_to(UP * 0.5)
        text2.move_to(ORIGIN)
        text3.move_to(DOWN * 0.5)
        
        return VGroup(text1, text2, text3)
    
    def play_main_sequence(self, particles, shapes, ribbons, orbs, text_group):
        # Phase 1: Entrance
        self.play(
            *[FadeIn(particle, shift=UP) for particle in particles[:20]],
            *[DrawBorderThenFill(shape) for shape in shapes[:4]],
            run_time=3
        )
        
        # Phase 2: Particle explosion
        self.play(
            *[FadeIn(particle, shift=np.random.random(3)) for particle in particles[20:]],
            *[Create(ribbon) for ribbon in ribbons],
            run_time=2
        )
        
        # Phase 3: Add orbs and text
        self.play(
            *[GrowFromCenter(orb) for orb in orbs],
            Write(text_group),
            run_time=2
        )
        
        # Phase 4: Complex coordinated motion
        particle_animations = []
        for i, particle in enumerate(particles):
            # Spiral motion
            def spiral_updater(mob, dt, i=i):
                t = self.renderer.time
                radius = 2 + 0.5 * np.sin(t * 2 + i * 0.1)
                angle = t * 0.5 + i * TAU / len(particles)
                new_pos = [
                    radius * np.cos(angle),
                    radius * np.sin(angle),
                    0.2 * np.sin(t * 3 + i * 0.2)
                ]
                mob.move_to(new_pos)
            
            particle.add_updater(spiral_updater)
        
        # Shape morphing and rotation
        shape_animations = []
        for i, shape in enumerate(shapes):
            if i == 0:  # Central shape
                self.play(
                    Transform(shape, RegularPolygon(n=8, radius=1.5, color=TEAL, fill_opacity=0.3)),
                    run_time=2
                )
            else:
                # Orbiting shapes
                def orbit_updater(mob, dt, i=i):
                    t = self.renderer.time
                    orbit_radius = 2.5 + 0.3 * np.sin(t * 1.5)
                    angle = t * 0.8 + (i-1) * TAU / 6
                    new_pos = [
                        orbit_radius * np.cos(angle),
                        orbit_radius * np.sin(angle),
                        0
                    ]
                    mob.move_to(new_pos)
                    mob.rotate(dt * 2)
                
                shape.add_updater(orbit_updater)
        
        # Pulsing orbs
        for i, orb in enumerate(orbs):
            def pulse_updater(mob, dt, i=i):
                t = self.renderer.time
                scale = 1 + 0.3 * np.sin(t * 3 + i * PI/3)
                opacity = 0.4 + 0.4 * np.sin(t * 4 + i * PI/2)
                mob.set_fill(opacity=opacity)
                # Reset scale and apply new one
                mob.scale(1/mob.scale_factor if hasattr(mob, 'scale_factor') else 1)
                mob.scale(scale)
                mob.scale_factor = scale
            
            orb.add_updater(pulse_updater)
        
        # Ribbon flow
        for i, ribbon in enumerate(ribbons):
            def flow_updater(mob, dt, i=i):
                t = self.renderer.time
                points = []
                for j, s in enumerate(np.linspace(0, TAU, 50)):
                    x = 3 * np.cos(s + i * PI/3 + t * 0.5)
                    y = 2 * np.sin(2*s + i * PI/3 + t * 0.3)
                    z = 0.1 * np.sin(s * 3 + t * 2)
                    points.append([x, y, z])
                mob.set_points_smoothly(points)
            
            ribbon.add_updater(flow_updater)
        
        # Text transformations
        text_cycle = [
            Text("VIBRANT", font_size=48, color=ORANGE),
            Text("FLUID", font_size=48, color=GREEN),
            Text("COSMIC", font_size=48, color=PURPLE),
        ]
        
        # Let the complex motion play for several seconds
        self.wait(4)
        
        # Phase 5: Text transformations
        for new_text in text_cycle:
            new_text.move_to(text_group.get_center())
            self.play(
                Transform(text_group, new_text),
                run_time=1.5
            )
            self.wait(1)
        
        # Phase 6: Color wave transformation
        color_wave_animations = []
        for i, particle in enumerate(particles):
            def color_wave_updater(mob, dt, i=i):
                t = self.renderer.time
                hue = (t * 0.5 + i * 0.05) % 1
                # Convert HSV to RGB-like color
                if hue < 1/6:
                    color = interpolate_color(RED, ORANGE, hue * 6)
                elif hue < 2/6:
                    color = interpolate_color(ORANGE, YELLOW, (hue - 1/6) * 6)
                elif hue < 3/6:
                    color = interpolate_color(YELLOW, GREEN, (hue - 2/6) * 6)
                elif hue < 4/6:
                    color = interpolate_color(GREEN, TEAL, (hue - 3/6) * 6)
                elif hue < 5/6:
                    color = interpolate_color(TEAL, BLUE, (hue - 4/6) * 6)
                else:
                    color = interpolate_color(BLUE, RED, (hue - 5/6) * 6)
                
                mob.set_color(color)
            
            particle.add_updater(color_wave_updater)
        
        # Let the color wave play
        self.wait(3)
        
        # Phase 7: Grand finale - everything converges
        self.play(
            *[particle.animate.move_to(ORIGIN) for particle in particles],
            *[shape.animate.move_to(ORIGIN) for shape in shapes],
            *[orb.animate.move_to(ORIGIN) for orb in orbs],
            text_group.animate.scale(2).set_color(WHITE),
            run_time=3
        )
        
        # Final explosion
        self.play(
            *[particle.animate.scale(0).set_opacity(0) for particle in particles],
            *[shape.animate.scale(0).set_opacity(0) for shape in shapes],
            *[orb.animate.scale(0).set_opacity(0) for orb in orbs],
            FadeOut(text_group),
            *[FadeOut(ribbon) for ribbon in ribbons],
            run_time=2
        )
        
        self.wait(1)

Example 16: Proper Text Formatting Example
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



Example 17: BAD Text Formatting Examples
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


Example 18: Smart Line Splitting for Readable Text
This example demonstrates how to properly split longer text into separate lines using VGroup instead of scaling fonts down with set_width().

from manim import *

class SmartLineSplitting(Scene):
    def construct(self):
        # Example text that would be hard to read if forced into one line or scaled down
        long_text = "The line spacing parameter was the main culprit for the massive gaps between lines within each paragraph. A line_spacing of 1.3 means 130% of normal height, which creates those huge gaps. Setting it to 1.1 gives much more reasonable spacing while still being readable."
        
        # ❌ BAD APPROACH: Using Paragraph with set_width (causes tiny fonts)
        bad_title = Text("❌ BAD: Paragraph with set_width()", font_size=24, color=RED)
        bad_title.to_edge(UP, buff=0.5)
        
        bad_text = Paragraph(long_text, font_size=32, line_spacing=1.1)
        bad_text.set_width(12)  # This scales the font down making it unreadable!
        bad_text.next_to(bad_title, DOWN, buff=0.5)
        
        self.play(Write(bad_title))
        self.play(FadeIn(bad_text))
        self.wait(2)
        self.play(FadeOut(VGroup(bad_title, bad_text)))
        
        # ✅ GOOD APPROACH: Split into separate Text objects
        good_title = Text("✅ GOOD: Split into separate Text objects", font_size=24, color=GREEN)
        good_title.to_edge(UP, buff=0.5)
        
        # Split the long text into logical phrases/sentences
        text_lines = [
            "The line spacing parameter was the main culprit",
            "for the massive gaps between lines within each paragraph.",
            "A line_spacing of 1.3 means 130% of normal height,",
            "which creates those huge gaps.",
            "Setting it to 1.1 gives much more reasonable spacing",
            "while still being readable."
        ]
        
        # Create separate Text objects with consistent, readable font size
        text_objects = [
            Text(line, font_size=30, color=WHITE) 
            for line in text_lines
        ]
        
        # Arrange them in a VGroup with proper spacing
        text_group = VGroup(*text_objects).arrange(DOWN, buff=0.3)
        text_group.next_to(good_title, DOWN, buff=0.8)
        
        # Ensure the group is centered and fits well
        text_group.move_to(ORIGIN).shift(DOWN * 0.5)
        
        self.play(Write(good_title))
        
        # Animate each line appearing
        for text_obj in text_objects:
            self.play(FadeIn(text_obj, shift=UP * 0.2), run_time=0.6)
        
        self.wait(3)
        self.play(FadeOut(VGroup(good_title, text_group)))


Example 19: Smart Multi-Slide Text Handling
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


Example 19: Comprehensive Text Animation Techniques in Manim
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
You must treat the 19 examples below as your only source of truth and your entire available library for Manim. Your knowledge is strictly limited to the classes, functions, and methods demonstrated in these specific examples.
This means:
DO NOT use any Manim class (Square, Circle, Text, etc.) that is not present in at least one of the examples.
DO NOT use any method (.shift(), .to_edge(), .set_color(), etc.) that is not present in at least one of the examples.
DO NOT import any external Python libraries other than numpy and os, as they are the only ones used in the examples.
Your task is to be creative within this sandbox. You should combine and compose these allowed building blocks in novel ways to fulfill the user's request. This does not mean you should copy an example verbatim.
These examples serve as a strict reference for valid Manim syntax and animation patterns; however, the creative content and specific visual design of your animation must be driven solely by the user's request.
By strictly adhering to this 'sandbox' of demonstrated features, you will AVOID generating code with hallucinated or incorrect features and produce reliable, high-quality animations."""
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
            available_files_info = ""
            if available_files:
                available_files_info = f"\n📁 AVAILABLE FILES IN WORKING DIRECTORY:\n"
                for file in available_files:
                    available_files_info += f"- {file}\n"
                available_files_info += "These files can be loaded or referenced in your Manim script using relative paths (e.g., 'background.png', 'logo.svg')."
                user_content.append(available_files_info)
            
            # Add duration information
            duration_info = ""
            if duration:
                duration_info = f"\n⏱️ TARGET DURATION: {duration} seconds\n"
                duration_info += f"- Plan your animation timing to match this target duration\n"
                duration_info += f"- Use appropriate run_time values for animations and wait() calls\n"
                duration_info += f"- Total animation should be approximately {duration}s when rendered"
                user_content.append(duration_info)
            
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
        response = self.model.generate_content(final_prompt)
        cleaned_code = response.text.strip()
        if cleaned_code.startswith("```python"): cleaned_code = cleaned_code[9:]
        if cleaned_code.startswith("```"): cleaned_code = cleaned_code[3:]
        if cleaned_code.endswith("```"): cleaned_code = cleaned_code[:-3]
        return cleaned_code.strip()

    def _run_manim_script(self, script_filename: str, asset_unit_path: str, run_logger: logging.Logger):
        command = [
            "manim", "-t", "-q", "l", "--format", "mov",
            script_filename, "GeneratedScene",
        ]
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