# test_manim_plugin.py

import logging
import os
import shutil
import sys
import uuid
import time

sys.path.insert(0, os.path.abspath(os.path.dirname(__file__)))

from dotenv import load_dotenv
from app.plugins.manim_plugin import ManimAnimationGenerator, ManimGenerationError

TEST_SESSIONS_DIR = "test_sessions_manim"

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logging.getLogger('app.plugins.manim_plugin').setLevel(logging.DEBUG)

def run_test(plugin: ManimAnimationGenerator, test_case: dict):
    test_name = test_case["name"]
    prompt = test_case["prompt"]
    session_id = str(uuid.uuid4())
    session_path = os.path.join(TEST_SESSIONS_DIR, session_id)
    os.makedirs(session_path, exist_ok=True)
    
    run_logger = logging.getLogger(f"test_run.{test_name}")
    
    print("\n" + "="*80)
    print(f"RUNNING TEST: {test_name}")
    print(f"PROMPT: '{prompt}'")
    print(f"SESSION PATH: {session_path}")
    print("="*80)
    
    # --- Simulate a simpler Planner ---
    # It always asks for a .mov file.
    output_filename = f"gen_asset_{test_case['index']}.mov"
    
    task_details = {
        "task": prompt,
        "output_filename": output_filename,
    }
    # --------------------------------

    if "original_asset_filename" in test_case:
        task_details["original_asset_filename"] = test_case["original_asset_filename"]
        original_script_path = os.path.join(test_case["original_session_path"], f"manim_script_{os.path.splitext(test_case['original_asset_filename'])[0]}.py")
        if os.path.exists(original_script_path):
            shutil.copy(original_script_path, session_path)
        else:
            print(f"\n--- TEST RESULT: SKIPPED (DEPENDENCY FAILED) ---")
            print(f"  Could not find original script for modification: {original_script_path}")
            return None, None

    try:
        start_time = time.time()
        generated_file = plugin.execute_task(task_details, session_path, run_logger)
        duration = time.time() - start_time
        
        print("\n--- TEST RESULT: SUCCESS ---")
        print(f"  Test '{test_name}' completed in {duration:.2f} seconds.")
        print(f"  Generated asset: {generated_file}")
        
        verification_msg = f"  >> Please verify the output video at: {os.path.join(session_path, generated_file)}"
        if "background" not in prompt.lower():
            verification_msg += " (and check that its background is transparent)."
        print(verification_msg)

        return session_path, generated_file

    except ManimGenerationError as e:
        print("\n--- TEST RESULT: FAILED ---")
        print(f"  Test '{test_name}' failed with a ManimGenerationError.")
        print(f"  ERROR: {e}")
        return None, None
    except Exception as e:
        print("\n--- TEST RESULT: FAILED (UNEXPECTED) ---")
        print(f"  Test '{test_name}' failed with an unexpected exception.")
        print(f"  ERROR: {e}")
        return None, None

if __name__ == "__main__":
    print("Loading environment variables from .env file...")
    if not load_dotenv():
        print("WARNING: .env file not found. Make sure GOOGLE_API_KEY is set in your environment.")
        
    if not os.getenv("GOOGLE_API_KEY"):
        print("FATAL: GOOGLE_API_KEY environment variable not set. Exiting.")
        sys.exit(1)

    if os.path.exists(TEST_SESSIONS_DIR):
        print(f"Removing old test directory: {TEST_SESSIONS_DIR}")
        shutil.rmtree(TEST_SESSIONS_DIR)
    os.makedirs(TEST_SESSIONS_DIR, exist_ok=True)
    
    manim_plugin = ManimAnimationGenerator()

    test_cases = [
        {
            "index": 1,
            "name": "Simple Transparent Text",
            "prompt": "Create a 5-second animation. Show the text 'Hello, World!' in the center, then have it fade out.",
        },
        {
            "index": 2,
            "name": "Shape Transformation with Color",
            "prompt": "Animate a red square turning into a blue circle over 3 seconds.",
        },
        {
            "index": 3,
            "name": "Animation with a Solid Background",
            "prompt": "Create a spinning white square on a solid black background.",
        }
    ]

    results = {}
    for test in test_cases:
        session_path, generated_file = run_test(manim_plugin, test)
        if session_path and generated_file:
            results[test["index"]] = (session_path, generated_file)

    if 2 in results:
        original_session_path, original_filename = results[2]
        modification_test = {
            "index": 4,
            "name": "Modification: Change background color",
            "prompt": "Modify the previous animation. Instead of a transparent background, give it a solid dark gray background.",
            "original_asset_filename": original_filename,
            "original_session_path": original_session_path
        }
        run_test(manim_plugin, modification_test)
    else:
        print("\nSkipping modification test because its dependency (Shape Transformation) failed.")

    print("\n" + "="*80)
    print("All tests complete.")
    print(f"Check the '{TEST_SESSIONS_DIR}' directory for all generated assets and logs.")
    print("="*80)