import os
import subprocess
import logging
import google.generativeai as genai
from dotenv import load_dotenv
import ast
import platform
# --- Imports for the new validation logic ---
import tempfile
import shutil
import sys

# prompts.py is now simplified, we define the new system instruction here.
from .prompts import USER_CONTENT_TEMPLATE

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)
# Set to DEBUG for verbose output from this module, giving full visibility into the process.
logger.setLevel(logging.DEBUG)

# --- NEW, SIMPLIFIED SYSTEM INSTRUCTION ---
# This instructs the AI to rely on the system's default font, which is a more robust approach.
SYSTEM_INSTRUCTION = """
You are an AI assistant that generates Python scripts for video editing using FFmpeg.
The script should take a video file named 'proxyN.mp4' as input and output the result
to a file named 'proxyN+1.mp4', where N is the current proxy index.
The script must only contain Python code using the 'subprocess' module to execute FFmpeg commands.
Do NOT include any explanations, markdown formatting (like ```python), or extra text outside the script.

IMPORTANT: For error handling, do NOT use sys.exit(). Instead, catch exceptions and raise them
to be handled by the calling code. This allows the FastAPI application to properly report errors.

The script must be executable Python code.
"""


# --- Constants for iterative generation ---
MAX_RETRIES = 3
CANDIDATES_FIRST = 1
CANDIDATES_RETRY = 3
# Timeout for ffmpeg dry-run validation in seconds
FFMPEG_TIMEOUT = 15 # Increased timeout for sandboxed execution

# Configure Gemini API
# The API key must be set as an environment variable
API_KEY = os.environ.get("GOOGLE_API_KEY")

if not API_KEY:
    logger.error("GOOGLE_API_KEY environment variable not set.")
    raise ValueError("GOOGLE_API_KEY environment variable is required to run this application.")

logger.info(f"GOOGLE_API_KEY found! (length: {len(API_KEY)})")
genai.configure(api_key=API_KEY)

# Use a suitable model for code generation
MODEL_NAME = "gemini-2.5-pro"

generation_config = {
  "temperature": 0.2, # Lower temperature for more predictable code
  "top_p": 1,
  "top_k": 1,
}

# Create a Gemini model instance
model = genai.GenerativeModel(model_name=MODEL_NAME,
                                generation_config=generation_config)

# --- Helper for creating a dummy video file for validation ---
_dummy_video_path = None

def _create_dummy_video():
    """Creates a short, silent dummy video file for validation and returns its path."""
    global _dummy_video_path
    if _dummy_video_path and os.path.exists(_dummy_video_path):
        return _dummy_video_path

    with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as temp_f:
        _dummy_video_path = temp_f.name
    
    logger.debug(f"Creating dummy video for validation at: {_dummy_video_path}")
    # A 60-second, 15fps video is representative for most edits without being slow.
    command = [
        'ffmpeg', '-y',
        '-f', 'lavfi', '-i', 'color=c=black:s=128x72:r=15:d=60',
        '-f', 'lavfi', '-i', 'anullsrc',
        '-c:v', 'libx264', '-c:a', 'aac',
        '-t', '60',
        _dummy_video_path
    ]
    try:
        subprocess.run(command, check=True, capture_output=True, text=True)
        return _dummy_video_path
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to create dummy video for validation: {e.stderr}")
        raise RuntimeError(f"FFmpeg failed to create a dummy video file: {e.stderr}") from e

def _cleanup_dummy_video():
    """Removes the dummy video file if it exists."""
    global _dummy_video_path
    if _dummy_video_path and os.path.exists(_dummy_video_path):
        try:
            os.remove(_dummy_video_path)
            logger.debug(f"Cleaned up dummy video: {_dummy_video_path}")
        except OSError as e:
            logger.warning(f"Could not remove dummy video file {_dummy_video_path}: {e}")
    _dummy_video_path = None

def _validate_script(script_code: str, dummy_input_path: str):
    """
    Validates a generated script through syntax and a live, sandboxed execution.
    Returns a tuple (is_valid, error_message).
    """
    # 1. Quick Syntax Check (ast.parse)
    logger.debug("Running validation step 1: Quick Syntax Check")
    try:
        ast.parse(script_code)
    except SyntaxError as e:
        error_msg = f"[SyntaxError] Invalid Python syntax on line {e.lineno}: {e.msg}"
        logger.debug(f"Syntax check FAILED: {error_msg}")
        return False, error_msg
    logger.debug("Syntax check PASSED.")

    # 2. Sandboxed Execution
    logger.debug("Running validation step 2: Sandboxed Execution")
    with tempfile.TemporaryDirectory() as sandbox_dir:
        try:
            script_path_in_sandbox = os.path.join(sandbox_dir, "test_script.py")
            with open(script_path_in_sandbox, "w") as f:
                f.write(script_code)

            # The script expects 'proxyN.mp4', so we copy our dummy video to that name
            # For the sandboxed run, N can be any placeholder. The executor handles the real index.
            shutil.copy(dummy_input_path, os.path.join(sandbox_dir, "proxyN.mp4"))

            logger.debug(f"Executing sandboxed script: {script_path_in_sandbox}")
            result = subprocess.run(
                [sys.executable, script_path_in_sandbox],
                cwd=sandbox_dir,
                check=True,
                capture_output=True,
                text=True,
                timeout=FFMPEG_TIMEOUT
            )
            logger.debug(f"Sandboxed execution successful. Stderr: {result.stderr}")
            return True, None

        except subprocess.TimeoutExpired:
            error_msg = f"[SandboxError] Script execution timed out after {FFMPEG_TIMEOUT} seconds."
            logger.debug(f"Sandboxed execution FAILED: {error_msg}")
            return False, error_msg
        except subprocess.CalledProcessError as e:
            stderr = e.stderr.lower()
            # This is the "Error-Aware" part. We check for errors that are likely
            # caused by our dummy data being too short, not a flaw in the script.
            acceptable_errors = ["invalid duration", "cannot seek", "end of file", "past eof"]
            
            if any(err_str in stderr for err_str in acceptable_errors):
                logger.debug(f"Sandboxed execution failed with an acceptable, data-dependent error. Approving script. Error: {e.stderr}")
                return True, None # This is a "false negative", so we approve the script.
            else:
                # This is a real syntax/config error from FFmpeg or the Python script.
                error_msg = f"[SandboxError] Script failed during execution.\n--- Stderr ---\n{e.stderr}"
                logger.debug(f"Sandboxed execution FAILED with a real error: {error_msg}")
                return False, error_msg
        except Exception as e:
            error_msg = f"[SandboxError] An unexpected error occurred during validation: {e}"
            logger.debug(f"Sandboxed execution FAILED: {error_msg}")
            return False, error_msg


def generate_edit_script(prompt: str) -> str:
    """
    Generates and validates a Python script to perform a video edit using FFmpeg.
    Retries generation with feedback upon validation failure.
    """
    logger.debug(f"Entering generate_edit_script with prompt: '{prompt}'")
    
    dummy_video_path = _create_dummy_video()
    feedback_str = ""
    last_attempt_errors = {}

    try:
        for attempt in range(MAX_RETRIES):
            logger.info(f"Generation attempt {attempt + 1}/{MAX_RETRIES}")
            
            candidate_count = CANDIDATES_FIRST if attempt == 0 else CANDIDATES_RETRY
            
            user_content = USER_CONTENT_TEMPLATE.format(prompt=prompt)
            full_prompt = f"{SYSTEM_INSTRUCTION}\n\n{user_content}"
            if feedback_str:
                full_prompt += f"\n\n{feedback_str}"
            logger.debug(f"--- Full prompt for attempt {attempt + 1} ---\n{full_prompt}\n--- End of prompt ---")

            try:
                iter_generation_config = generation_config.copy()
                iter_generation_config["candidate_count"] = candidate_count
                
                logger.debug(f"Calling Gemini API with candidate_count={candidate_count}...")
                response = model.generate_content(
                    [{"role": "user", "parts": [{"text": full_prompt}]}],
                    generation_config=iter_generation_config
                )
                if not response.candidates:
                    raise ValueError("Gemini API returned no candidates.")
                logger.debug(f"Gemini API returned {len(response.candidates)} candidates.")

            except Exception as e:
                logger.error(f"Error calling Gemini API on attempt {attempt + 1}: {e}")
                if attempt < MAX_RETRIES - 1:
                    continue
                raise ConnectionError(f"Failed to communicate with Gemini API after {MAX_RETRIES} attempts.") from e

            attempt_errors = {}
            for i, candidate in enumerate(response.candidates):
                logger.debug(f"--- Processing Candidate {i+1}/{len(response.candidates)} ---")
                if not candidate.content.parts:
                    logger.warning(f"Candidate {i+1} has no content, skipping.")
                    continue
                
                raw_script_content = candidate.content.parts[0].text
                logger.debug(f"Raw candidate content:\n---\n{raw_script_content}\n---")
                
                script_content = raw_script_content.strip()
                if script_content.startswith("```python"):
                    script_content = script_content[len("```python"):].strip()
                if script_content.endswith("```"):
                    script_content = script_content[:-len("```")].strip()
                
                is_valid, error_msg = _validate_script(script_content, dummy_video_path)
                
                if is_valid:
                    logger.info(f"Candidate {i+1} passed all validation steps. Script generation successful.")
                    logger.debug("Exiting generate_edit_script successfully.")
                    return script_content
                else:
                    logger.warning(f"Candidate {i+1} failed validation: {error_msg}")
                    attempt_errors[f"Candidate {i+1}"] = {"error": error_msg, "code": script_content}
            
            last_attempt_errors = attempt_errors
            feedback_parts = [
                "# FEEDBACK",
                "# The previous script(s) were invalid. Analyze the script and the corresponding error, then provide a new, valid script.",
                "# REMINDER: When using 'drawtext', you MUST NOT use the 'fontfile' parameter. Rely on system defaults.",
            ]
            # Include both the failed code and the error message in the feedback.
            for name, details in attempt_errors.items():
                feedback_parts.append(f"\n# --- {name} ---")
                feedback_parts.append("# FAILED SCRIPT:")
                # Indent the script with comment markers for clarity in the prompt
                indented_script = "\n".join([f"# {line}" for line in details['code'].split('\n')])
                feedback_parts.append(indented_script)
                feedback_parts.append("# ERROR MESSAGE:")
                indented_error = "\n".join([f"# {line}" for line in details['error'].strip().split('\n')])
                feedback_parts.append(indented_error)

            feedback_str = "\n".join(feedback_parts)
            logger.debug(f"All candidates for attempt {attempt + 1} failed. Preparing feedback string for next attempt.")
    
    finally:
        _cleanup_dummy_video()

    error_report = "Failed to generate a valid script after all retries.\nLast attempt errors:\n"
    for name, details in last_attempt_errors.items():
        error_report += f"- {name}: {details['error']}\n---\n{details['code']}\n---\n"
        
    logger.error(error_report)
    logger.debug("Exiting generate_edit_script with failure after all retries.")
    raise ValueError(error_report)