import os
import subprocess
import shutil
from fastapi import UploadFile

def create_proxy(uploaded_video_path: str, session_path: str) -> str:
    """
    Creates a 480p, 15fps proxy video, preserving the original audio track.
    """
    proxy0_path = os.path.join(session_path, "proxy0.mp4")
    
    # Create a 480p proxy at 15fps, re-encoding the original audio to AAC.
    # This ensures the proxy has audio if the original did, preventing errors
    # in audio-related editing steps.
    ffmpeg_command = [
        "ffmpeg",
        "-y",                                 # Overwrite output file if it exists
        "-i", uploaded_video_path,
        "-vf", "scale='trunc(oh*a/2)*2:480'", # Scale video to 480p height
        "-r", "15",                           # Set frame rate to 15fps
        "-c:v", "libx264",                    # Use a common video codec for broad compatibility
        "-c:a", "aac",                        # Re-encode audio to AAC, a common standard
        "-b:a", "128k",                       # Set a reasonable audio bitrate for the proxy
        proxy0_path
    ]

    try:
        subprocess.run(ffmpeg_command, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        # Provide a more detailed error if proxy creation fails
        error_message = (
            f"FFmpeg failed to create proxy for {uploaded_video_path}.\n"
            f"Stderr: {e.stderr}\n"
            f"Stdout: {e.stdout}"
        )
        raise RuntimeError(error_message) from e
        
    return proxy0_path

def save_uploaded_file(file: UploadFile, session_path: str) -> str:
    """
    Saves the uploaded file to the specified session path.
    """
    uploaded_video_path = os.path.join(session_path, file.filename)
    with open(uploaded_video_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return uploaded_video_path