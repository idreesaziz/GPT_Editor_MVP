# Video Editing AI Agent - Architecture Overview

This document outlines the architecture of the AI-powered video editing application. The system is designed to interpret a user's natural language prompt, break it down into a series of technical steps, generate executable Python scripts for each step, and then run them to produce a final edited video.

## Core Concepts

**Session-Based Workflow:**  
Every uploaded video initiates a unique "session," identified by a UUID. All artifacts for that editing job—including the original video, proxies, generated scripts, logs, and edit history—are stored in a dedicated session directory. This ensures isolation and statefulness.

**Planner-Generator-Executor Model:**  
The core logic is decoupled into three main agents:
- **The Planner (`planner.py`):** Takes the user's prompt and breaks it down into a logical sequence of discrete tasks (a "plan").
- **The Script Generator (`script_gen.py`):** Takes a single task from the plan and generates a Python script to accomplish it, using a specific "tool".
- **The Executor (`executor.py`):** Runs the validated Python script in a controlled environment.

**Extensible Tool System (`plugins/`):**  
The system's capabilities are defined as "Tool Plugins". Each plugin represents a specific tool (e.g., FFmpeg, FFprobe), informs the Planner of its capabilities, and provides the system prompt and validation logic for the Script Generator. This makes the system highly extensible.

**Sandbox Validation & Self-Correction:**  
Generated scripts are first run in a temporary, isolated "sandbox" with dummy files. If the script fails, the error is captured and fed back to the Script Generator AI, which then attempts to correct its own code. This retry loop dramatically increases the success rate.

---

## Architecture Diagram

```mermaid
sequenceDiagram
    participant User
    participant FastAPI (main.py)
    participant Orchestrator (orchestrator.py)
    participant Planner (planner.py)
    participant ScriptGen (script_gen.py)
    participant Executor (executor.py)
    participant Gemini_AI as Gemini AI

    User->>+FastAPI: POST /edit (prompt, session_id)
    FastAPI->>+Orchestrator: process_complex_request()
    Orchestrator->>+Planner: create_plan(prompt, tools)
    Planner->>+Gemini_AI: Generate plan from prompt + tool descriptions
    Gemini_AI-->>-Planner: JSON plan (e.g., [{task, tool}, ...])
    Planner-->>-Orchestrator: Return plan
    
    Orchestrator->>Orchestrator: Loop through each step in plan

    Note over Orchestrator,ScriptGen: For each step...
    Orchestrator->>+ScriptGen: generate_validated_script(task, plugin, context)
    
    loop Validation & Retry Loop
        ScriptGen->>ScriptGen: Create sandbox with dummy files
        ScriptGen->>+Gemini_AI: Generate Python script for the task
        Gemini_AI-->>-ScriptGen: Return Python script
        ScriptGen->>ScriptGen: Validate script in sandbox
        alt Script fails validation
            ScriptGen->>ScriptGen: Capture error, add to prompt as feedback
        else Script is valid
            break
        end
    end
    
    ScriptGen-->>-Orchestrator: Return validated script content
    Orchestrator->>Orchestrator: Save script to session directory
    Orchestrator->>+Executor: execute_script(script_path, cwd)
    Executor->>Executor: Runs script via subprocess
    Executor-->>-Orchestrator: Return execution result
    
    Orchestrator->>Orchestrator: Clean up intermediate files, update history.json
    Orchestrator-->>-FastAPI: Return success/error
    FastAPI-->>-User: JSON Response (output_url, status)

```

---

## Component Breakdown

- **main.py:** The web server entry point (FastAPI). Defines API endpoints, manages session creation, and invokes the Orchestrator.
- **orchestrator.py:** The central coordinator. Manages the edit workflow, calls the Planner, iterates through each step, invokes ScriptGen and Executor, manages files and logs.
- **planner.py:** The high-level planning agent. Constructs a prompt for Gemini, providing the user's request and available tools. Returns a structured JSON list of tasks.
- **script_gen.py:** The code generation agent. Takes a single task and, using a system prompt from the relevant ToolPlugin, asks Gemini to write a Python script. Handles validation and self-correction.
- **executor.py:** Runs scripts in a controlled environment, capturing output and errors.
- **plugins/:** Modular tools.
  - **base.py:** Abstract base class for plugins.
  - **ffmpeg_plugin.py:** Plugin for video manipulation using FFmpeg.
  - **metadata_extractor_plugin.py:** Plugin for extracting video metadata with ffprobe.
- **sandbox_provider.py:** (or logic in script_gen.py) Creates high-fidelity dummy files in the sandbox for validation.
- **media_utils.py:** Helper functions for interacting with media files (e.g., get_asset_metadata).
- **video_io.py:** Handles file I/O, saving uploads, and creating proxies.
- **logging_config.py:** Sets up per-run log files for debugging.
- **prompts.py:** Stores prompt templates for ScriptGen.
- **models.py:** Defines Pydantic models for API request bodies.

---

## How to Run

**Install Dependencies:**
```bash
pip install -r requirements.txt
```

**Install System Dependencies:**  
Requires `ffmpeg` and `ffprobe` in your system's PATH.

**Set Up Environment Variables:**  
Create a `.env` file in the root directory:
```
GOOGLE_API_KEY="your_gemini_api_key_here"
```

**Run the Server:**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

The application will be available at [http://localhost:8000](http://localhost:8000).
