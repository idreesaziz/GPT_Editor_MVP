# app/orchestrator.py

import logging
import os
import json
from typing import Dict, Any, List, Optional

# Import the specific class from your installed package
from swimlane import SwimlaneEngine

from . import planner, swml_generator, media_utils
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
    run_logger.info("=" * 20 + " ORCHESTRATOR (Iterative Refinement) " + "=" * 20)
    
    MAX_SWML_GENERATION_RETRIES = 3 # Max attempts for LLM to fix its SWML/render issues
    
    # Initialize feedback variables for the iterative loop
    last_error_message: Optional[str] = None
    last_warnings: Optional[str] = None
    
    output_video_filename: Optional[str] = None
    output_swml_filename: Optional[str] = None

    with Timer(run_logger, "Total Orchestration Process"):
        # Load the current SWML once to get sources for planner and composition
        with open(current_swml_path, 'r') as f:
            base_swml_data = json.load(f)

        # --- Extract metadata for ALL existing assets (for Planner) ---
        existing_assets_metadata_list: List[Dict[str, Any]] = []
        for source in base_swml_data.get('sources', []):
            asset_filename = source.get('path')
            if asset_filename:
                full_asset_path = os.path.join(session_path, asset_filename)
                metadata = media_utils.get_asset_metadata(full_asset_path)
                metadata['id'] = source.get('id', 'unknown') 
                metadata['filename'] = asset_filename
                existing_assets_metadata_list.append(metadata)
        
        # Convert to JSON string for the Planner LLM
        existing_assets_metadata_json_str = json.dumps(existing_assets_metadata_list, indent=2)

        # 1. PLANNING (Now receives existing asset metadata)
        plan = planner.create_plan(
            prompt, 
            list(PLUGIN_REGISTRY.values()), 
            new_index, 
            run_logger,
            available_assets_metadata=existing_assets_metadata_json_str # Pass asset metadata to planner
        )
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

        # 3. Iterative SWML Generation & Rendering
        for attempt in range(MAX_SWML_GENERATION_RETRIES):
            run_logger.info(f"\n--- SWML & RENDER ATTEMPT {attempt + 1}/{MAX_SWML_GENERATION_RETRIES} ---")

            # Start with the base SWML data from the current_swml_path
            # and add newly generated assets for the SWML generator's context.
            swml_for_llm_with_new_assets = json.loads(json.dumps(base_swml_data)) # Deep copy
            swml_for_llm_with_new_assets["sources"].extend(newly_generated_sources)

            # --- Extract metadata for ALL available assets (for SWML Generator) ---
            # This is done *inside* the loop so it includes newly generated assets
            # if they were just created.
            all_available_assets_metadata_list: List[Dict[str, Any]] = []
            for source in swml_for_llm_with_new_assets.get('sources', []):
                asset_filename = source.get('path')
                if asset_filename:
                    full_asset_path = os.path.join(session_path, asset_filename)
                    metadata = media_utils.get_asset_metadata(full_asset_path)
                    metadata['id'] = source.get('id', 'unknown') 
                    metadata['filename'] = asset_filename
                    all_available_assets_metadata_list.append(metadata)
            
            # Convert to JSON string for the LLM
            all_available_assets_metadata_json_str = json.dumps(all_available_assets_metadata_list, indent=2)


            # 3a. COMPOSITION (SWML Generation)
            run_logger.info("-" * 20 + " Composing SWML " + "-" * 20)
            try:
                final_swml_data = swml_generator.generate_swml(
                    prompt=composition_prompt,
                    current_swml=swml_for_llm_with_new_assets, # Pass SWML with all available assets
                    prompt_history=prompt_history,
                    run_logger=run_logger,
                    last_error=last_error_message,      # Pass error feedback from previous attempt
                    last_warnings=last_warnings,        # Pass warning feedback from previous attempt
                    available_assets_metadata=all_available_assets_metadata_json_str # Pass all asset metadata
                )
                output_swml_filename = f"comp{new_index}.swml"
                new_swml_filepath = os.path.join(session_path, output_swml_filename)
                with open(new_swml_filepath, "w") as f: json.dump(final_swml_data, f, indent=2)
                run_logger.info(f"Saved composition state to {output_swml_filename}")

            except Exception as e:
                last_error_message = f"SWML Generation failed: {str(e)}"
                last_warnings = None # Clear warnings on a new error
                run_logger.error(f"SWML Generation failed: {e}", exc_info=True)
                if attempt == MAX_SWML_GENERATION_RETRIES - 1:
                    raise RuntimeError(f"Failed to generate valid SWML after {MAX_SWML_GENERATION_RETRIES} attempts.") from e
                continue # Try again

            # 3b. RENDER
            run_logger.info("-" * 20 + " Rendering Final Video " + "-" * 20)
            output_video_filename = f"proxy{new_index}.mp4"
            output_video_filepath = os.path.join(session_path, output_video_filename)
            
            try:
                with Timer(run_logger, "Swimlane Engine Render"):
                    run_logger.info("Initializing SwimlaneEngine with file paths.")
                    run_logger.debug(f"  SWML Path: {new_swml_filepath}")
                    run_logger.debug(f"  Output Path: {output_video_filepath}")
                    
                    engine = SwimlaneEngine(
                        swml_path=new_swml_filepath,
                        output_path=output_video_filepath
                    )

                    # SwimlaneEngine's render method doesn't return stdout/stderr directly.
                    # If it logged internally, it would appear in `run_logger`.
                    # For a real implementation needing stdout/stderr for warnings,
                    # the SwimlaneEngine library itself would need modification,
                    # or its calls would need to be wrapped with subprocess capture.
                    engine.render()
                    
                    run_logger.info(f"Engine render command for '{output_video_filename}' complete.")
                    
                    # Placeholder for capturing warnings from SwimlaneEngine:
                    # If SwimlaneEngine were to provide stdout/stderr, we'd process it here.
                    # For now, simulate a warning for demonstration if needed, or keep empty.
                    # For example: last_warnings = "Render completed with a minor codec warning." 
                    last_warnings = None # Assume no warnings unless captured
                    last_error_message = None # Clear error on success

                # Final check to ensure the file was created.
                if not os.path.exists(output_video_filepath):
                    raise FileNotFoundError("Swimlane engine finished but the output video file was not found.")
                
                # If we reach here, both SWML generation and rendering were successful. Break the loop.
                run_logger.info(f"SWML and Render successful after {attempt + 1} attempt(s).")
                break 

            except Exception as e:
                last_error_message = f"Rendering failed: {str(e)}"
                last_warnings = None # Clear warnings on a new error
                run_logger.error(f"Rendering failed: {e}", exc_info=True)
                if attempt == MAX_SWML_GENERATION_RETRIES - 1:
                    raise RuntimeError(f"Failed to render final video after {MAX_SWML_GENERATION_RETRIES} attempts. Last error: {last_error_message}") from e
                continue # Try again

        else: # This 'else' block executes if the loop completes without a 'break'
            raise RuntimeError(f"Exceeded max retries ({MAX_SWML_GENERATION_RETRIES}) for SWML generation and rendering.")

        return {
            "prompt": prompt,
            "output_video": output_video_filename,
            "output_swml": output_swml_filename
        }