import logging
import os
import tempfile
import shutil
from typing import Dict, List, Any

from . import script_gen
from . import executor
from . import planner
from .plugins.base import ToolPlugin
from .plugins.ffmpeg_plugin import FFmpegPlugin # We'll keep imports for now

logger = logging.getLogger(__name__)

# The orchestrator knows about all available plugins
# In the future, this could be loaded dynamically
PLUGIN_REGISTRY: Dict[str, ToolPlugin] = {
    p.name: p for p in [FFmpegPlugin()]
}


def process_complex_request(session_path: str, prompt: str, initial_proxy_name: str) -> Dict[str, Any]:
    """
    Plans and executes a complex, multi-step edit request.
    Returns a dictionary for the history log.
    """
    logger.info(f"Orchestrator processing request for session '{session_path}': '{prompt}'")

    # 1. Create a plan
    plan = planner.create_plan(prompt, list(PLUGIN_REGISTRY.values()))
    
    # 2. Setup a temporary working directory for the chain
    with tempfile.TemporaryDirectory(dir=session_path) as temp_dir:
        logger.debug(f"Created temporary working directory: {temp_dir}")
        
        # This tracks the filenames of outputs from each step
        step_outputs: Dict[int, str] = {}
        # The main input video for the whole chain
        initial_input_path = os.path.join("..", initial_proxy_name) # Relative path to temp_dir
        
        # *** THE FIX IS HERE ***
        # This log collects the full context of each completed step to inform the next one.
        completed_steps_log: List[Dict] = []

        try:
            # 3. Execute the plan step-by-step
            for i, step in enumerate(plan):
                is_last_step = (i == len(plan) - 1)
                step_num = i + 1
                logger.info(f"Executing step {step_num}/{len(plan)}: {step['task']}")
                
                tool_name = step["tool"]
                plugin = PLUGIN_REGISTRY.get(tool_name)
                if not plugin:
                    raise ValueError(f"Planner assigned an unknown tool: '{tool_name}'")
                
                if i == 0:
                    inputs = {"video": initial_input_path}
                else:
                    inputs = {"video": step_outputs[i - 1]}

                if is_last_step:
                    output_file = os.path.join("..", f"proxy{int(initial_proxy_name.split('proxy')[1].split('.')[0]) + 1}.mp4")
                else:
                    output_file = f"intermediate_{step_num}.mp4"
                
                outputs = {"video": output_file}

                # *** THE FIX IS HERE ***
                # The context now includes the log of previously completed steps in this chain.
                context = {
                    "original_prompt": prompt,
                    "full_plan": plan,
                    "current_step": step_num,
                    "completed_steps_log": completed_steps_log 
                }

                # 4. Delegate single script generation to the specialist
                script_content = script_gen.generate_validated_script(
                    task=step["task"],
                    plugin=plugin,
                    context=context,
                    inputs=inputs,
                    outputs=outputs
                )
                
                script_filename = f"edit{int(initial_proxy_name.split('proxy')[1].split('.')[0])}_part{step_num}.py"
                script_path = os.path.join(temp_dir, script_filename)
                with open(script_path, "w") as f:
                    f.write(script_content)

                # 5. Delegate execution to the robot
                executor.execute_script(script_path=script_path, cwd=temp_dir)
                
                step_outputs[i] = output_file

                # *** THE FIX IS HERE ***
                # Append the result of the successful step to the log for the *next* step.
                completed_steps_log.append({
                    "step_number": step_num,
                    "task": step["task"],
                    "tool": tool_name,
                    "inputs": inputs,
                    "outputs": outputs,
                    "generated_script": script_content
                })

                logger.info(f"Step {step_num} completed successfully.")

            final_output_name = os.path.basename(step_outputs[len(plan) - 1])
            logger.info(f"Orchestration complete. Final output: {final_output_name}")

            # Return a detailed log for history.json
            return {
                "prompt": prompt,
                "output": final_output_name,
                "scripts": completed_steps_log # This log now contains everything we need
            }

        except Exception as e:
            # If any step fails, the context manager will automatically clean up temp_dir.
            logger.error(f"Orchestration failed at step {i+1}. Error: {e}")
            raise RuntimeError(f"Failed during step {i+1} ('{step['task']}'): {e}") from e