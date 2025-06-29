"""
Prompts for generating video editing scripts.
These are used by script_gen.py to instruct the LLM.
"""

# System instruction for the Gemini model
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

# User content template for the Gemini model
USER_CONTENT_TEMPLATE = """
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
