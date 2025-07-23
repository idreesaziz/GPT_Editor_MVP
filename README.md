# AI-Driven Video Editor Backend

[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.68+-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)

## Overview

The AI-Driven Video Editor Backend is an intelligent video composition system that transforms natural language descriptions into professional video edits. By leveraging advanced Large Language Models (LLMs) and a custom declarative video rendering engine, this platform enables users to create sophisticated video content through conversational interfaces.

The system implements a multi-layered architecture combining AI-powered planning, automated asset generation, and precise video composition using Swimlane Markup Language (SWML), providing both flexibility and control over the video editing process.

## Key Features

### 🤖 Intelligent Natural Language Processing
- **Conversational Video Editing**: Transform descriptive text prompts into executable video editing operations
- **Multi-Modal AI Planning**: Utilizes Gemini 2.5 Flash for sophisticated request decomposition and task orchestration
- **Context-Aware Decision Making**: Maintains session context for coherent multi-step editing workflows

### 🎬 Advanced Asset Management
- **Dynamic Asset Generation**: Integrated Manim plugin for programmatic animation and text overlay creation
- **Intelligent Asset Reuse**: Maintains asset metadata and source code for future modifications and amendments
- **Multi-Format Support**: Handles video, image, and audio assets with automatic metadata extraction

### 🏗️ Declarative Video Composition
- **SWML Architecture**: JSON-based Swimlane Markup Language for precise video element control
- **Temporal Precision**: Frame-accurate timing and transformation specifications
- **Modular Composition**: Reusable components and templates for consistent video production

### ⚡ Performance & Workflow Optimization
- **Fast Preview Rendering**: Low-latency proxy generation for iterative editing workflows
- **Session-Based Architecture**: Complete edit history with undo/redo capabilities and time-travel editing
- **Comprehensive Logging**: Detailed execution reports and structured debugging information

## Architecture

### Core Components

#### Orchestrator (`app/orchestrator.py`)
Central coordination engine responsible for:
- Project state management and asset inventory
- Multi-phase workflow orchestration
- Plugin execution and dependency resolution
- Video rendering pipeline management

#### AI Planning System (`app/planner.py`)
LLM-powered intelligent planning module that:
- Analyzes user intent and project constraints
- Determines optimal asset generation strategies
- Creates execution plans for complex multi-step operations

#### SWML Generator (`app/swml_generator.py`)
Specialized AI module for video composition that:
- Transforms high-level plans into precise SWML specifications
- Ensures technical compliance with Swimlane Engine requirements
- Optimizes composition for performance and quality

#### Plugin Architecture (`app/plugins/`)
Extensible system supporting:
- **Manim Plugin**: Programmatic animation generation with Python code synthesis
- **Asset Generation Framework**: Standardized interface for additional creative tools
- **Metadata Preservation**: Source code and parameter storage for iterative refinement

## Technical Requirements

### System Dependencies
- **Python**: Version 3.9 or higher
- **FFmpeg**: Essential for media processing and metadata extraction
- **Manim**: Required for animation generation capabilities
- **Swimlane Engine**: Declarative video rendering framework

### API Dependencies
- **Google AI Studio API**: Required for LLM-powered planning and generation
- **FastAPI Framework**: Web server and API endpoint management

## Installation & Setup

### 1. Repository Setup
```bash
git clone https://github.com/your-organization/ai-video-editor-backend.git
cd ai-video-editor-backend
```

### 2. Environment Configuration
```bash
python3 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 3. API Configuration
Create `.env` file in project root:
```env
GOOGLE_API_KEY=your_google_ai_studio_api_key
```

### 4. Application Launch
```bash
uvicorn app.main:app --reload --env-file .env
```

The service will be available at `http://127.0.0.1:8000`

## API Reference

### Session Management

#### Create New Session
```http
POST /sessions
Content-Type: application/json

{
  "width": 1920,
  "height": 1080,
  "fps": 30,
  "duration": 10.0
}
```

**Response:**
```json
{
  "session_id": "uuid-session-identifier",
  "message": "New session created successfully."
}
```

#### Upload Media Assets
```http
POST /sessions/{session_id}/assets
Content-Type: multipart/form-data

file: [media_file]
```

### Video Editing Operations

#### Execute Natural Language Edit
```http
POST /edit
Content-Type: application/json

{
  "session_id": "uuid-session-identifier",
  "prompt": "Add a fade-in transition to the uploaded video over 2 seconds",
  "base_index": null,
  "preview": true
}
```

**Response:**
```json
{
  "status": "success",
  "new_history": {
    "current_index": 2,
    "history": [...]
  },
  "output_url": "/static/{session_id}/preview.mp4",
  "log_file": "run_edit_2.log",
  "detailed_report": {...}
}
```

### Asset Retrieval

#### Download Generated Content
```http
GET /static/{session_id}/{filename}
```

## Project Structure

```
ai-video-editor-backend/
├── app/
│   ├── main.py                 # FastAPI application entry point
│   ├── orchestrator.py         # Workflow coordination engine
│   ├── planner.py             # AI planning and decision making
│   ├── swml_generator.py      # Video composition generation
│   ├── media_utils.py         # Media processing utilities
│   ├── video_io.py            # Video I/O operations
│   ├── logging_config.py      # Structured logging configuration
│   ├── models.py              # API data models
│   ├── prompts.py             # LLM prompt templates
│   ├── executor.py            # Code execution environment
│   ├── report_collector.py    # Execution analytics
│   └── plugins/
│       ├── base.py            # Plugin interface specification
│       └── manim_plugin.py    # Animation generation plugin
├── sessions/                   # Session data storage
├── requirements.txt           # Python dependencies
├── .env                       # Environment configuration
└── README.md
```

## Development & Contribution

### Code Quality Standards
- **Type Hints**: All functions must include comprehensive type annotations
- **Documentation**: Docstrings required for all public methods and classes
- **Testing**: Unit tests for core functionality with pytest framework
- **Linting**: Code must pass flake8 and black formatting standards

### Plugin Development
To extend the system with additional asset generation capabilities:

1. Inherit from `plugins.base.ToolPlugin`
2. Implement required abstract methods
3. Register plugin in orchestrator configuration
4. Add comprehensive error handling and logging

### Contribution Guidelines
1. Fork the repository and create feature branches
2. Ensure all tests pass and maintain code coverage above 80%
3. Submit pull requests with detailed descriptions and test cases
4. Follow semantic versioning for releases

## Security Considerations

- **API Key Protection**: Never commit API keys to version control
- **Input Validation**: All user inputs are sanitized and validated
- **File System Isolation**: Session data is isolated with proper permissions
- **Resource Limits**: Configurable limits on asset sizes and processing time

## Performance & Scalability

### Optimization Features
- **Lazy Loading**: Assets loaded on-demand to minimize memory usage
- **Caching Strategy**: Intelligent caching of generated assets and SWML states
- **Concurrent Processing**: Asynchronous operations for I/O-bound tasks
- **Resource Monitoring**: Built-in metrics for performance analysis

### Scaling Considerations
- **Horizontal Scaling**: Stateless design enables multi-instance deployment
- **Storage Architecture**: Session data can be moved to distributed storage
- **Load Balancing**: API endpoints support standard load balancing strategies

## Troubleshooting

### Common Issues

**FFmpeg Not Found**
```bash
# Ubuntu/Debian
sudo apt update && sudo apt install ffmpeg

# macOS
brew install ffmpeg

# Windows
# Download from https://ffmpeg.org/download.html
```

**Manim Installation Issues**
```bash
# Install system dependencies first
pip install manim
# See official Manim documentation for platform-specific requirements
```

**Google API Authentication**
- Verify API key is correctly set in `.env` file
- Ensure Google AI Studio API access is enabled for your account
- Check API usage quotas and billing status

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for complete terms and conditions.

## Support & Documentation

For additional support, please refer to:
- **API Documentation**: Available at `/docs` endpoint when running the application
- **Issue Tracking**: GitHub Issues for bug reports and feature requests
- **Community**: Discussions and community support via GitHub Discussions

---
