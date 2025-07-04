import os
import subprocess
import requests
import time
import logging
import shutil
import sys

# --- CONFIGURATION ---
BASE_URL = "http://127.0.0.1:8000"
TEST_VIDEO_FILENAME = "robustness_test_input.mp4"
LOG_FILENAME = "robustness_test.log"
# The sequence of prompts to send to the editor
PROMPT_CHAIN = [
    "Make the video black and white.",
    "Now, speed the footage up by 2x.",
    "Make the video smell like coffee and roses.", # <-- This prompt is designed to fail
    "Crop the video to a square aspect ratio, focusing on the center.", # <-- Test if system can recover
    "Trim the result to the first 4 seconds of its new duration.",
    "Finally, add the text 'E2E ROBUSTNESS TEST' in the center of the video."
]
# Timeout for API requests in seconds (FFmpeg can be slow)
REQUEST_TIMEOUT = 180 

# --- SETUP LOGGER ---
# Ensure log/video paths are relative to the project root, not the script's directory
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOG_FILE_PATH = os.path.join(PROJECT_ROOT, LOG_FILENAME)
TEST_VIDEO_PATH = os.path.join(PROJECT_ROOT, TEST_VIDEO_FILENAME)

# Clears the log file for a fresh run
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

def generate_test_video(filepath: str):
    """Generates a standard test video using ffmpeg."""
    logging.info(f"Checking for ffmpeg...")
    if not shutil.which("ffmpeg"):
        logging.error("`ffmpeg` command not found. Please install ffmpeg and ensure it's in your system's PATH.")
        raise FileNotFoundError("ffmpeg is required to generate the test video.")

    logging.info(f"Generating test video: {filepath}...")
    try:
        command = [
            'ffmpeg',
            '-f', 'lavfi',
            '-i', 'testsrc=duration=20:size=640x480:rate=30',
            '-pix_fmt', 'yuv420p', # Common pixel format for compatibility
            '-y', # Overwrite output file if it exists
            filepath
        ]
        result = subprocess.run(command, check=True, capture_output=True, text=True)
        logging.info("Test video generated successfully.")
    except subprocess.CalledProcessError as e:
        logging.error("Failed to generate test video.")
        logging.error(f"FFmpeg stderr:\n{e.stderr}")
        raise

def cleanup(session_id: str, video_path: str):
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
        else:
            logging.warning(f"Session directory not found for cleanup: {session_path}")

    if os.path.exists(video_path):
        try:
            os.remove(video_path)
            logging.info(f"Removed test video: {video_path}")
        except OSError as e:
            logging.error(f"Error removing test video {video_path}: {e}")


def run_test_flow() -> bool:
    """Executes the full end-to-end test and returns True if all steps passed."""
    
    # 1. UPLOAD
    logging.info("--- STEP 1: UPLOADING VIDEO ---")
    try:
        with open(TEST_VIDEO_PATH, 'rb') as f:
            response = requests.post(f"{BASE_URL}/upload", files={'file': f}, timeout=REQUEST_TIMEOUT)
        
        response.raise_for_status() # Raise an exception for bad status codes (4xx or 5xx)
        
        response_data = response.json()
        session_id = response_data.get("session_id")
        if not session_id:
            raise ValueError("Response did not contain a session_id.")
        
        logging.info(f"Upload successful. Session ID: {session_id}")
    except Exception as e:
        logging.error(f"FATAL: Could not upload video. Test cannot continue. Error: {e}")
        return False, None # Return False for success status and None for session_id

    # 2. EDITING CHAIN
    all_steps_succeeded = True
    for i, prompt in enumerate(PROMPT_CHAIN):
        step_num = i + 2
        logging.info(f"--- STEP {step_num}: SENDING PROMPT '{prompt}' ---")
        
        payload = {"session_id": session_id, "prompt": prompt}
        
        try:
            response = requests.post(f"{BASE_URL}/edit", json=payload, timeout=REQUEST_TIMEOUT)
            
            # Check for non-200 status codes, which indicate server-level errors
            if response.status_code != 200:
                logging.error(f"Step {step_num} FAILED with HTTP Status Code: {response.status_code}")
                logging.error(f"Response Body: {response.text}")
                all_steps_succeeded = False
                continue # Move to the next prompt

            response_data = response.json()
            # Check for application-level errors reported in the JSON body
            if response_data.get("status") != "success":
                logging.warning(f"Step {step_num} FAILED. API reported an error: {response_data.get('error', 'Unknown error')}")
                all_steps_succeeded = False
                continue # Move to the next prompt

            logging.info(f"Step {step_num} SUCCEEDED. Response: {response_data}")
            time.sleep(1) # Small delay between requests

        except requests.exceptions.RequestException as e:
            logging.error(f"Step {step_num} FAILED due to a network or timeout error: {e}")
            all_steps_succeeded = False
            continue # Move to the next prompt

    return all_steps_succeeded, session_id


if __name__ == "__main__":
    session_id_to_clean = None
    test_passed = False
    try:
        # Check if the server is running
        try:
            requests.get(BASE_URL, timeout=3)
        except requests.ConnectionError:
            logging.error(f"Could not connect to the server at {BASE_URL}.")
            logging.error("Please ensure the FastAPI server is running before starting the test.")
            exit(1)

        generate_test_video(TEST_VIDEO_PATH)
        test_passed, session_id_to_clean = run_test_flow()
        
        logging.info("======================================")
        if test_passed:
            logging.info("✅ ROBUSTNESS TEST COMPLETED: All steps passed successfully.")
        else:
            logging.warning("⚠️ ROBUSTNESS TEST COMPLETED: One or more steps failed. See log for details.")
        logging.info("======================================")
        
    except Exception as e:
        logging.error("=======================================")
        logging.error(f"❌ ROBUSTNESS TEST FAILED due to an unhandled exception: {e}")
        logging.error("=======================================")
        
    finally:
        cleanup(session_id_to_clean, TEST_VIDEO_PATH)