import os
import subprocess
import shutil
from fastapi import UploadFile

def create_proxy(uploaded_video_path: str, session_path: str) -> str:
    proxy0_path = os.path.join(session_path, "proxy0.mp4")
    
    # Create 480p proxy with 15fps
    ffmpeg_command = [
        "ffmpeg",
        "-i",
        uploaded_video_path,
        "-vf",
        "scale='trunc(oh*a/2)*2:480'",
        "-r", "15",  # Set frame rate to 15fps
        proxy0_path
    ]
    subprocess.run(ffmpeg_command, check=True)
    return proxy0_path

def save_uploaded_file(file: UploadFile, session_path: str) -> str:
    uploaded_video_path = os.path.join(session_path, file.filename)
    with open(uploaded_video_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return uploaded_video_path
