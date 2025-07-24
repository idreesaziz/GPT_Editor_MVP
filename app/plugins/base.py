# app/plugins/base.py

from abc import ABC, abstractmethod
import logging
import json
import os
from typing import Dict, Any, List
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
    def execute_task(self, task_details: Dict, asset_unit_path: str, run_logger: logging.Logger) -> List[str]:
        """
        Executes the specific task for this plugin within a dedicated directory.

        Args:
            task_details: A dictionary from the planner's 'generation_tasks' list.
            asset_unit_path: The absolute path to the dedicated asset unit directory
                             where all files for this task should be saved.
            run_logger: The logger for this specific execution run.

        Returns:
            A list of filenames (relative to asset_unit_path) of the generated assets.
        """
        pass

    def _create_metadata_file(
        self, 
        task_details: Dict, 
        asset_unit_path: str, 
        child_assets: List[str], 
        plugin_data: Dict[str, Any]
    ):
        """
        Creates a standardized metadata.json file for a generated asset unit.
        This is a helper method to be called by concrete plugin implementations.
        """
        meta_filepath = os.path.join(asset_unit_path, "metadata.json")
        
        metadata = {
            "unit_id": task_details.get('unit_id'),
            "child_assets": child_assets,
            "generating_plugin": self.name,
            "source_prompt": task_details.get('task'),
            "creation_timestamp": datetime.now(timezone.utc).isoformat(),
            "plugin_data": plugin_data
        }
        
        with open(meta_filepath, 'w') as f:
            json.dump(metadata, f, indent=2)