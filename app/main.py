# app/main.py

import fastapi
import uvicorn
import os
import json
import uuid
import logging
import shutil
from fastapi import UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import Optional

# Local imports - these will be created/refactored in subsequent steps
from . import orchestrator
from .logging_config import setup_run_logger
from .video_io import save_uploaded_file
from .models import EditRequest, UndoRequest

# --- Pydantic Models ---
# It's good practice to keep these in `models.py`, but for completeness they are here.
class SessionSettings(BaseModel):
    width: int = 1920
    height: int = 1080
    fps: int = 30
    duration: float = 10.0 # Default duration for a new project

# --- Application Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = fastapi.FastAPI()
SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)
app.mount("/static", StaticFiles(directory=SESSIONS_DIR), name="static")

# --- API Endpoints ---

@app.post("/sessions", status_code=201)
async def create_session(settings: SessionSettings):
    """
    Creates a new, blank editing session and its initial SWML file.
    This is the new starting point for any project.
    """
    session_id = str(uuid.uuid4())
    session_path = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(session_path, exist_ok=True)
    logger.info(f"Creating new session: {session_id}")

    # Create the initial, blank SWML file (comp0.swml)
    initial_swml = {
        "composition": {
            "width": settings.width,
            "height": settings.height,
            "fps": settings.fps,
            "duration": settings.duration,
            "output_format": "mp4"
        },
        "sources": [], # Starts with no sources
        "tracks": [],  # Starts with no tracks
    }
    with open(os.path.join(session_path, "comp0.swml"), "w") as f:
        json.dump(initial_swml, f, indent=2)

    # Create the initial history file
    history = {
        "current_index": 0,
        "history": [
            {
                "index": 0,
                "prompt": "Initial project creation",
                "swml_file": "comp0.swml",
            }
        ],
    }
    with open(os.path.join(session_path, "history.json"), "w") as f:
        json.dump(history, f, indent=2)

    return {"session_id": session_id, "message": "New session created successfully."}


@app.post("/sessions/{session_id}/assets")
async def add_asset_to_session(session_id: str, file: UploadFile):
    """
    Uploads a media file to a session and registers it as a source
    in the latest SWML file.
    """
    session_path = os.path.join(SESSIONS_DIR, session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")

    try:
        saved_filepath = save_uploaded_file(file, session_path)
        filename = os.path.basename(saved_filepath)
        logger.info(f"Saved asset '{filename}' to session '{session_id}'")
    except Exception as e:
        logger.error(f"Failed to save file for session {session_id}: {e}")
        raise fastapi.HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    # Update the latest SWML to include the new source
    history_path = os.path.join(session_path, "history.json")
    with open(history_path, "r+") as f:
        history = json.load(f)
        latest_swml_filename = history["history"][history["current_index"]]["swml_file"]
        latest_swml_path = os.path.join(session_path, latest_swml_filename)

        with open(latest_swml_path, "r+") as swml_f:
            swml_data = json.load(swml_f)
            source_id = os.path.splitext(filename)[0].lower().replace(" ", "_").replace("-", "_")
            
            # Check for duplicate source IDs
            if any(src['id'] == source_id for src in swml_data['sources']):
                source_id = f"{source_id}_{uuid.uuid4().hex[:4]}"

            swml_data["sources"].append({"id": source_id, "path": filename})
            
            swml_f.seek(0)
            json.dump(swml_data, f, indent=2)
            swml_f.truncate()

    return {"session_id": session_id, "asset_id": source_id, "filename": filename}


@app.post("/edit")
async def edit_video(request: EditRequest):
    """
    Initiates an edit operation based on a user prompt. This can be
    based on the latest version or any previous version in history.
    """
    session_path = os.path.join(SESSIONS_DIR, request.session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")

    history_path = os.path.join(session_path, "history.json")
    with open(history_path, "r") as f:
        history = json.load(f)

    base_index = request.base_index if request.base_index is not None else history["current_index"]
    if base_index < 0 or base_index >= len(history["history"]):
        raise fastapi.HTTPException(status_code=400, detail=f"Invalid base_index: {base_index}")
    
    if base_index < history["current_index"]:
        logger.info(f"Time-travel edit for session {request.session_id}. Pruning history from index {base_index + 1}.")
        history["history"] = history["history"][:base_index + 1]
    
    current_index = base_index
    new_index = current_index + 1

    log_filename = f"run_edit_{new_index}.log"
    log_filepath = os.path.join(session_path, log_filename)
    run_logger = setup_run_logger(f"run-{request.session_id}-{new_index}", log_filepath)

    run_logger.info("="*80)
    run_logger.info(f"STARTING EDIT RUN {new_index} for Session {request.session_id} (Base: {current_index})")
    run_logger.info(f"User Prompt: '{request.prompt}'")
    run_logger.info("="*80)
    
    current_swml_filename = history["history"][current_index]["swml_file"]
    current_swml_path = os.path.join(session_path, current_swml_filename)
    
    try:
        result_log = orchestrator.process_edit_request(
            session_path=session_path,
            prompt=request.prompt,
            current_swml_path=current_swml_path,
            new_index=new_index,
            run_logger=run_logger
        )
        run_logger.info("="*80 + "\nEDIT RUN SUCCEEDED\n" + "="*80)
    except Exception as e:
        run_logger.error("="*80 + f"\nEDIT RUN FAILED: {e}\n" + "="*80, exc_info=True)
        logger.error(f"Edit failed for session {request.session_id}: {e}")
        return JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e), "log_file": log_filename}
        )
    
    history_entry = {
        "index": new_index,
        "prompt": request.prompt,
        "swml_file": result_log["output_swml"],
        "video_file": result_log["output_video"],
        "log_file": log_filename
    }
    history["history"].append(history_entry)
    history["current_index"] = new_index

    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)

    preview_symlink = os.path.join(session_path, "preview.mp4")
    # Check if a symlink exists, not if it's a file or dir
    if os.path.islink(preview_symlink) or os.path.exists(preview_symlink):
        os.remove(preview_symlink)
    os.symlink(result_log["output_video"], preview_symlink)

    return {
        "status": "success",
        "new_history": history,
        "output_url": f"/static/{request.session_id}/preview.mp4",
        "log_file": log_filename
    }

# The /undo endpoint is now much simpler, just a state change.
@app.post("/undo")
async def undo_edit(request: UndoRequest):
    # This endpoint could be removed in favor of just using /edit with a base_index,
    # but it's a convenient shortcut for the UI.
    # Its implementation would be similar to the previous version.
    pass # Implementation left as an exercise

@app.get("/static/{session_id}/{filename}")
async def get_session_file(session_id: str, filename: str):
    file_path = os.path.join(SESSIONS_DIR, session_id, filename)
    if not os.path.exists(file_path):
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return FileResponse(file_path)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)