"""Environment-based configuration for future project stages."""

from dataclasses import dataclass
import os


@dataclass
class Settings:
    """Configuration values that may be used by later stages."""

    model: str | None = None
    api_key: str | None = None
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        """Create settings from system environment variables."""
        return cls(
            model=os.getenv("LLM_MODEL"),
            api_key=os.getenv("LLM_API_KEY"),
            base_url=os.getenv("LLM_BASE_URL"),
        )
