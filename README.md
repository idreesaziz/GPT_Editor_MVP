# Gen-AI Video Editing System

A modular, multi-stage pipeline that translates high-level user prompts into executed video editing operations using Large Language Models for reasoning and code generation within a safe and traceable execution environment.

## Architecture Overview

The system follows a four-stage process for every edit request: **Plan**, **Generate**, **Validate**, and **Execute**. This workflow is managed by a central orchestrator and relies on a pluggable tool system to define capabilities.

The core philosophy is to deconstruct complex tasks into discrete, verifiable steps, ensuring safety and traceability throughout the entire editing process.

## Key Components

### Web API & Session Management
- **File**: `main.py`
- **Purpose**: Exposes a FastAPI web server as the primary interface
- **Features**: 
  - User session management with dedicated filesystem directories
  - Contains all assets, generated scripts, logs, and stateful history (`history.json`)

### Planning System
- **File**: `planner.py`
- **Purpose**: Breaks complex user requests into sequential, atomic plans
- **Process**:
  - Input: User prompt and list of available tools
  - Uses powerful reasoning model (e.g., Gemini 1.5 Pro)
  - Output: JSON list of task objects with descriptions and tool assignments

**Example Output**:
```json
[
  {
    "task": "Crop the video into a 1:1 aspect ratio",
    "tool": "FFmpeg Video Editor"
  },
  {
    "task": "Apply a black and white filter to the video",
    "tool": "FFmpeg Video Editor"
  }
]
```

### Script Generation & Validation
- **Files**: `script_gen.py`, `plugins/`
- **Process**:
  - Generator creates Python scripts using fast instruction-following models
  - Generated scripts undergo mandatory validation in isolated sandbox environments
  - Validation includes running scripts against test files to ensure correctness
  - Failed validations trigger self-correction attempts

### Execution Engine
- **File**: `executor.py`
- **Purpose**: Runs validated scripts in the main session directory
- **Features**:
  - Subprocess-based execution with full output capture
  - Comprehensive logging for traceability
  - Direct application of changes to actual video files

### Central Orchestrator
- **File**: `orchestrator.py`
- **Role**: Manages the complete end-to-end workflow
- **Responsibilities**:
  - Plan creation and iteration management
  - State management between steps
  - Context passing and script history maintenance
  - Cleanup of intermediate files

## Plugin System

The architecture is extensible through a `ToolPlugin` interface defined in `plugins/base.py`. Each plugin represents a distinct capability and handles:

- **Advertising**: Name, description, and prerequisites for the Planner
- **Instructing**: System prompts to guide Script Generator code generation
- **Validating**: Critical safety testing of generated scripts in sandbox environments

### Current Plugins

**FFmpegPlugin**
- Handles all video/audio manipulations via FFmpeg
- Creates high-fidelity dummy videos for validation testing
- Matches resolution, duration, and other properties of source files

**MetadataExtractorPlugin**
- Reads video properties using ffprobe
- Provides metadata context for other operations

## Safety & Sandboxing

Safety is implemented through multi-layered sandboxing during validation:

### Filesystem Isolation
- Temporary directories created for each validation attempt
- Complete separation from production assets

### Asset Duplication
- Real source assets copied to sandbox environments
- Maintains access to genuine metadata for validation

### Plugin-Level Validation
- Scripts executed within controlled sandbox environments
- Timeout mechanisms prevent infinite loops
- Subprocess execution with comprehensive error handling

## Logging & State Management

### Per-Run Logging
- **File**: `logging_config.py`
- Dedicated timestamped log files for each edit request
- Millisecond-precision tracing of entire pipeline execution
- Comprehensive debugging capabilities

### Session History
- **File**: `history.json`
- Maintains sequence of successful edits
- Forms Directed Acyclic Graph (DAG) of operations
- Enables features like operation rollback

## Data Flow

```
User Prompt → Planning → Script Generation → Validation → Execution → Result
     ↓           ↓              ↓              ↓            ↓         ↓
  FastAPI   →  LLM Pro  →   LLM Flash   →   Plugin   →  Executor → Video
```

## Usage

The system accepts natural language prompts describing video editing tasks:

**Input**: "crop the video to a square and then make it black and white"

**Process**:
1. **Plan**: Break into atomic steps (crop → filter)
2. **Generate**: Create Python scripts for each step
3. **Validate**: Test scripts in safe sandbox environments
4. **Execute**: Apply validated changes to actual video files

## Technical Requirements

- FastAPI for web interface
- Large Language Models (Gemini 1.5 Pro for planning, Flash for generation)
- FFmpeg for video processing
- Python subprocess execution environment
- Filesystem-based session management

## File Structure

```
├── main.py                 # FastAPI web server
├── orchestrator.py         # Central workflow management
├── planner.py             # Request planning system
├── script_gen.py          # Code generation and validation
├── executor.py            # Script execution engine
├── logging_config.py      # Logging configuration
└── plugins/               # Extensible tool system
    ├── base.py           # Plugin interface
    ├── ffmpeg_plugin.py  # Video processing capabilities
    └── metadata_plugin.py # Video property extraction
```

## Security Considerations

- All generated code undergoes mandatory validation
- Sandbox environments prevent damage to source files
- Comprehensive logging enables audit trails
- Plugin-based architecture isolates tool-specific risks
- Timeout mechanisms prevent resource exhaustion

## Extensibility

The plugin system allows for easy addition of new capabilities:

1. Implement the `ToolPlugin` interface
2. Define validation logic specific to your tool
3. Register with the orchestrator
4. Tool becomes available for planning and execution

This architecture ensures that complex video editing tasks can be safely automated while maintaining full transparency and control over the editing process.