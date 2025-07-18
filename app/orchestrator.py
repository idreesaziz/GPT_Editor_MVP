# app/orchestrator.py

import logging
import os
import json
from typing import Dict, Any

# Import the specific class from your installed package
from swimlane import SwimlaneEngine

from . import planner, swml_generator
from .plugins.base import ToolPlugin
from .utils import Timer

logger = logging.getLogger(__name__)

# --- Placeholder Plugin Registry for Day 1 ---
# This section remains correct and does not need changes.
class DummyPlugin(ToolPlugin):
    def __init__(self, name, description):
        self._name = name
        self._description = description
    @property
    def name(self) -> str: return self._name
    @property
    def description(self) -> str: return self._description
    def execute_task(self, task_details, session_path, run_logger) -> str:
        output_file = task_details['output_filename']
        run_logger.info(f"DUMMY PLUGIN '{self.name}': Pretending to execute task '{task_details['task']}'.")
        # Create an empty placeholder file so it exists on disk.
        with open(os.path.join(session_path, output_file), 'w') as f:
            f.write(f"Dummy asset from {self.name}")
        run_logger.info(f"DUMMY PLUGIN '{self.name}': Created placeholder asset '{output_file}'.")
        return output_file

PLUGIN_REGISTRY: Dict[str, ToolPlugin] = {
    p.name: p for p in [
        DummyPlugin("Manim Animation Generator", "Creates animated videos from text, shapes, and code."),
        DummyPlugin("Imagen Image Generator", "Generates photorealistic or artistic images from a text description."),
        DummyPlugin("Text-to-Speech Voiceover Generator", "Converts text into a natural-sounding audio voiceover file.")
    ]
}
# --- End Placeholder ---

def process_edit_request(session_path: str, prompt: str, current_swml_path: str, new_index: int, prompt_history: list, run_logger: logging.Logger) -> Dict[str, Any]:
    run_logger.info("=" * 20 + " ORCHESTRATOR (Serial) " + "=" * 20)
    with Timer(run_logger, "Total Orchestration Process"):
        # 1. PLANNING (Unchanged)
        plan = planner.create_plan(prompt, list(PLUGIN_REGISTRY.values()), new_index, run_logger)
        generation_tasks = plan.get("generation_tasks", [])
        composition_prompt = plan.get("composition_prompt")
        if not composition_prompt:
            raise ValueError("Planner failed to provide a composition_prompt, which is mandatory.")

        # 2. GENERATION (Unchanged)
        newly_generated_sources = []
        if generation_tasks:
            run_logger.info(f"Starting serial generation of {len(generation_tasks)} asset(s)...")
            for i, task_spec in enumerate(generation_tasks):
                tool_name = task_spec.get("tool")
                plugin = PLUGIN_REGISTRY.get(tool_name)
                if not plugin: raise ValueError(f"Planner specified unknown tool: '{tool_name}'")
                
                run_logger.info("-" * 20 + f" Generating Asset {i+1}/{len(generation_tasks)} " + "-" * 20)
                generated_filename = plugin.execute_task(task_spec, session_path, run_logger)
                asset_id = os.path.splitext(generated_filename)[0]
                newly_generated_sources.append({"id": asset_id, "path": generated_filename})
        else:
            run_logger.info("Planner indicated no new assets are required for this edit.")

        # 3. COMPOSITION (Unchanged)
        run_logger.info("-" * 20 + " Composing Final Video " + "-" * 20)
        with open(current_swml_path, 'r') as f:
            current_swml = json.load(f)
        current_swml["sources"].extend(newly_generated_sources)

        final_swml_data = swml_generator.generate_swml(
            prompt=composition_prompt,
            current_swml=current_swml,
            prompt_history=prompt_history,
            run_logger=run_logger
        )
        new_swml_filename = f"comp{new_index}.swml"
        new_swml_filepath = os.path.join(session_path, new_swml_filename)
        with open(new_swml_filepath, "w") as f: json.dump(final_swml_data, f, indent=2)
        run_logger.info(f"Saved composition state to {new_swml_filename}")

        # --- 4. RENDER (REVISED LOGIC) ---
        run_logger.info("-" * 20 + " Rendering Final Video " + "-" * 20)
        output_video_filename = f"proxy{new_index}.mp4"
        output_video_filepath = os.path.join(session_path, output_video_filename)
        
        try:
            with Timer(run_logger, "Swimlane Engine Render"):
                # Initialize the engine instance by passing the required arguments
                # to its constructor, as indicated by the TypeError.
                run_logger.info("Initializing SwimlaneEngine with file paths.")
                run_logger.debug(f"  SWML Path: {new_swml_filepath}")
                run_logger.debug(f"  Output Path: {output_video_filepath}")
                
                engine = SwimlaneEngine(
                    swml_path=new_swml_filepath,
                    output_path=output_video_filepath
                )

                # Now, call the main action method of the engine.
                # We assume this is '.render()' based on the engine's design.
                engine.render()
                
                run_logger.info(f"Engine render command for '{output_video_filename}' complete.")

            # Final check to ensure the file was created.
            if not os.path.exists(output_video_filepath):
                 raise FileNotFoundError("Swimlane engine finished but the output video file was not found.")

        except Exception as e:
            run_logger.error(f"Swimlane Engine failed during render: {e}", exc_info=True)
            raise RuntimeError(f"Failed to render final video with Swimlane Engine. See logs for details.")

        return {
            "prompt": prompt,
            "output_video": output_video_filename,
            "output_swml": new_swml_filename
        }