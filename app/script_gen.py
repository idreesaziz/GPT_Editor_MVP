# --- START OF FILE script_gen.py ---
import os
import subprocess
import logging
import google.generativeai as genai
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

logger = logging.getLogger(__name__)

# Configure Gemini API
# The API key must be set as an environment variable
API_KEY = os.environ.get("GOOGLE_API_KEY")

if not API_KEY:
    logger.error("GOOGLE_API_KEY environment variable not set.")
    raise ValueError("GOOGLE_API_KEY environment variable is required to run this application.")

logger.info(f"GOOGLE_API_KEY found! (length: {len(API_KEY)})")
genai.configure(api_key=API_KEY)

# Choose a model
# You can list available models and their capabilities
# for model in genai.list_models():
#     print(model.name)
#     print(model.supported_generation_methods)
# Use gemini-1.5-pro or gemini-2.5-pro if available and appropriate for generating code
MODEL_NAME = "gemini-2.5-pro" # Or "gemini-2.5-pro" if available and preferred

generation_config = {
  "temperature": 0.7, # Adjust for creativity vs. consistency
  "top_p": 1,
  "top_k": 1,
}

# Create a Gemini model instance
model = genai.GenerativeModel(model_name=MODEL_NAME,
                                generation_config=generation_config)


def generate_edit_script(prompt: str) -> str:
    """
    Generates a Python script to perform a video edit using FFmpeg based on a text prompt.
    The script uses subprocess to call FFmpeg.
    It expects input as 'proxyN.mp4' and outputs to 'proxyN+1.mp4'.
    """
    logger.info(f"Generating script for prompt: {prompt}")

    # Define the prompt for the generative model
    # We instruct the model to generate Python code that calls FFmpeg
    # Ensure the output format is just the Python script.
    system_instruction = """
    You are an AI assistant that generates Python scripts for video editing using FFmpeg.
    The script should take a video file named 'proxyN.mp4' as input and output the result
    to a file named 'proxyN+1.mp4', where N is the current proxy index.
    The script must only contain Python code using the 'subprocess' module to execute FFmpeg commands.
    Do NOT include any explanations, markdown formatting (like ```python), or extra text outside the script.
    
    IMPORTANT: For error handling, do NOT use sys.exit(). Instead, catch exceptions and raise them
    to be handled by the calling code. This allows the FastAPI application to properly report errors.
    
    The script must be executable Python code.
    """

    user_content = f"""
    Generate a Python script using 'subprocess' to execute an FFmpeg command for video editing.
    Input file: 'proxyN.mp4'
    Output file: 'proxyN+1.mp4'
    Perform the following edit based on the user's request:
    '{prompt}'

    The script should start with:
    import subprocess
    import os

    # Define input and output files
    input_file = 'proxyN.mp4'
    output_file = 'proxyN+1.mp4'

    # Your ffmpeg command list definition here...
    # Example: command = ["ffmpeg", "-i", input_file, ...]

    # Your subprocess.run call here with proper error handling...
    # IMPORTANT: Do NOT use sys.exit() in error handling. Instead, raise exceptions
    # to be handled by the calling code.
    """

    try:
        response = model.generate_content(
            [
                {"role": "user", "parts": [{"text": f"{system_instruction}\n\n{user_content}"}]}
            ]
        )

        # Extract the generated text, removing potential markdown formatting if any slipped through
        script_content = response.text.strip()
        # Clean up potential markdown fence if the model ignored the system instruction
        if script_content.startswith("```python"):
            script_content = script_content[len("```python"):].strip()
        if script_content.endswith("```"):
            script_content = script_content[:-len("```")].strip()

        logger.info("Script generation successful.")
        # The main.py will handle replacing 'proxyN.mp4' and 'proxyN+1.mp4' with
        # the actual index numbers before writing the script to a file.
        # So the generated script should contain these literal strings.
        return script_content

    except Exception as e:
        logger.error(f"Error generating script with Gemini API: {e}")
        # Don't silently fall back - re-raise the exception
        raise

# --- END OF FILE script_gen.py ---