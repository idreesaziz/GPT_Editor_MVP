# app/report_collector.py

import time
import json
import traceback
from datetime import datetime
from typing import Dict, Any, List, Optional
import logging

class ReportCollector:
    """Collects comprehensive execution data for machine-readable reports"""
    
    def __init__(self, edit_index: int, user_prompt: str):
        self.report = {
            "status": "in_progress",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "edit_index": edit_index,
            "user_prompt": user_prompt,
            
            "ai_plan": None,
            
            "execution_phases": {
                "planning": {"status": "not_started", "duration_ms": 0, "errors": []},
                "asset_generation": {"status": "not_started", "duration_ms": 0, "tasks_completed": 0, "assets_created": []},
                "composition": {"status": "not_started", "duration_ms": 0, "errors": [], "swml_attempts": 0},
                "rendering": {"status": "not_started", "duration_ms": 0, "errors": []}
            },
            
            "assets_created": [],
            
            "final_outputs": {
                "video_path": None,
                "swml_path": None,
                "swml_content": None
            },
            
            "errors": [],
            
            "performance_metrics": {
                "total_duration_ms": 0,
                "swml_generation_attempts": 0,
                "memory_peak_mb": None
            }
        }
        
        self.start_time = time.time()
        self.phase_start_times = {}
    
    def start_phase(self, phase_name: str):
        """Mark the start of an execution phase"""
        self.phase_start_times[phase_name] = time.time()
        self.report["execution_phases"][phase_name]["status"] = "in_progress"
    
    def complete_phase(self, phase_name: str, success: bool = True):
        """Mark the completion of an execution phase"""
        if phase_name in self.phase_start_times:
            duration = (time.time() - self.phase_start_times[phase_name]) * 1000
            self.report["execution_phases"][phase_name]["duration_ms"] = int(duration)
        
        self.report["execution_phases"][phase_name]["status"] = "success" if success else "failure"
    
    def add_error(self, phase: str, error_type: str, message: str, exception: Optional[Exception] = None):
        """Add an error to both the phase and global error list"""
        error_entry = {
            "phase": phase,
            "error_type": error_type,
            "message": message,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
        
        if exception:
            error_entry["traceback"] = traceback.format_exc()
        
        self.report["errors"].append(error_entry)
        
        # Only add to phase errors if it's a valid phase
        if phase in self.report["execution_phases"]:
            self.report["execution_phases"][phase]["errors"].append(error_entry)
    
    def set_ai_plan(self, plan: Dict[str, Any]):
        """Store the AI's original plan"""
        self.report["ai_plan"] = plan
    
    def add_asset_created(self, filename: str, tool_used: str, metadata: Dict[str, Any], generation_prompt: str):
        """Record a newly created asset"""
        asset_info = {
            "filename": filename,
            "tool_used": tool_used,
            "metadata": metadata,
            "generation_prompt": generation_prompt
        }
        
        self.report["assets_created"].append(asset_info)
        self.report["execution_phases"]["asset_generation"]["assets_created"].append(asset_info)
    
    def increment_asset_generation_tasks(self):
        """Increment the count of completed asset generation tasks"""
        self.report["execution_phases"]["asset_generation"]["tasks_completed"] += 1
    
    def increment_swml_attempts(self):
        """Increment the count of SWML generation attempts"""
        self.report["performance_metrics"]["swml_generation_attempts"] += 1
        self.report["execution_phases"]["composition"]["swml_attempts"] += 1
    
    def set_final_outputs(self, video_path: str, swml_path: str, swml_content: Dict[str, Any]):
        """Store the final output files and content"""
        self.report["final_outputs"] = {
            "video_path": video_path,
            "swml_path": swml_path,
            "swml_content": swml_content
        }
    
    def finalize(self, success: bool) -> Dict[str, Any]:
        """Finalize the report and return it"""
        self.report["status"] = "success" if success else "failure"
        self.report["performance_metrics"]["total_duration_ms"] = int((time.time() - self.start_time) * 1000)
        
        # Calculate completion timestamp
        self.report["completion_timestamp"] = datetime.utcnow().isoformat() + "Z"
        
        return self.report
    
    def get_current_report(self) -> Dict[str, Any]:
        """Get the current state of the report (for debugging)"""
        return self.report.copy()
