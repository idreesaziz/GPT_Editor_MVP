Of course. Here is a README file describing the architecture of the provided project.

Project Architecture

This document outlines the software architecture of the Gen-AI Video Editing system. The system is designed as a modular, multi-stage pipeline that translates high-level user prompts into a sequence of executed video editing operations.

The core philosophy is to deconstruct a complex task into discrete, verifiable steps, using Large Language Models (LLMs) for reasoning and code generation, all within a safe and traceable execution environment.

Architectural Overview

The system follows a four-stage process for every edit request: Plan, Generate, Validate, and Execute. This workflow is managed by a central orchestrator and relies on a pluggable tool system to define capabilities.

Web API & Session Management (main.py): The system exposes a FastAPI web server as its primary interface. It manages user sessions, where each session is a dedicated directory on the filesystem containing all assets, generated scripts, logs, and a stateful history (history.json).

Planning (planner.py): When a user submits a prompt (e.g., "crop the video to a square and then make it black and white"), the request is first sent to the Planner. The Planner uses a powerful reasoning model (e.g., Gemini 1.5 Pro) to break the complex request into a sequential, atomic plan. Each step in the plan consists of a simple task description and the name of the most appropriate tool to use.

Input: User prompt, list of available tools.

Output: A JSON list of objects, e.g., [{"task": "Crop the video into a 1:1 aspect ratio", "tool": "FFmpeg Video Editor"}, {"task": "Apply a black and white filter to the video", "tool": "FFmpeg Video Editor"}].

Generation & Validation (script_gen.py, plugins/): The Orchestrator iterates through the plan, executing one step at a time. For each step, it invokes the Script Generator.

The Generator takes the single-step task, the chosen tool's system prompt, and contextual information (like input/output filenames and script history).

It uses a fast, instruction-following model (e.g., Gemini 1.5 Flash) to generate a Python script to perform the task.

Crucially, this generated script is not trusted. It immediately enters a validation phase, which is the responsibility of the selected ToolPlugin.

The plugin executes the script in a temporary, isolated sandbox directory. This sandbox contains copies of the real input files. The plugin's validation logic runs the script against these files (or high-fidelity dummies derived from them) to ensure it runs without errors and produces the expected output files before it is approved for real execution. If validation fails, the error is fed back to the LLM for a self-correction attempt.

Execution (executor.py): Once a script has been validated, the Orchestrator passes it to the Executor. The Executor runs the validated script in the main session directory using a subprocess, applying the changes to the actual video files for that step. All output (stdout, stderr) is captured and logged for traceability.

Key Components
1. Orchestrator (orchestrator.py)

The brain of the system. It manages the end-to-end workflow:

Invokes the Planner to create the multi-step plan.

Iterates through the plan, managing the state between steps (e.g., the output of step 1 becomes the input for step 2).

Calls the Script Generator for each step.

Calls the Executor to run the validated script.

Handles logging, context management (passing script history to the generator), and cleanup of intermediate files.

2. Plugin System (plugins/)

The architecture is extensible through a ToolPlugin interface (plugins/base.py). Each plugin represents a distinct capability and is responsible for:

Advertising: Providing its name, description, and prerequisites to the Planner.

Instructing: Providing a specific system prompt to the Script Generator to guide code generation.

Validating: Implementing the validate_script method. This is the most critical role, defining the logic for safely testing a generated script in a sandbox.

Current plugins include:

FFmpegPlugin: For all video/audio manipulations via FFmpeg. Its validator creates high-fidelity dummy videos (matching resolution, duration, etc.) to test ffmpeg commands.

MetadataExtractorPlugin: For reading video properties using ffprobe.

3. Sandboxing & Safety

Safety is a primary architectural concern. The system employs a multi-layered sandboxing approach during the validation phase:

Filesystem Isolation: script_gen.py creates a temporary directory for each validation attempt.

Asset Duplication: It populates this sandbox by copying the real source assets from the session directory. This gives the validation logic access to real metadata.

Plugin-Level Validation: The plugin (e.g., FFmpegPlugin) then runs the generated script within this sandbox. It may create another layer of dummy files (e.g., test patterns) based on the metadata of the copied assets to ensure the script's logic is sound without risking the actual intermediate files. The script is executed via subprocess with a timeout to prevent infinite loops.

4. Logging & State (logging_config.py, main.py)

Per-Run Logging: Every "edit" request spawns a dedicated, timestamped log file within the session directory. This provides an extremely detailed, millisecond-precision trace of the entire Plan -> Generate -> Validate -> Execute pipeline for that specific run, making debugging straightforward.

Session History: A history.json file in each session directory maintains the sequence of successful edits, forming a DAG of operations that enables features like "undo".

Data & Control Flow Diagram
Generated mermaid
sequenceDiagram
    participant User
    participant MainAPI as "main.py (FastAPI)"
    participant Orchestrator as "orchestrator.py"
    participant Planner as "planner.py"
    participant ScriptGen as "script_gen.py"
    participant Plugin as "ToolPlugin"
    participant Executor as "executor.py"
    participant LLM_Pro as "LLM (Pro/Planner)"
    participant LLM_Flash as "LLM (Flash/Generator)"

    User->>MainAPI: POST /edit (prompt)
    MainAPI->>Orchestrator: process_complex_request(prompt)
    Orchestrator->>Planner: create_plan(prompt, tools)
    Planner->>LLM_Pro: Generate plan from prompt
    LLM_Pro-->>Planner: JSON Plan
    Planner-->>Orchestrator: Return Plan
    
    loop For each step in Plan
        Orchestrator->>ScriptGen: generate_validated_script(task, context)
        ScriptGen->>LLM_Flash: Generate script from task
        LLM_Flash-->>ScriptGen: Python Code
        
        Note over ScriptGen, Plugin: Validation Loop
        ScriptGen->>Plugin: validate_script(code, sandbox)
        Plugin-->>ScriptGen: (isValid, errorMsg)
        
        alt Validation Fails
            ScriptGen->>LLM_Flash: Retry with error feedback
            LLM_Flash-->>ScriptGen: New Python Code
        end
        
        ScriptGen-->>Orchestrator: Return Validated Script
        Orchestrator->>Executor: execute_script(script)
        Executor-->>Orchestrator: Execution Result
    end

    Orchestrator-->>MainAPI: Final Result Log
    MainAPI-->>User: Success Response (URL to new video)
