import logging
import os
import shutil
from typing import Dict, List, Any
import glob

from . import script_gen
from . import executor
from . import planner
from . import media_utils
from .plugins.base import ToolPlugin
from .plugins.ffmpeg_plugin import FFmpegPlugin

logger = logging.getLogger(__name__)

PLUGIN_REGISTRY: Dict[str, ToolPlugin] = {
    p.name: p for p in [FFmpegPlugin()]
}

def _cleanup_old_intermediate_files(session_path: str):
    """Deletes any leftover intermediate files from a previous failed run."""
    logger.debug(f"Cleaning up old intermediate files in {session_path}")
    old_files = glob.glob(os.path.join(session_path, "intermediate_*"))
    for f in old_files:
        try:
            os.remove(f)
            logger.debug(f"Removed old intermediate file: {f}")
        except OSError as e:
            logger.warning(f"Could not remove old intermediate file {f}: {e}")


def process_complex_request(session_path: str, prompt: str, initial_proxy_name: str) -> Dict[str, Any]:
    """
    Plans and executes a complex, multi-step edit request directly within the session directory.
    Intermediate scripts are kept, intermediate data files are cleaned up on success.
    """
    logger.info(f"Orchestrator starting request in session '{session_path}': '{prompt}'")
    
    # 1. Cleanup old artifacts before starting a new run
    _cleanup_old_intermediate_files(session_path)

    # 2. Create a plan
    plan = planner.create_plan(prompt, list(PLUGIN_REGISTRY.values()))

    # 3. Setup asset tracking for the current run
    initial_input_abs_path = os.path.join(session_path, initial_proxy_name)
    initial_asset_log = {
        "filename": initial_proxy_name,
        **media_utils.get_asset_metadata(initial_input_abs_path)
    }

    completed_steps_log: List[Dict] = []
    # This list tracks intermediate DATA files to be cleaned up on success
    intermediate_data_files: List[str] = []
    
    try:
        for i, step in enumerate(plan):
            is_last_step = (i == len(plan) - 1)
            step_num = i + 1
            logger.info(f"Executing step {step_num}/{len(plan)}: {step['task']}")
            
            plugin = PLUGIN_REGISTRY.get(step["tool"])
            if not plugin: raise ValueError(f"Unknown tool: {step['tool']}")

            # --- Simplified I/O Logic ---
            inputs = {'original_video': initial_proxy_name}
            if i > 0:
                # The output of the last step is an input to this step
                last_step_output_filename = completed_steps_log[-1]["outputs"][0]["filename"]
                inputs['previous_step_output'] = last_step_output_filename
            
            if is_last_step:
                output_filename = f"proxy{int(initial_proxy_name.split('proxy')[1].split('.')[0]) + 1}.mp4"
            else:
                output_filename = f"intermediate_{step_num}.mp4"
            outputs = {"final_video": output_filename}

            # All assets available for the sandbox are now in the session path
            asset_logs_for_sandbox = [initial_asset_log] + [log["outputs"][0] for log in completed_steps_log]
            
            context = {
                "original_prompt": prompt, "full_plan": plan, "current_step": step_num,
                "completed_steps_log": completed_steps_log
            }

            script_content = script_gen.generate_validated_script(
                task=step["task"], plugin=plugin, context=context,
                inputs=inputs, outputs=outputs, asset_logs=asset_logs_for_sandbox,
                session_path=session_path # Pass the session path for validation context
            )
            
            # Scripts are now also created directly in the session directory
            script_filename = f"edit{int(initial_proxy_name.split('proxy')[1].split('.')[0])}_part{step_num}.py"
            script_path_abs = os.path.join(session_path, script_filename)
            with open(script_path_abs, "w") as f: f.write(script_content)

            # Execution CWD is now the session_path
            executor.execute_script(script_path=script_filename, cwd=session_path)
            
            output_path_abs = os.path.join(session_path, output_filename)
            if not os.path.exists(output_path_abs):
                raise FileNotFoundError(f"Script for step {step_num} ran but did not create the expected output: {output_filename}")

            # If this is an intermediate file, add it to the cleanup list
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
            logger.info(f"Step {step_num} completed successfully.")

        final_output_filename = completed_steps_log[-1]["outputs"][0]["filename"]
        logger.info(f"Orchestration complete. Final output: {final_output_filename}")
        
        # On complete success, clean up intermediate DATA files, but leave scripts.
        for f in intermediate_data_files:
            try:
                os.remove(f)
                logger.debug(f"Cleaned up intermediate data file: {f}")
            except OSError as e:
                logger.warning(f"Could not clean up intermediate data file {f}: {e}")

        return {
            "prompt": prompt,
            "output": final_output_filename,
            "scripts": completed_steps_log
        }

    except Exception as e:
        # On failure, we do NOT clean up, to allow for debugging.
        logger.error(f"Orchestration failed at step {i+1}. Intermediate files are left for debugging. Error: {e}", exc_info=True)
        raise RuntimeError(f"Failed during step {i+1} ('{step['task']}'): {e}") from e