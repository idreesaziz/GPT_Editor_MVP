# Comprehensive Report System Documentation

## Overview
The system now provides detailed, machine-readable reports after each editing attempt. This replaces the simple success/failure responses with comprehensive execution data.

## Report Structure

```json
{
  "status": "success" | "failure",
  "timestamp": "2025-07-22T10:30:00Z",
  "completion_timestamp": "2025-07-22T10:30:22Z",
  "edit_index": 5,
  "user_prompt": "Add a blue circle in the center",
  
  "ai_plan": {
    "generation_tasks": [
      {
        "tool": "Manim Animation Generator",
        "task": "Create a blue circle animation...",
        "output_filename": "gen_asset_5_1_blue_circle.mov",
        "parameters": { "duration": 5.0 }
      }
    ],
    "composition_prompt": "Add the new blue circle to the center..."
  },
  
  "execution_phases": {
    "planning": { 
      "status": "success", 
      "duration_ms": 1200, 
      "errors": [] 
    },
    "asset_generation": { 
      "status": "success", 
      "duration_ms": 5000,
      "tasks_completed": 1,
      "assets_created": [...]
    },
    "composition": { 
      "status": "success", 
      "duration_ms": 800, 
      "errors": [],
      "swml_attempts": 1
    },
    "rendering": { 
      "status": "success", 
      "duration_ms": 15000, 
      "errors": [] 
    }
  },
  
  "assets_created": [
    {
      "filename": "gen_asset_5_1_blue_circle.mov",
      "tool_used": "Manim Animation Generator",
      "metadata": { 
        "width": 1920, 
        "height": 1080, 
        "duration": 5.0,
        "type": "video"
      },
      "generation_prompt": "Create a blue circle animation..."
    }
  ],
  
  "final_outputs": {
    "video_path": "/sessions/abc/proxy5.mp4",
    "swml_path": "/sessions/abc/comp5.swml", 
    "swml_content": { ... }  // Full SWML JSON
  },
  
  "errors": [
    // Empty array for successful runs
    // Populated with detailed error info for failures
  ],
  
  "performance_metrics": {
    "total_duration_ms": 22000,
    "swml_generation_attempts": 1,
    "memory_peak_mb": null  // Future enhancement
  }
}
```

## API Response Changes

### Success Response
```json
{
  "status": "success",
  "new_history": { ... },
  "output_url": "/static/session-id/preview.mp4",
  "log_file": "run_edit_5.log",
  "detailed_report": { ... }  // Full report as shown above
}
```

### Failure Response
```json
{
  "status": "error",
  "error": "Brief error message",
  "log_file": "run_edit_5.log", 
  "detailed_report": { ... }  // Full report with error details
}
```

## Benefits

1. **Machine Readable**: Structured JSON for automated analysis
2. **Complete Audit Trail**: Every step is tracked with timing and status
3. **Error Debugging**: Exact error location and context
4. **Performance Monitoring**: Duration metrics for each phase
5. **Asset Tracking**: Full metadata for all generated content
6. **AI Transparency**: Original plan vs. actual execution
7. **SWML Preservation**: Complete final state included

## Usage Examples

### Analyzing Failures
```python
if report["status"] == "failure":
    for error in report["errors"]:
        print(f"Error in {error['phase']}: {error['message']}")
```

### Performance Analysis
```python
total_time = report["performance_metrics"]["total_duration_ms"]
render_time = report["execution_phases"]["rendering"]["duration_ms"]
render_percentage = (render_time / total_time) * 100
```

### Asset Inventory
```python
for asset in report["assets_created"]:
    print(f"Generated {asset['filename']} using {asset['tool_used']}")
```
