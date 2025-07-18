# app/plugins/base.py

from abc import ABC, abstractmethod
import logging
from typing import Dict

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
                          It includes the 'task' description and 'output_filename'.
            session_path: The absolute path to the current session directory.
            run_logger: The logger for this specific execution run.

        Returns:
            The filename of the generated asset, which should match 'output_filename'.
        """
        pass