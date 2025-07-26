# app/orchestrator.py

import logging
import os
import json
from typing import Dict, Any, List, Optional
import uuid

from swimlane import SwimlaneEngine

from . import planner, swml_generator, media_utils
from .plugins.base import ToolPlugin
from .plugins.manim_plugin import ManimAnimationGenerator 
from .utils import Timer
from .report_collector import ReportCollector

logger = logging.getLogger(__name__)

PLUGIN_REGISTRY: Dict[str, ToolPlugin] = {
    p.name: p for p in [
        ManimAnimationGenerator(), 
    ]
}

def _get_asset_unit_path(swml_asset_path: str) -> Optional[str]:
    """Given a path from SWML, returns the path to its asset unit directory if it's a generated asset."""
    if swml_asset_path and swml_asset_path.startswith("assets/"):
        return os.path.dirname(swml_asset_path)
    return None

def _gather_rich_metadata(sources: List[Dict], session_path: str, run_logger: logging.Logger) -> List[Dict]:
    """
    Gathers rich metadata for a list of source assets.
    For each asset, it combines technical metadata (from ffprobe) with
    creation metadata (from its asset unit's metadata.json file, if it exists).
    """
    metadata_list = []
    for source in sources:
        swml_path = source.get('path')
        if not swml_path:
            continue

        full_disk_path = os.path.join(session_path, swml_path)
        
        # 1. Get technical metadata from the asset file itself
        tech_meta = media_utils.get_asset_metadata(full_disk_path)
        
        # 2. Get creation metadata from the unit's metadata.json
        creation_meta = {}
        asset_unit_dir = _get_asset_unit_path(swml_path)
        if asset_unit_dir:
            meta_filepath = os.path.join(session_path, asset_unit_dir, "metadata.json")
            if os.path.exists(meta_filepath):
                try:
                    with open(meta_filepath, 'r') as f:
                        meta_content = json.load(f)
                        # Exclude the large plugin_data field from the context for the LLMs
                        meta_content.pop("plugin_data", None)
                        creation_meta = {"creation_info": meta_content}
                except (json.JSONDecodeError, IOError) as e:
                    run_logger.warning(f"Could not read or parse metadata file: {meta_filepath}. Error: {e}")

        # 3. Merge all metadata into a single object
        merged_meta = {
            "id": source.get('id', 'unknown'),
            "filename": swml_path, # Use the full SWML path for uniqueness
            **tech_meta,
            **creation_meta
        }
        metadata_list.append(merged_meta)
    return metadata_list


def process_edit_request(session_path: str, prompt: str, current_swml_path: str, new_index: int, prompt_history: list, run_logger: logging.Logger, preview: bool = False, status_callback=None) -> Dict[str, Any]:
    run_logger.info("=" * 20 + " ORCHESTRATOR (Iterative Refinement) " + "=" * 20)
    
    report = ReportCollector(edit_index=new_index, user_prompt=prompt)
    MAX_SWML_GENERATION_RETRIES = 3
    last_error_message: Optional[str] = None
    
    try:
        with Timer(run_logger, "Total Orchestration Process"):
            with open(current_swml_path, 'r') as f:
                base_swml_data = json.load(f)
            
            composition_settings = base_swml_data.get("composition", {})

            run_logger.info("Gathering rich metadata for existing assets...")
            existing_assets_metadata_list = _gather_rich_metadata(
                base_swml_data.get('sources', []), session_path, run_logger
            )
            existing_assets_metadata_json_str = json.dumps(existing_assets_metadata_list, indent=2)

            # =================================================================
            # PHASE 1: PLANNING
            # =================================================================
            report.start_phase("planning")
            run_logger.info("=" * 20 + " Phase 1: Planning " + "=" * 20)
            
            # Update status to planning phase
            if status_callback:
                status_callback("planning", "planning")
            
            try:
                plan = planner.create_plan(
                    prompt=prompt, 
                    plugins=list(PLUGIN_REGISTRY.values()), 
                    edit_index=new_index, 
                    run_logger=run_logger,
                    available_assets_metadata=existing_assets_metadata_json_str,
                    composition_settings=composition_settings
                )
                report.set_ai_plan(plan)
                generation_tasks = plan.get("generation_tasks", [])
                composition_prompt = plan.get("composition_prompt")
                if not composition_prompt:
                    raise ValueError("Planner failed to provide a composition_prompt.")
                report.complete_phase("planning", success=True)
            except Exception as e:
                report.add_error("planning", "planner_error", str(e), e)
                report.complete_phase("planning", success=False)
                raise

            # =================================================================
            # PHASE 2: ASSET GENERATION
            # =================================================================
            report.start_phase("asset_generation")
            run_logger.info("=" * 20 + " Phase 2: Asset Generation " + "=" * 20)
            
            # Update status to asset generation phase
            if status_callback:
                status_callback("asset_generation", "generating assets")
            
            newly_generated_sources = []
            try:
                if generation_tasks:
                    run_logger.info(f"Starting serial generation of {len(generation_tasks)} asset unit(s)...")
                    for i, task_spec in enumerate(generation_tasks):
                        tool_name = task_spec.get("tool")
                        plugin = PLUGIN_REGISTRY.get(tool_name)
                        unit_id = task_spec.get("unit_id")
                        if not plugin or not unit_id:
                            error_msg = f"Planner task {i+1} is missing a 'tool' or 'unit_id'."
                            report.add_error("asset_generation", "invalid_task", error_msg)
                            raise ValueError(error_msg)
                        
                        run_logger.info("-" * 20 + f" Generating Asset Unit '{unit_id}' using '{tool_name}' " + "-" * 20)
                        
                        asset_unit_path = os.path.join(session_path, "assets", unit_id)
                        os.makedirs(asset_unit_path, exist_ok=True)

                        # For amendments, fetch original plugin data
                        if "original_asset_path" in task_spec:
                            run_logger.info(f"Amendment task detected. Original asset path: {task_spec['original_asset_path']}")
                            original_unit_dir = _get_asset_unit_path(task_spec['original_asset_path'])
                            if original_unit_dir:
                                original_meta_path = os.path.join(session_path, original_unit_dir, "metadata.json")
                                try:
                                    with open(original_meta_path, 'r') as f:
                                        original_meta = json.load(f)
                                    task_spec['original_plugin_data'] = original_meta.get('plugin_data', {})
                                    run_logger.debug("Successfully loaded original plugin_data for amendment.")
                                except (FileNotFoundError, json.JSONDecodeError) as e:
                                    run_logger.warning(f"Could not load metadata for amendment from {original_meta_path}: {e}")
                        
                        try:
                            # Plugin now returns a list of filenames relative to its unit directory
                            child_assets = plugin.execute_task(task_spec, asset_unit_path, run_logger)
                            
                            for child_asset_filename in child_assets:
                                swml_path = os.path.join("assets", unit_id, child_asset_filename)
                                full_disk_path = os.path.join(session_path, swml_path)
                                
                                asset_metadata = media_utils.get_asset_metadata(full_disk_path)
                                report.add_asset_created(
                                    filename=swml_path,
                                    tool_used=tool_name,
                                    metadata=asset_metadata.get('metadata', {}),
                                    generation_prompt=task_spec.get('task', '')
                                )
                                
                                source_id = f"{unit_id}_{os.path.splitext(child_asset_filename)[0]}".replace("-", "_")
                                newly_generated_sources.append({"id": source_id, "path": swml_path})

                            report.increment_asset_generation_tasks()
                            
                        except Exception as e:
                            report.add_error("asset_generation", "generation_error", f"Failed to generate asset unit '{unit_id}': {str(e)}", e)
                            raise
                else:
                    run_logger.info("Planner indicated no new assets are required for this edit.")
                
                report.complete_phase("asset_generation", success=True)
                
            except Exception as e:
                report.complete_phase("asset_generation", success=False)
                raise

            # =================================================================
            # PHASE 3: COMPOSITION & RENDER
            # =================================================================
            report.start_phase("composition")
            run_logger.info("=" * 20 + " Phase 3: Composition & Render " + "=" * 20)
            
            # Update status to composition phase
            if status_callback:
                status_callback("composition", "composing video")
            
            final_swml_data = None
            output_swml_filename = None
            new_swml_filepath = None
            
            try:
                for attempt in range(MAX_SWML_GENERATION_RETRIES):
                    run_logger.info(f"\n--- SWML & RENDER ATTEMPT {attempt + 1}/{MAX_SWML_GENERATION_RETRIES} ---")
                    report.increment_swml_attempts()

                    swml_for_llm_with_new_assets = json.loads(json.dumps(base_swml_data))
                    swml_for_llm_with_new_assets["sources"].extend(newly_generated_sources)

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
                            last_warnings=None,
                            available_assets_metadata=all_assets_metadata_json_str
                        )
                        output_swml_filename = f"comp{new_index}.swml"
                        new_swml_filepath = os.path.join(session_path, output_swml_filename)
                        with open(new_swml_filepath, "w") as f: 
                            json.dump(final_swml_data, f, indent=2)
                        run_logger.info(f"Saved composition state to {output_swml_filename}")

                    except Exception as e:
                        last_error_message = f"SWML Generation failed: {str(e)}"
                        report.add_error("composition", "swml_generation_error", last_error_message, e)
                        run_logger.error(f"SWML Generation failed: {e}", exc_info=True)
                        if attempt == MAX_SWML_GENERATION_RETRIES - 1:
                            report.complete_phase("composition", success=False)
                            raise RuntimeError(f"Failed to generate valid SWML after {MAX_SWML_GENERATION_RETRIES} attempts.") from e
                        continue

                    if attempt == 0:
                        report.complete_phase("composition", success=True)
                        report.start_phase("rendering")
                        # Update status to rendering phase
                        if status_callback:
                            status_callback("rendering", "rendering video")

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
                        
                        report.set_final_outputs(
                            video_path=output_video_filepath,
                            swml_path=new_swml_filepath,
                            swml_content=final_swml_data
                        )
                        report.complete_phase("rendering", success=True)
                        run_logger.info(f"SWML and Render successful after {attempt + 1} attempt(s).")
                        break 

                    except Exception as e:
                        last_error_message = f"Rendering failed: {str(e)}"
                        report.add_error("rendering", "render_error", last_error_message, e)
                        run_logger.error(f"Rendering failed: {e}", exc_info=True)
                        if attempt == MAX_SWML_GENERATION_RETRIES - 1:
                            report.complete_phase("rendering", success=False)
                            raise RuntimeError(f"Failed to render final video after {MAX_SWML_GENERATION_RETRIES} attempts. Last error: {last_error_message}") from e
                        continue

                else:
                    raise RuntimeError(f"Exceeded max retries ({MAX_SWML_GENERATION_RETRIES}) for SWML generation and rendering.")

            except Exception as e:
                if report.report["execution_phases"]["composition"]["status"] == "in_progress":
                    report.complete_phase("composition", success=False)
                if report.report["execution_phases"]["rendering"]["status"] in ["not_started", "in_progress"]:
                    if report.report["execution_phases"]["rendering"]["status"] == "not_started":
                        report.start_phase("rendering")
                    report.complete_phase("rendering", success=False)
                raise

        return report.finalize(success=True)

    except Exception as e:
        for phase_name, phase_data in report.report["execution_phases"].items():
            if phase_data["status"] == "in_progress":
                report.complete_phase(phase_name, success=False)
        
        if not any(error["message"] == str(e) for error in report.report["errors"]):
            report.add_error("orchestrator", "fatal_error", str(e), e)
        
        return report.finalize(success=False)