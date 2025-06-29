# GPT-Powered Video Editing API

A FastAPI-based application that provides an API for video editing using LLM-generated Python scripts. This project allows users to:

- Upload videos and create editing proxies
- Apply edits to videos using natural language prompts
- Undo/redo edits with version history
- Generate Python/FFmpeg scripts for video manipulation

## Architecture

- **FastAPI** – Web API framework
- **Gemini 2.5 Pro** – Generates Python edit scripts
- **FFmpeg** – Used for video processing
- **Python subprocess** – Executes generated scripts
- **Local disk-based session system**

## Structure

```
demo-video-editor/
├─ app/
│  ├─ __init__.py
│  ├─ main.py          # FastAPI app + endpoints
│  ├─ video_io.py      # proxy downscale, file paths
│  ├─ script_gen.py    # chat → Python script prompt builder
│  ├─ executor.py      # sandbox runner (subprocess)
│  └─ models.py        # Pydantic schemas
├─ scripts/
│  └─ test_flow.py     # CLI script to test workflow
├─ .env                # API_KEYS
└─ requirements.txt
```

## Getting Started

1. Install requirements:
   ```
   pip install -r requirements.txt
   ```

2. Set up your API key:
   Create a `.env` file with your Gemini API key:
   ```
   GOOGLE_API_KEY=your_api_key
   ```

3. Run the server:
   ```
   python -m app.main
   ```

4. Test with the CLI:
   ```
   python scripts/test_flow.py /path/to/video.mp4 "your edit instruction"
   ```

## API Endpoints

- `POST /upload` - Upload a video and create proxy
- `POST /edit` - Edit a video with natural language
- `POST /undo` - Revert to previous version
- `GET /static/{session_id}/preview.mp4` - View current edit

## Notes

Each edit generates a Python script that is saved along with the output video, creating a full history of changes that can be navigated with undo/redo operations.
