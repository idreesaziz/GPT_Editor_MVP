from abc import ABC, abstractmethod
from typing import Tuple, Optional

class ToolPlugin(ABC):
    """Abstract base class for a tool plugin."""

    @property
    @abstractmethod
    def name(self) -> str:
        """A short, descriptive name for the plugin."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """A detailed description for the LLM to understand the plugin's capabilities."""
        pass

    @property
    @abstractmethod
    def prerequisites(self) -> str:
        """A natural language description of any prerequisites for the LLM to understand."""
        pass

    @abstractmethod
    def get_system_instruction(self) -> str:
        """Returns the system instruction prompt for the LLM for this specific tool."""
        pass

    @abstractmethod
    def validate_script(self, script_code: str, sandbox_path: str) -> Tuple[bool, Optional[str]]:
        """
        Validates the generated script within a pre-populated sandbox directory.

        Args:
            script_code: The Python script content to validate.
            sandbox_path: Path to a directory containing dummy assets for validation.
        
        Returns:
            A tuple of (is_valid, error_message).
            error_message is None if is_valid is True.
        """
        pass