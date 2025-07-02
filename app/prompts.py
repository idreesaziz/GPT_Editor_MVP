
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