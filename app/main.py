import fastapi
import uvicorn
import os
import json
import uuid
import logging
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .models import EditRequest, UndoRequest
from .video_io import save_uploaded_file, create_proxy
from . import orchestrator # Changed from script_gen
# from .executor import execute_script # No longer needed here

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = fastapi.FastAPI()

SESSIONS_DIR = "sessions"
if not os.path.exists(SESSIONS_DIR):
    os.makedirs(SESSIONS_DIR)

app.mount("/static", StaticFiles(directory=SESSIONS_DIR), name="static")

@app.post("/upload")
async def upload_video(file: fastapi.UploadFile):
    session_id = str(uuid.uuid4())
    session_path = os.path.join(SESSIONS_DIR, session_id)
    os.makedirs(session_path, exist_ok=True)

    uploaded_video_path = save_uploaded_file(file, session_path)
    proxy0_path = create_proxy(uploaded_video_path, session_path)

    history = {
        "current_index": 0,
        "history": [
            {
                "index": 0,
                "prompt": "Initial upload",
                "output": "proxy0.mp4"
            }
        ]
    }
    with open(os.path.join(session_path, "history.json"), "w") as f:
        json.dump(history, f, indent=2)

    preview_symlink = os.path.join(session_path, "preview.mp4")
    if os.path.exists(preview_symlink):
        os.remove(preview_symlink)
    os.symlink("proxy0.mp4", preview_symlink)

    return {"session_id": session_id, "message": "Uploaded and proxy0.mp4 created."}

@app.post("/edit")
async def edit_video(request: EditRequest):
    session_path = os.path.join(SESSIONS_DIR, request.session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")

    history_path = os.path.join(session_path, "history.json")
    with open(history_path, "r") as f:
        history = json.load(f)

    current_index = history["current_index"]
    
    # If editing from an older point in history, prune the future
    if current_index < len(history["history"]) - 1:
        # TODO: Add logic to clean up orphaned proxy files and scripts
        history["history"] = history["history"][:current_index + 1]

    initial_proxy_name = f"proxy{current_index}.mp4"
    
    try:
        # Delegate the entire complex process to the orchestrator
        result_log = orchestrator.process_complex_request(
            session_path=session_path,
            prompt=request.prompt,
            initial_proxy_name=initial_proxy_name
        )
    except Exception as e:
        logger.error(f"Edit failed due to orchestrator error: {e}", exc_info=True)
        return fastapi.responses.JSONResponse(
            status_code=500,
            content={"status": "error", "error": str(e)}
        )
    
    new_index = current_index + 1
    
    history_entry = {
        "index": new_index,
        "prompt": result_log["prompt"],
        "output": result_log["output"],
        "scripts": result_log["scripts"]
    }
    history["history"].append(history_entry)
    history["current_index"] = new_index

    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)

    preview_symlink = os.path.join(session_path, "preview.mp4")
    if os.path.exists(preview_symlink):
        os.remove(preview_symlink)
    os.symlink(f"proxy{new_index}.mp4", preview_symlink)

    return {
        "status": "success",
        "output_url": f"/static/{request.session_id}/preview.mp4",
        "steps_taken": len(result_log["scripts"])
    }

# ... (rest of main.py is unchanged: /undo, /static, uvicorn.run)
@app.post("/undo")
async def undo_edit(request: UndoRequest):
    session_path = os.path.join(SESSIONS_DIR, request.session_id)
    if not os.path.exists(session_path):
        raise fastapi.HTTPException(status_code=404, detail="Session not found")

    history_path = os.path.join(session_path, "history.json")
    with open(history_path, "r") as f:
        history = json.load(f)

    current_index = history["current_index"]
    new_index = max(0, current_index - request.steps)
    history["current_index"] = new_index

    with open(history_path, "w") as f:
        json.dump(history, f, indent=2)

    preview_symlink = os.path.join(session_path, "preview.mp4")
    if os.path.exists(preview_symlink):
        os.remove(preview_symlink)
    os.symlink(f"proxy{new_index}.mp4", preview_symlink)

    return {"status": "success", "preview": f"proxy{new_index}.mp4"}

@app.get("/static/{session_id}/{filename}")
async def get_session_file(session_id: str, filename: str):
    file_path = os.path.join(SESSIONS_DIR, session_id, filename)
    if not os.path.exists(file_path):
        return fastapi.responses.JSONResponse(status_code=404, content={"error": "File not found"})
    return FileResponse(file_path)

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)