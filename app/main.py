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
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware

# --- Local imports ---
from . import orchestrator
from .logging_config import setup_run_logger
from .video_io import save_uploaded_file
from .models import EditRequest, UndoRequest, SessionSettings

# --- Application Setup ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = fastapi.FastAPI()
SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

# --- CORS Configuration ---
origins = [
    "http://localhost",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Mount static files directory AFTER app and middleware are configured
app.mount("/static", StaticFiles(directory=SESSIONS_DIR), name="static")


# --- API Endpoints ---

@app.post("/sessions", status_code=201)
async def create_session(settings: SessionSettings):
    """Creates a new, blank editing session and its initial SWML file."""
    session_id = str(uuid.uuid4())
    session_path = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(session_path, exist_ok=True)
    logger.info(f"Creating new session: {session_id}")

    initial_swml = {
        "composition": {
            "width": settings.width,
            "height": settings.height,
            "fps": settings.fps,
            "duration": settings.duration,
            "output_format": "mp4"
        },
        "sources": [],
        "tracks": [],
    }
    with open(os.path.join(session_path, "comp0.swml"), "w") as f:
        json.dump(initial_swml, f, indent=2)

    history = {
        "current_index": 0,
        "history": [{
            "index": 0,
            "prompt": "Initial project creation",
            "swml_file": "comp0.swml",
            "video_file": None,
            "log_file": None
        }],
    }
    with open(os.path.join(session_path, "history.json"), "w") as f:
        json.dump(history, f, indent=2)

    return {"session_id": session_id, "message": "New session created successfully."}


@app.post("/sessions/{session_id}/assets")
async def add_asset_to_session(session_id: str, file: UploadFile):
    """Uploads a media file and registers it as a source in the latest SWML."""
    session_path = os.path.join(SESSIONS_DIR, session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")

    try:
        saved_filepath = save_uploaded_file(file, session_path)
        filename = os.path.basename(saved_filepath)
    except Exception as e:
        logger.error(f"Failed to save file for session {session_id}: {e}")
        raise fastapi.HTTPException(status_code=500, detail=f"Failed to save file: {e}")

    history_path = os.path.join(session_path, "history.json")
    with open(history_path, "r") as f:
        history = json.load(f)
        
    latest_swml_path = os.path.join(session_path, history["history"][history["current_index"]]["swml_file"])
    with open(latest_swml_path, "r") as f:
        swml_data = json.load(f)

    source_id = os.path.splitext(filename)[0].lower().replace(" ", "_").replace("-", "_")
    original_source_id = source_id
    while any(src['id'] == source_id for src in swml_data['sources']):
        source_id = f"{original_source_id}_{uuid.uuid4().hex[:4]}"
    swml_data["sources"].append({"id": source_id, "path": filename})

    new_index = history["current_index"] + 1
    new_swml_filename = f"comp{new_index}.swml"
    new_swml_filepath = os.path.join(session_path, new_swml_filename)
    with open(new_swml_filepath, "w") as f:
        json.dump(swml_data, f, indent=2)
    
    history_entry = {
        "index": new_index,
        "prompt": f"Added asset: {filename}",
        "swml_file": new_swml_filename,
        "video_file": history["history"][history["current_index"]].get("video_file"),
        "log_file": None
    }
    history["history"].append(history_entry)
    history["current_index"] = new_index
    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)

    logger.info(f"Added asset '{filename}' (ID: {source_id}) to session '{session_id}', creating new state {new_index}")
    return {"session_id": session_id, "asset_id": source_id, "filename": filename, "new_history": history}


@app.post("/edit")
async def edit_video(request: EditRequest):
    """Initiates an edit operation based on a user prompt."""
    session_path = os.path.join(SESSIONS_DIR, request.session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")

    with open(os.path.join(session_path, "history.json"), "r") as f:
        history = json.load(f)

    base_index = request.base_index if request.base_index is not None else history["current_index"]
    if not (0 <= base_index < len(history["history"])):
        raise fastapi.HTTPException(status_code=400, detail=f"Invalid base_index: {base_index}")
    
    if base_index < history["current_index"]:
        logger.info(f"Time-travel edit for session {request.session_id}. Pruning from index {base_index + 1}.")
        history["history"] = history["history"][:base_index + 1]
    
    current_index = base_index
    new_index = current_index + 1

    log_filename = f"run_edit_{new_index}.log"
    log_filepath = os.path.join(session_path, log_filename)
    run_logger = setup_run_logger(f"run-{request.session_id}-{new_index}", log_filepath)

    run_logger.info("="*80 + f"\nSTARTING EDIT RUN {new_index} (Base: {current_index})\nUser Prompt: '{request.prompt}'\n" + "="*80)
    
    current_swml_path = os.path.join(session_path, history["history"][current_index]["swml_file"])
    
    prompt_history = [item["prompt"] for item in history["history"][:current_index + 1] if item.get("prompt")]
    
    try:
        result_log = orchestrator.process_edit_request(
            session_path=session_path,
            prompt=request.prompt,
            current_swml_path=current_swml_path,
            new_index=new_index,
            prompt_history=prompt_history,
            run_logger=run_logger,
            preview=request.preview
        )
        run_logger.info("="*80 + "\nEDIT RUN SUCCEEDED\n" + "="*80)
    except Exception as e:
        run_logger.error("="*80 + f"\nEDIT RUN FAILED: {e}\n" + "="*80, exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "error": str(e), "log_file": log_filename})
    
    history_entry = {
        "index": new_index,
        "prompt": request.prompt,
        "swml_file": result_log["output_swml"],
        "video_file": result_log["output_video"],
        "log_file": log_filename
    }
    history["history"].append(history_entry)
    history["current_index"] = new_index

    with open(os.path.join(session_path, "history.json"), "w") as f:
        json.dump(history, f, indent=2)

    preview_symlink = os.path.join(session_path, "preview.mp4")
    if os.path.islink(preview_symlink) or os.path.exists(preview_symlink):
        os.remove(preview_symlink)
    os.symlink(result_log["output_video"], preview_symlink)

    return {
        "status": "success",
        "new_history": history,
        "output_url": f"/static/{request.session_id}/preview.mp4",
        "log_file": log_filename
    }


@app.get("/static/{session_id}/{filename}")
async def get_session_file(session_id: str, filename: str):
    file_path = os.path.join(SESSIONS_DIR, session_id, filename)
    if not os.path.exists(file_path):
        return JSONResponse(status_code=404, content={"error": "File not found"})
    return FileResponse(file_path)


# This block allows running the app for development with `python3 -m app.main`
# but the recommended way is `uvicorn app.main:app --reload --env-file .env`
if __name__ == "__main__":
    # NOTE: This development server will not have auto-reloading for .env files.
    # For that, use the uvicorn command directly.
    from dotenv import load_dotenv
    load_dotenv()
    
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)