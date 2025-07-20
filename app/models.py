# app/models.py

from pydantic import BaseModel
from typing import Optional

# Add this new class
class SessionSettings(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration: float = 10.0 # Default duration for a new project

class EditRequest(BaseModel):
    session_id: str
    prompt: str
    base_index: Optional[int] = None
    preview: bool = False  # Add preview flag for faster, lower quality rendering

class UndoRequest(BaseModel):
    session_id: str
    steps: int = 1