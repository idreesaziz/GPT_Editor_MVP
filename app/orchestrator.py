import logging
import os
import shutil
from typing import Dict, List, Any
import glob
import json # <-- Add import

from . import script_gen
from . import executor
from . import planner
from . import media_utils
from .plugins.base import ToolPlugin
from .plugins.ffmpeg_plugin import FFmpegPlugin
from .plugins.metadata_extractor_plugin import MetadataExtractorPlugin

logger = logging.getLogger(__name__) # Keep for general logging

PLUGIN_REGISTRY: Dict[str, ToolPlugin] = {
    p.name: p for p in [FFmpegPlugin(), MetadataExtractorPlugin()]
}

def _cleanup_old_intermediate_files(session_path: str, run_logger: logging.Logger):
    """Deletes any leftover intermediate files from a previous failed run."""
    run_logger.debug(f"Cleaning up old intermediate files in {session_path}")
    old_files = glob.glob(os.path.join(session_path, "intermediate_*"))
    for f in old_files:
        try:
            if os.path.isfile(f) or os.path.islink(f):
                os.remove(f)
            elif os.path.isdir(f):
                shutil.rmtree(f)
            run_logger.debug(f"Removed old intermediate file: {f}")
        except OSError as e:
            run_logger.warning(f"Could not remove old intermediate file {f}: {e}")


def process_complex_request(session_path: str, prompt: str, initial_proxy_name: str, run_logger: logging.Logger) -> Dict[str, Any]:
    """
    Plans and executes a complex, multi-step edit request directly within the session directory.
    """
    run_logger.info("Orchestrator starting process.")
    
    _cleanup_old_intermediate_files(session_path, run_logger)
    plan = planner.create_plan(prompt, list(PLUGIN_REGISTRY.values()), run_logger)
    run_logger.info(f"PLANNER produced a {len(plan)}-step plan:")
    for i, step in enumerate(plan):
        run_logger.info(f"  Step {i+1}: [{step['tool']}] - {step['task']}")
    
    initial_input_abs_path = os.path.join(session_path, initial_proxy_name)
    initial_asset_log = {
        "filename": initial_proxy_name,
        **media_utils.get_asset_metadata(initial_input_abs_path)
    }
    run_logger.debug(f"Initial asset log: {json.dumps(initial_asset_log, indent=2)}")

    completed_steps_log: List[Dict] = []
    intermediate_data_files: List[str] = []
    
    current_edit_number = int(initial_proxy_name.split('proxy')[1].split('.')[0])

    try:
        for i, step in enumerate(plan):
            is_last_step = (i == len(plan) - 1)
            step_num = i + 1
            run_logger.info("-" * 20 + f" EXECUTING STEP {step_num}/{len(plan)} " + "-" * 20)
            run_logger.info(f"Task: {step['task']}")
            
            plugin = PLUGIN_REGISTRY.get(step["tool"])
            if not plugin: raise ValueError(f"Unknown tool: {step['tool']}")

            inputs = {'initial_video': initial_proxy_name}
            if i > 0:
                for log_entry in reversed(completed_steps_log):
                    output_file = log_entry["outputs"][0]["filename"]
                    if '.json' in output_file and 'metadata_json' not in inputs:
                         inputs['metadata_json'] = output_file
                    elif '.mp4' in output_file and 'previous_video' not in inputs:
                         inputs['previous_video'] = output_file
                if 'previous_video' in inputs:
                     inputs['previous_step_output'] = inputs['previous_video'] # Keep for compatibility
            
            if is_last_step:
                output_filename = f"proxy{current_edit_number + 1}.mp4"
                outputs = {"final_video": output_filename}
            else:
                if plugin.name == "Metadata Extractor":
                    output_filename = f"intermediate_{step_num}_metadata.json"
                    outputs = {"metadata_json": output_filename}
                else:
                    output_filename = f"intermediate_{step_num}_video.mp4"
                    outputs = {"intermediate_video": output_filename}
            
            run_logger.info(f"Step Inputs: {inputs}")
            run_logger.info(f"Step Outputs: {outputs}")

            asset_logs_for_script_gen = [initial_asset_log] + [log["outputs"][0] for log in completed_steps_log]
            
            script_history_content = []
            for past_step in completed_steps_log:
                past_script_filename = past_step.get("script")
                if past_script_filename:
                    try:
                        with open(os.path.join(session_path, past_script_filename), 'r') as f:
                            lines = f.readlines()
                        try:
                            separator_index = lines.index('\n')
                            core_content = "".join(lines[separator_index+1:])
                        except ValueError:
                            logger.warning(f"Could not find header/body separator in script {past_script_filename}. Using full content for history.")
                            core_content = "".join(lines)
                        script_history_content.append(f"# --- Code from Step {past_step['step_number']}: {past_step['task']} ---\n{core_content}\n# --- End of Code ---\n")
                    except FileNotFoundError:
                        run_logger.warning(f"Could not find script {past_script_filename} to build history.")
            script_history = "\n".join(script_history_content) if script_history_content else "No scripts have been executed yet."

            context = {
                "original_prompt": prompt, "full_plan": plan, "current_step": step_num,
                "script_history": script_history
            }

            script_content = script_gen.generate_validated_script(
                task=step["task"], plugin=plugin, context=context,
                inputs=inputs, outputs=outputs, asset_logs=asset_logs_for_script_gen,
                session_path=session_path, run_logger=run_logger
            )
            
            script_filename = f"edit{current_edit_number}_part{step_num}.py"
            script_path_abs = os.path.join(session_path, script_filename)
            with open(script_path_abs, "w") as f: f.write(script_content)
            run_logger.info(f"Generated script '{script_filename}'")

            executor.execute_script(script_path=script_filename, cwd=session_path, run_logger=run_logger)
            
            output_path_abs = os.path.join(session_path, output_filename)
            if not os.path.exists(output_path_abs):
                raise FileNotFoundError(f"Script for step {step_num} ran but did not create expected output: {output_filename}")

            if not is_last_step:
                intermediate_data_files.append(output_path_abs)

            output_asset_log = {
                "filename": output_filename,
                **media_utils.get_asset_metadata(output_path_abs)
            }
            
            completed_steps_log.append({
                "step_number": step_num, "task": step["task"], "tool": step["tool"],
                "inputs": inputs, "outputs": [output_asset_log], "script": script_filename
            })
            run_logger.info(f"Step {step_num} completed successfully. Output asset: {output_asset_log['filename']}")

        final_output_filename = completed_steps_log[-1]["outputs"][0]["filename"]
        run_logger.info(f"Orchestration complete. Final output: {final_output_filename}")
        
        for f in intermediate_data_files:
            try:
                os.remove(f)
                run_logger.debug(f"Cleaned up intermediate data file: {f}")
            except OSError as e:
                run_logger.warning(f"Could not clean up intermediate data file {f}: {e}")

        return {
            "prompt": prompt,
            "output": final_output_filename,
            "scripts": completed_steps_log
        }

    except Exception as e:
        run_logger.error(f"Orchestration failed at step {i+1 if 'i' in locals() else 1}. Error: {e}", exc_info=True)
        task_name = step['task'] if 'step' in locals() else "planning"
        raise RuntimeError(f"Failed during step '{task_name}': {e}") from e