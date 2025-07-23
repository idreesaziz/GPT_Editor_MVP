# app/plugins/base.py

from abc import ABC, abstractmethod
import logging
import json
import os
from typing import Dict, Any
from datetime import datetime, timezone

class ToolPlugin(ABC):
    """
    Abstract Base Class for a self-contained, self-executing plugin.
    Each plugin is a factory for creating a specific type of media asset.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """A unique, human-readable name for the plugin (e.g., 'Imagen Image Generator')."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """
        A description for the Planner LLM to understand what this tool does.
        e.g., 'Generates photorealistic or artistic images from a text description.'
        """
        pass

    @abstractmethod
    def execute_task(self, task_details: Dict, session_path: str, run_logger: logging.Logger) -> str:
        """
        Executes the specific task for this plugin. This method contains all logic
        for generating code/prompts, calling APIs or CLIs, and saving the final asset.

        Args:
            task_details: A dictionary from the planner's 'generation_tasks' list.
            session_path: The absolute path to the current session directory.
            run_logger: The logger for this specific execution run.

        Returns:
            The filename of the generated asset.
        """
        pass

    # --- NEW: Shared metadata creation method for all plugins ---
    def _create_metadata_file(self, task_details: Dict, session_path: str, plugin_data: Dict[str, Any]):
        """
        Creates a standardized .meta.json file for a generated asset.
        This is a helper method to be called by concrete plugin implementations.
        """
        output_filename = task_details['output_filename']
        meta_filename = f"{os.path.splitext(output_filename)[0]}.meta.json"
        meta_filepath = os.path.join(session_path, meta_filename)
        
        metadata = {
            "asset_filename": output_filename,
            "generating_plugin": self.name,
            "source_prompt": task_details.get('task'),
            "creation_timestamp": datetime.now(timezone.utc).isoformat(),
            "plugin_data": plugin_data
        }
        
        with open(meta_filepath, 'w') as f:
            json.dump(metadata, f, indent=2)