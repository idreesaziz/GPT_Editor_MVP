# Narrative

**An AI-Native Video Editing Engine for Conversational Content Creation**

Narrative is a proof-of-concept video editing platform that transforms traditional timeline-based editing into natural language conversations. Built on FastAPI and powered by Google's Gemini AI models, it demonstrates how modern AI can reimagine creative workflows through intelligent agent orchestration and declarative composition.

## Technical Innovation

### Multi-Agent Architecture
Narrative implements a sophisticated multi-agent system where specialized AI components collaborate to interpret user intent and execute complex video editing operations:

- **Prompt Synthesizer**: Analyzes conversational input and current project state to generate unambiguous editing instructions
- **Strategic Planner**: Creates comprehensive execution plans, determining asset generation requirements and compositional changes
- **Task Orchestrator**: Manages workflow execution across multiple specialized tool plugins
- **SWML Compositor**: Generates frame-accurate edit decision lists in a custom declarative format

### Declarative Composition Language (SWML)
The system introduces Swimlane Media Language (SWML), a JSON-based format for describing video compositions. This approach provides:
- Human-readable edit specifications
- Version control compatibility  
- Non-destructive editing workflows
- Clear separation of concerns between AI planning and media processing

### Extensible Plugin System
Modular tool plugins handle specialized tasks:
- **Procedural Animation**: Manim-based text, title, and shape generation
- **Photorealistic Video**: Vertex AI Veo integration for cinematic content
- **Image Generation**: Imagen API for backgrounds and graphics
- **Audio Processing**: Text-to-speech and music generation
- **Media Transformation**: FFmpeg-based processing pipeline

## Key Features

**Conversational Interface**: Edit videos through natural language commands, from initial creation to fine-grained adjustments.

**Multi-Modal Asset Generation**: Generate video, images, audio, and animations on-demand using state-of-the-art AI models.

**Advanced Media Processing**: Apply sophisticated transformations including color correction, audio extraction, and visual effects.

**Session Management**: Maintain complete project history with asset tracking and version control.

**Asynchronous Processing**: Background task execution ensures responsive API performance during complex operations.

**Extensible Architecture**: Plugin-based design enables rapid integration of new AI capabilities and processing tools.

## System Architecture

```
User Prompt → FastAPI → Synthesizer → Planner → Orchestrator → Tool Plugins
                                                      ↓
Static Assets ← SWML Generator ← Asset Management ← Generated Media
```

Each edit request flows through a structured pipeline:
1. **Prompt Analysis**: Context-aware interpretation of user intent
2. **Strategic Planning**: Asset generation and composition strategy
3. **Parallel Execution**: Multi-threaded tool plugin orchestration
4. **Composition**: Declarative timeline generation in SWML format
5. **Rendering**: Final video output with comprehensive metadata

## Technology Stack

**Backend Framework**: FastAPI with Uvicorn ASGI server  
**AI Integration**: Google Gemini Pro, Vertex AI (Imagen, Veo, Text-to-Speech)  
**Media Processing**: FFmpeg, Manim procedural animation  
**Data Management**: Pydantic validation, JSON-based asset metadata  
**Infrastructure**: Asynchronous task processing, Google Cloud Storage integration

## Installation & Setup

### Prerequisites
- Python 3.9+
- FFmpeg with ffprobe
- Google Cloud Project with Vertex AI API enabled
- Google Cloud Storage bucket for media assets

### Configuration
```bash
git clone <repository-url>
cd narrative
pip install -r requirements.txt
```

Create `.env` configuration:
```env
GOOGLE_API_KEY="your-gemini-api-key"
GOOGLE_APPLICATION_CREDENTIALS="/path/to/service-account.json"
VERTEX_PROJECT_ID="your-gcp-project-id"
VERTEX_LOCATION="us-central1"
VEO_OUTPUT_GCS_BUCKET="your-media-bucket"
```

### Launch
```bash
uvicorn app.main:app --reload --env-file .env
```

## API Reference

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `POST` | `/sessions` | Initialize new editing session |
| `POST` | `/sessions/{id}/assets` | Upload media assets |
| `POST` | `/edit` | Execute conversational edit operation |
| `GET` | `/sessions/{id}/status` | Monitor background task progress |
| `GET` | `/sessions/{id}/result` | Retrieve completed video output |

## Architecture Extensibility

The plugin architecture supports rapid capability expansion. New tools integrate through a standardized interface:

```python
class CustomPlugin(ToolPlugin):
    @property
    def name(self) -> str:
        return "Custom Processing Tool"
    
    def execute_task(self, task_details: Dict, asset_path: str, logger: Logger) -> List[str]:
        # Implementation with automatic orchestrator integration
        pass
```

## Development Status

This is an early-stage proof-of-concept demonstrating the viability of AI-native video editing workflows. The system successfully validates core architectural concepts while providing a foundation for production-scale development.

## Technical Highlights

- **Complex State Management**: Sophisticated session and asset lifecycle management
- **AI Model Orchestration**: Coordinated multi-model inference workflows  
- **Scalable Plugin Architecture**: Modular design supporting diverse media processing capabilities
- **Declarative Composition**: Novel approach to programmatic video editing
- **Production-Ready Patterns**: Professional FastAPI implementation with comprehensive error handling

---

*Narrative represents a forward-looking approach to creative software, demonstrating how conversational AI can enhance rather than replace human creativity in professional media workflows.*