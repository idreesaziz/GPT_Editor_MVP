import os
import subprocess
import requests
import time
import logging
import shutil
import sys
import json

# --- CONFIGURATION ---
BASE_URL = "http://127.0.0.1:8000"
TEST_VIDEO_FILENAME = "e2e_test_input.mp4"
LOG_FILENAME = "e2e_test.log"

# A comprehensive list of edit requests to test the system's robustness
PROMPT_CHAIN = [
    # Basic Filters & Color
    {"prompt": "Make the video black and white."},
    {"prompt": "Increase the contrast of the video slightly."},
    {"prompt": "Give the video a vintage, sepia tone look."},
    
    # Speed & Time Manipulation
    {"prompt": "Speed the footage up by 2x."},
    {"prompt": "Now slow the video down to half its current speed."},
    {"prompt": "Trim the video to only show the first 5 seconds."},
    {"prompt": "Cut out the middle 2 seconds of the clip, from second 1 to second 3."},

    # Geometric & Cropping
    {"prompt": "Crop the video to a square aspect ratio, focusing on the center."},
    {"prompt": "Flip the video horizontally."},
    {"prompt": "Rotate the video 90 degrees clockwise."},
    
    # Text Overlays
    {"prompt": "Add the text 'HELLO WORLD' in the top-left corner."},
    {"prompt": "Put the text 'TESTING' in a big white font at the bottom of the screen."},
    
    # Chained & Complex Commands
    {"prompt": "Make the left half of the video blurry, but keep the right half clear."},
    {"prompt": "For the first 3 seconds, make the video grayscale, then return to color."},
    
    # Potential Failure & Recovery Test
    {"prompt": "This prompt is intentionally gibberish and should not work.", "should_fail": True},
    {"prompt": "After the gibberish prompt, please fade the video to black at the end.", "validation_fn": lambda: True}, # A valid prompt to test recovery
    
    # Audio (assuming input has audio)
    {"prompt": "Remove the audio track completely."},
    {"prompt": "Add it back.", "should_fail": True}, # This is impossible, tests planner's reasoning
    {"prompt": "Replace the audio with a sine wave tone.", "validation_fn": lambda: True},
]
REQUEST_TIMEOUT = 240 # Increased timeout for potentially very slow ffmpeg operations

# --- SETUP PATHS & LOGGER ---
try:
    PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
except NameError:
    PROJECT_ROOT = os.getcwd()

# Place logs and test videos in a dedicated 'tests' subdirectory
TESTS_DIR = os.path.join(PROJECT_ROOT, "tests")
os.makedirs(TESTS_DIR, exist_ok=True)
LOG_FILE_PATH = os.path.join(TESTS_DIR, LOG_FILENAME)
TEST_VIDEO_PATH = os.path.join(TESTS_DIR, TEST_VIDEO_FILENAME)

if os.path.exists(LOG_FILE_PATH):
    os.remove(LOG_FILE_PATH)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE_PATH),
        logging.StreamHandler(sys.stdout)
    ]
)

# --- HELPER FUNCTIONS ---

def check_ffmpeg_installed():
    """Checks if ffmpeg is available."""
    if not shutil.which("ffmpeg"):
        logging.error("`ffmpeg` command not found. Please install ffmpeg and ensure it's in your system's PATH.")
        raise FileNotFoundError("ffmpeg is required to run this test.")

def generate_test_video(filepath: str, duration: int = 15):
    """Generates a standard test video with audio using ffmpeg."""
    logging.info(f"Generating test video at: {filepath}")
    try:
        command = [
            'ffmpeg', '-y',
            '-f', 'lavfi', '-i', f'testsrc=duration={duration}:size=640x480:rate=30',
            '-f', 'lavfi', '-i', 'anullsrc=channel_layout=stereo:sample_rate=44100',
            '-c:v', 'libx264',
            '-c:a', 'aac',
            '-pix_fmt', 'yuv420p',
            '-t', str(duration),
            filepath
        ]
        subprocess.run(command, check=True, capture_output=True, text=True)
        logging.info("Test video generated successfully.")
    except subprocess.CalledProcessError as e:
        logging.error(f"Failed to generate test video. FFmpeg stderr:\n{e.stderr}")
        raise

def cleanup(session_id: str):
    """Removes the session directory and the generated test video."""
    logging.info("--- CLEANUP ---")
    if session_id:
        session_path = os.path.join(PROJECT_ROOT, "sessions", session_id)
        if os.path.exists(session_path):
            try:
                shutil.rmtree(session_path)
                logging.info(f"Removed session directory: {session_path}")
            except OSError as e:
                logging.error(f"Error removing session directory {session_path}: {e}")

    if os.path.exists(TEST_VIDEO_PATH):
        try:
            os.remove(TEST_VIDEO_PATH)
            logging.info(f"Removed test video: {TEST_VIDEO_PATH}")
        except OSError as e:
            logging.error(f"Error removing test video {TEST_VIDEO_PATH}: {e}")

# --- TEST FLOW ---

def run_edit_step(session_id: str, step_info: dict, step_num: int) -> bool:
    """Sends a single edit prompt and checks for a success or graceful failure."""
    prompt = step_info["prompt"]
    should_fail = step_info.get("should_fail", False)
    
    logging.info(f"--- STEP {step_num}: PROMPT: '{prompt}' ---")
    if should_fail:
        logging.info("(This step is expected to fail gracefully)")

    payload = {"session_id": session_id, "prompt": prompt}
    
    try:
        response = requests.post(f"{BASE_URL}/edit", json=payload, timeout=REQUEST_TIMEOUT)
        
        # A 500 error on a prompt that should fail is a "pass" for this test
        if should_fail:
            if response.status_code == 500:
                logging.info(f"✅ PASSED: API correctly returned a server error as expected. Message: {response.text[:150]}...")
                return True
            # A 200 with a no-op message is also a "pass"
            if response.status_code == 200 and "No action was taken" in response.json().get("message", ""):
                 logging.info(f"✅ PASSED: API correctly identified prompt as a no-op.")
                 return True
            else:
                logging.error(f"❌ FAILED: Step was expected to fail but returned a success code. Status: {response.status_code}, Body: {response.text}")
                return False

        # For normal prompts, any non-200 code is a failure
        response.raise_for_status()
        response_data = response.json()

        # An explicit "error" status in the JSON is also a failure
        if response_data.get("status") != "success":
            raise ValueError(f"API reported an application-level error: {response_data.get('error', 'Unknown error')}")

        logging.info(f"✅ PASSED: API call successful. Response: {response_data}")
        return True

    except Exception as e:
        if should_fail:
            logging.info(f"✅ PASSED: Step correctly failed with an exception as expected: {e}")
            return True
        logging.error(f"❌ FAILED: Step threw an unexpected exception: {e}", exc_info=False)
        return False


def run_test_suite() -> (bool, str):
    """Executes the full end-to-end test suite and returns the final status and session_id."""
    
    # 1. UPLOAD
    logging.info("--- STEP 1: UPLOADING VIDEO ---")
    session_id = None
    try:
        with open(TEST_VIDEO_PATH, 'rb') as f:
            response = requests.post(f"{BASE_URL}/upload", files={'file': f}, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        session_id = response.json().get("session_id")
        if not session_id: raise ValueError("Response did not contain a session_id.")
        logging.info(f"Upload successful. Session ID: {session_id}")
    except Exception as e:
        logging.error(f"FATAL: Could not upload video. Test cannot continue. Error: {e}")
        return False, None

    # 2. EDITING CHAIN
    all_tests_passed = True
    for i, step_info in enumerate(PROMPT_CHAIN):
        step_passed = run_edit_step(session_id, step_info, step_num=i + 2)
        if not step_passed:
            all_tests_passed = False
            logging.warning(f"Halting test chain due to failure at step {i+2}.")
            break # Stop the test on the first unexpected failure
        time.sleep(1) # Small delay to not overwhelm the server

    return all_tests_passed, session_id


if __name__ == "__main__":
    session_id_to_clean = None
    final_result = False
    try:
        # Prerequisite checks
        try:
            requests.get(BASE_URL, timeout=3)
            logging.info(f"Server is reachable at {BASE_URL}.")
            check_ffmpeg_installed()
        except (requests.ConnectionError, FileNotFoundError) as e:
            logging.error(f"Prerequisite check failed: {e}")
            logging.error("Please ensure the FastAPI server is running and ffmpeg is installed.")
            sys.exit(1)

        generate_test_video(TEST_VIDEO_PATH)
        final_result, session_id_to_clean = run_test_suite()
        
    except Exception as e:
        logging.critical("An unhandled exception terminated the test suite.", exc_info=True)
        final_result = False
        
    finally:
        logging.info("=" * 60)
        if final_result:
            logging.info("✅✅✅ E2E TEST SUITE: PASSED ✅✅✅")
        else:
            logging.error("❌❌❌ E2E TEST SUITE: FAILED ❌❌❌")
        logging.info("=" * 60)
        cleanup(session_id_to_clean)
        sys.exit(0 if final_result else 1)