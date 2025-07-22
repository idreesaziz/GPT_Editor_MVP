# app/orchestrator.py

import logging
import os
import json
from typing import Dict, Any, List, Optional

from swimlane import SwimlaneEngine

from . import planner, swml_generator, media_utils
from .plugins.base import ToolPlugin
from .plugins.manim_plugin import ManimAnimationGenerator 
from .utils import Timer

logger = logging.getLogger(__name__)

PLUGIN_REGISTRY: Dict[str, ToolPlugin] = {
    p.name: p for p in [
        ManimAnimationGenerator(), 
    ]
}

def _gather_rich_metadata(sources: List[Dict], session_path: str, run_logger: logging.Logger) -> List[Dict]:
    """
    Gathers rich metadata for a list of source assets.
    For each asset, it combines technical metadata (from ffprobe) with
    creation metadata (from its .meta.json file, if it exists).
    """
    metadata_list = []
    for source in sources:
        asset_filename = source.get('path')
        if not asset_filename:
            continue

        full_asset_path = os.path.join(session_path, asset_filename)
        
        # 1. Get technical metadata
        tech_meta = media_utils.get_asset_metadata(full_asset_path)
        
        # 2. Get creation metadata from .meta.json if it exists
        creation_meta = {}
        meta_filepath = os.path.join(session_path, f"{os.path.splitext(asset_filename)[0]}.meta.json")
        if os.path.exists(meta_filepath):
            try:
                with open(meta_filepath, 'r') as f:
                    meta_content = json.load(f)
                    # Exclude the large plugin_data field from the context for the LLMs to save tokens
                    meta_content.pop("plugin_data", None)
                    creation_meta = {"creation_info": meta_content}
            except (json.JSONDecodeError, IOError) as e:
                run_logger.warning(f"Could not read or parse metadata file: {meta_filepath}. Error: {e}")

        # 3. Merge all metadata into a single object
        merged_meta = {
            "id": source.get('id', 'unknown'),
            "filename": asset_filename,
            **tech_meta,
            **creation_meta
        }
        metadata_list.append(merged_meta)
    return metadata_list


def process_edit_request(session_path: str, prompt: str, current_swml_path: str, new_index: int, prompt_history: list, run_logger: logging.Logger, preview: bool = False) -> Dict[str, Any]:
    run_logger.info("=" * 20 + " ORCHESTRATOR (Iterative Refinement) " + "=" * 20)
    
    MAX_SWML_GENERATION_RETRIES = 3
    last_error_message: Optional[str] = None
    
    with Timer(run_logger, "Total Orchestration Process"):
        with open(current_swml_path, 'r') as f:
            base_swml_data = json.load(f)
        
        composition_settings = base_swml_data.get("composition", {})

        # --- Gather metadata for existing assets to inform the Planner ---
        run_logger.info("Gathering rich metadata for existing assets...")
        existing_assets_metadata_list = _gather_rich_metadata(
            base_swml_data.get('sources', []), session_path, run_logger
        )
        existing_assets_metadata_json_str = json.dumps(existing_assets_metadata_list, indent=2)

        # =================================================================
        # PHASE 1: PLANNING
        # =================================================================
        run_logger.info("=" * 20 + " Phase 1: Planning " + "=" * 20)
        plan = planner.create_plan(
            prompt=prompt, 
            plugins=list(PLUGIN_REGISTRY.values()), 
            edit_index=new_index, 
            run_logger=run_logger,
            available_assets_metadata=existing_assets_metadata_json_str,
            composition_settings=composition_settings
        )
        generation_tasks = plan.get("generation_tasks", [])
        composition_prompt = plan.get("composition_prompt")
        if not composition_prompt:
            raise ValueError("Planner failed to provide a composition_prompt.")

        # =================================================================
        # PHASE 2: ASSET GENERATION
        # =================================================================
        run_logger.info("=" * 20 + " Phase 2: Asset Generation " + "=" * 20)
        newly_generated_sources = []
        if generation_tasks:
            run_logger.info(f"Starting serial generation of {len(generation_tasks)} asset(s)...")
            for i, task_spec in enumerate(generation_tasks):
                tool_name = task_spec.get("tool")
                plugin = PLUGIN_REGISTRY.get(tool_name)
                if not plugin:
                    run_logger.error(f"FATAL: Planner specified a tool '{tool_name}' that is not in the PLUGIN_REGISTRY.")
                    raise ValueError(f"Planner specified unknown tool: '{tool_name}'")
                
                run_logger.info("-" * 20 + f" Generating Asset {i+1}/{len(generation_tasks)} using '{tool_name}' " + "-" * 20)
                generated_filename = plugin.execute_task(task_spec, session_path, run_logger)
                
                asset_id_base = os.path.splitext(generated_filename)[0]
                asset_id = asset_id_base
                source_ids = {s['id'] for s in base_swml_data.get('sources', [])} | {s['id'] for s in newly_generated_sources}
                suffix = 1
                while asset_id in source_ids:
                    asset_id = f"{asset_id_base}_{suffix}"
                    suffix += 1

                newly_generated_sources.append({"id": asset_id, "path": generated_filename})
        else:
            run_logger.info("Planner indicated no new assets are required for this edit.")

        # =================================================================
        # PHASE 3: COMPOSITION & RENDER
        # =================================================================
        run_logger.info("=" * 20 + " Phase 3: Composition & Render " + "=" * 20)
        for attempt in range(MAX_SWML_GENERATION_RETRIES):
            run_logger.info(f"\n--- SWML & RENDER ATTEMPT {attempt + 1}/{MAX_SWML_GENERATION_RETRIES} ---")

            swml_for_llm_with_new_assets = json.loads(json.dumps(base_swml_data))
            swml_for_llm_with_new_assets["sources"].extend(newly_generated_sources)

            # --- Gather metadata for ALL assets (existing + new) for the SWML Generator ---
            all_assets_metadata_list = _gather_rich_metadata(
                swml_for_llm_with_new_assets.get('sources', []), session_path, run_logger
            )
            all_assets_metadata_json_str = json.dumps(all_assets_metadata_list, indent=2)

            run_logger.info("-" * 20 + " Composing SWML " + "-" * 20)
            try:
                final_swml_data = swml_generator.generate_swml(
                    prompt=composition_prompt,
                    current_swml=swml_for_llm_with_new_assets,
                    prompt_history=prompt_history,
                    run_logger=run_logger,
                    last_error=last_error_message,
                    last_warnings=None, # Warnings are not yet implemented in the renderer
                    available_assets_metadata=all_assets_metadata_json_str
                )
                output_swml_filename = f"comp{new_index}.swml"
                new_swml_filepath = os.path.join(session_path, output_swml_filename)
                with open(new_swml_filepath, "w") as f: json.dump(final_swml_data, f, indent=2)
                run_logger.info(f"Saved composition state to {output_swml_filename}")

            except Exception as e:
                last_error_message = f"SWML Generation failed: {str(e)}"
                run_logger.error(f"SWML Generation failed: {e}", exc_info=True)
                if attempt == MAX_SWML_GENERATION_RETRIES - 1:
                    raise RuntimeError(f"Failed to generate valid SWML after {MAX_SWML_GENERATION_RETRIES} attempts.") from e
                continue

            run_logger.info("-" * 20 + " Rendering Final Video " + "-" * 20)
            output_video_filename = f"proxy{new_index}.mp4" 
            output_video_filepath = os.path.join(session_path, output_video_filename)
            
            try:
                with Timer(run_logger, "Swimlane Engine Render"):
                    engine = SwimlaneEngine(
                        swml_path=new_swml_filepath,
                        output_path=output_video_filepath,
                        preview_mode=True
                    )
                    run_logger.info("Rendering final composition in preview mode (low quality for speed)")
                    engine.render()
                    run_logger.info(f"Engine render command for '{output_video_filename}' complete.")
                    last_error_message = None

                if not os.path.exists(output_video_filepath):
                    raise FileNotFoundError("Swimlane engine finished but the output video file was not found.")
                
                run_logger.info(f"SWML and Render successful after {attempt + 1} attempt(s).")
                break 

            except Exception as e:
                last_error_message = f"Rendering failed: {str(e)}"
                run_logger.error(f"Rendering failed: {e}", exc_info=True)
                if attempt == MAX_SWML_GENERATION_RETRIES - 1:
                    raise RuntimeError(f"Failed to render final video after {MAX_SWML_GENERATION_RETRIES} attempts. Last error: {last_error_message}") from e
                continue

        else: # This 'else' block executes if the loop completes without a 'break'
            raise RuntimeError(f"Exceeded max retries ({MAX_SWML_GENERATION_RETRIES}) for SWML generation and rendering.")

        return {
            "prompt": prompt,
            "output_video": output_video_filename,
            "output_swml": output_swml_filename
        }