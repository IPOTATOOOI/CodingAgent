"""大语言模型客户端的环境配置。"""

from dataclasses import dataclass
import os

from dotenv import load_dotenv


class ConfigurationError(ValueError):
    """缺少必要的 LLM 配置时抛出的异常。"""


@dataclass(frozen=True)
class Settings:
    """连接 LLM 服务所需的配置。"""

    api_key: str
    model: str
    base_url: str | None = None

    @classmethod
    def from_env(cls) -> "Settings":
        """从 ``.env`` 和系统环境变量创建配置。"""
        load_dotenv()

        api_key = os.getenv("LLM_API_KEY", "").strip()
        model = os.getenv("LLM_MODEL", "").strip()
        base_url = os.getenv("LLM_BASE_URL", "").strip() or None

        if not api_key:
            raise ConfigurationError("LLM_API_KEY is not set.")
        if not model:
            raise ConfigurationError("LLM_MODEL is not set.")

        return cls(api_key=api_key, model=model, base_url=base_url)
