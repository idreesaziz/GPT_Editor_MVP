# app/main.py

import fastapi
import uvicorn
import os
import json
import uuid
import logging
import shutil
import asyncio
from concurrent.futures import ThreadPoolExecutor
from fastapi import UploadFile, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from typing import Optional, Dict, Any
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

# Thread pool for running CPU-intensive tasks
executor = ThreadPoolExecutor(max_workers=4)  # Limit concurrent edit operations

# --- State Management for Background Tasks (In-memory, non-production) ---
# This dictionary will store the real-time status of any ongoing edit operations.
# In a production environment, this should be replaced with a more robust
# key-value store like Redis.
session_status: Dict[str, Dict[str, Any]] = {}

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


# --- Session Status Management ---
def set_session_status(session_id: str, status: str, current_phase: str = None, edit_index: int = None, details: Dict[str, Any] = None):
    """Update the status of a session with detailed phase information."""
    session_status[session_id] = {
        "status": status,
        "current_phase": current_phase,
        "edit_index": edit_index,
        "details": details or {},
        "timestamp": None  # Could add timestamp if needed
    }
    logger.debug(f"Session {session_id} status updated to: {status} (phase: {current_phase}, details: {details})")

def get_session_status(session_id: str) -> dict:
    """Get the current status of a session."""
    return session_status.get(session_id, {"status": "ready", "current_phase": None, "edit_index": None, "details": {}})

def clear_session_status(session_id: str):
    """Clear the status of a session (set it back to ready)."""
    session_status.pop(session_id, None)
    logger.debug(f"Session {session_id} status cleared (set to ready)")


# --- API Endpoints ---

@app.post("/sessions", status_code=201)
async def create_session(settings: SessionSettings):
    """Creates a new, blank editing session and its initial SWML file."""
    session_id = str(uuid.uuid4())
    session_path = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(session_path, exist_ok=True)
    
    # Create the dedicated directory for generated assets
    os.makedirs(os.path.join(session_path, "assets"), exist_ok=True)

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
        # User-uploaded files are still saved in the session root
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
        
    # The path for uploaded assets is just the filename (relative to session root)
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


@app.get("/sessions/{session_id}/status")
async def get_session_edit_status(session_id: str):
    """
    Get the current edit status for a session.
    
    Returns:
        - status: "ready" (no edit in progress) or "processing" (edit in progress)
        - current_phase: Current phase if processing (planning, generating_[task_name], composing, rendering)
        - edit_index: Index of edit being processed (if processing)
        - current_history_index: Current index in session history
        - total_edits: Total number of edits completed in session
        - details: Additional phase-specific information
    """
    session_path = os.path.join(SESSIONS_DIR, session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")
    
    status_info = get_session_status(session_id)
    
    # Add additional context about the session
    try:
        with open(os.path.join(session_path, "history.json"), "r") as f:
            history = json.load(f)
        
        response = {
            "session_id": session_id,
            "status": status_info["status"],
            "current_phase": status_info["current_phase"],
            "edit_index": status_info["edit_index"],
            "current_history_index": history["current_index"],
            "total_edits": len(history["history"]) - 1,  # Subtract 1 for initial state
            "is_ready": status_info["status"] == "ready",
            "details": status_info.get("details", {})
        }
        
        # If currently processing an edit, add more details
        if status_info["status"] == "processing":
            response["processing_edit_index"] = status_info["edit_index"]
            
            # Enhanced phase descriptions
            phase_descriptions = {
                "starting": "Initializing edit process",
                "planning": "Analyzing request and creating execution plan",
                "composing": "Applying edits to timeline",
                "rendering": "Rendering final video output"
            }
            
            # Handle dynamic generation task phases
            current_phase = status_info["current_phase"]
            if current_phase and current_phase.startswith("generating_"):
                task_name = current_phase.replace("generating_", "").replace("_", " ").title()
                phase_descriptions[current_phase] = f"Generating {task_name}"
            
            response["phase_description"] = phase_descriptions.get(
                current_phase, 
                f"Processing: {current_phase}"
            )
            
        return response
        
    except Exception as e:
        logger.error(f"Error reading session history for status check: {e}")
        # Return basic status even if we can't read history
        return {
            "session_id": session_id,
            "status": status_info["status"],
            "current_phase": status_info["current_phase"],
            "edit_index": status_info["edit_index"],
            "is_ready": status_info["status"] == "ready",
            "details": status_info.get("details", {}),
            "error": "Could not read session history"
        }


async def run_edit_sync(
    session_id: str,
    session_path: str,
    prompt: str,
    current_swml_path: str,
    new_index: int,
    prompt_history: list,
    run_logger: logging.Logger,
    preview: bool = False
):
    """
    Synchronous wrapper for the orchestrator that runs in a thread pool.
    This function handles the complete edit workflow and updates session status.
    """
    
    # Define callback for detailed status updates
    def update_status(payload: dict):
        phase = payload.get("phase", "unknown")
        status_type = payload.get("status", "in_progress")
        message = payload.get("message", "")
        details = payload.get("details", {})
        
        # Map orchestrator phases to user-friendly phase names
        phase_mapping = {
            "planning": "planning",
            "asset_generation": "generating_assets",
            "composition": "composing", 
            "rendering": "rendering"
        }
        
        mapped_phase = phase_mapping.get(phase, phase)
        
        # For asset generation, include specific task information
        if phase == "asset_generation" and "task_name" in details:
            task_name = details["task_name"].replace(" ", "_").lower()
            mapped_phase = f"generating_{task_name}"
        
        set_session_status(session_id, "processing", mapped_phase, new_index, details)
    
    try:
        # Run the orchestrator process
        result_report = await asyncio.get_event_loop().run_in_executor(
            executor,
            orchestrator.process_edit_request,
            session_path,
            prompt,
            current_swml_path,
            new_index,
            prompt_history,
            run_logger,
            preview,
            update_status
        )
        
        if result_report["status"] == "success":
            run_logger.info("="*80 + "\nEDIT RUN SUCCEEDED\n" + "="*80)
            
            # Extract output filenames from report
            output_video_path = result_report["final_outputs"]["video_path"]
            output_swml_path = result_report["final_outputs"]["swml_path"]
            output_video_filename = os.path.basename(output_video_path) if output_video_path else None
            output_swml_filename = os.path.basename(output_swml_path) if output_swml_path else None
            
            if output_video_filename and output_swml_filename:
                # Update history
                with open(os.path.join(session_path, "history.json"), "r") as f:
                    history = json.load(f)
                
                log_filename = f"run_edit_{new_index}.log"
                history_entry = {
                    "index": new_index,
                    "prompt": prompt,
                    "swml_file": output_swml_filename,
                    "video_file": output_video_filename,
                    "log_file": log_filename
                }
                history["history"].append(history_entry)
                history["current_index"] = new_index

                with open(os.path.join(session_path, "history.json"), "w") as f:
                    json.dump(history, f, indent=2)

                # Update preview symlink
                preview_symlink = os.path.join(session_path, "preview.mp4")
                if os.path.islink(preview_symlink) or os.path.exists(preview_symlink):
                    os.remove(preview_symlink)
                os.symlink(output_video_filename, preview_symlink)
                
                run_logger.info(f"Edit completed successfully. New video: {output_video_filename}")
            else:
                run_logger.error("Missing output files in successful report")
                
        else:
            run_logger.error("="*80 + "\nEDIT RUN FAILED (reported as failure)\n" + "="*80)
            
    except Exception as e:
        run_logger.error("="*80 + f"\nEDIT RUN FAILED: {e}\n" + "="*80, exc_info=True)
    finally:
        # Always clear session status when done
        clear_session_status(session_id)


@app.post("/edit")
async def edit_video(request: EditRequest, background_tasks: BackgroundTasks):
    """Initiates an edit operation based on a user prompt."""
    session_path = os.path.join(SESSIONS_DIR, request.session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")

    # Check if there's already an edit in progress for this session
    current_status = get_session_status(request.session_id)
    if current_status["status"] == "processing":
        raise fastapi.HTTPException(
            status_code=409, 
            detail="An edit is already in progress for this session. Please wait for it to complete."
        )

    with open(os.path.join(session_path, "history.json"), "r") as f:
        history = json.load(f)

    base_index = request.base_index if request.base_index is not None else history["current_index"]
    if not (0 <= base_index < len(history["history"])):
        raise fastapi.HTTPException(status_code=400, detail=f"Invalid base_index: {base_index}")
    
    if base_index < history["current_index"]:
        logger.info(f"Time-travel edit for session {request.session_id}. Pruning from index {base_index + 1}.")
        history["history"] = history["history"][:base_index + 1]
        # Update history file immediately for time-travel edits
        with open(os.path.join(session_path, "history.json"), "w") as f:
            json.dump(history, f, indent=2)
    
    current_index = base_index
    new_index = current_index + 1

    log_filename = f"run_edit_{new_index}.log"
    log_filepath = os.path.join(session_path, log_filename)
    run_logger = setup_run_logger(f"run-{request.session_id}-{new_index}", log_filepath)

    run_logger.info("="*80 + f"\nSTARTING EDIT RUN {new_index} (Base: {current_index})\nUser Prompt: '{request.prompt}'\n" + "="*80)
    
    current_swml_path = os.path.join(session_path, history["history"][current_index]["swml_file"])
    prompt_history = [item["prompt"] for item in history["history"][:current_index + 1] if item.get("prompt")]
    
    # Set initial session status
    set_session_status(request.session_id, "processing", "starting", new_index)
    
    # Start the edit process in the background
    background_tasks.add_task(
        run_edit_sync,
        request.session_id,
        session_path,
        request.prompt,
        current_swml_path,
        new_index,
        prompt_history,
        run_logger,
        request.preview
    )
    
    # Return immediately with task initiated status
    return {
        "status": "initiated",
        "session_id": request.session_id,
        "edit_index": new_index,
        "message": "Edit process started. Use /sessions/{session_id}/status to poll for progress.",
        "log_file": log_filename
    }


@app.get("/sessions/{session_id}/result")
async def get_edit_result(session_id: str):
    """
    Get the result of the most recent edit operation.
    This endpoint is useful after polling shows the edit is complete.
    """
    session_path = os.path.join(SESSIONS_DIR, session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")
    
    # Check if there's an edit in progress
    current_status = get_session_status(session_id)
    if current_status["status"] == "processing":
        return JSONResponse(status_code=202, content={
            "status": "processing",
            "message": "Edit is still in progress. Please continue polling /status endpoint."
        })
    
    try:
        with open(os.path.join(session_path, "history.json"), "r") as f:
            history = json.load(f)
        
        current_entry = history["history"][history["current_index"]]
        
        return {
            "status": "success",
            "session_id": session_id,
            "current_index": history["current_index"],
            "history": history,
            "output_url": f"/static/{session_id}/preview.mp4",
            "current_video": current_entry.get("video_file"),
            "current_swml": current_entry.get("swml_file"),
            "log_file": current_entry.get("log_file")
        }
        
    except Exception as e:
        logger.error(f"Error reading session result: {e}")
        return JSONResponse(status_code=500, content={
            "status": "error",
            "error": "Could not read session result"
        })


@app.get("/static/{session_id}/{filename:path}")
async def get_session_file(session_id: str, filename: str):
    # This path now correctly handles nested asset directories
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