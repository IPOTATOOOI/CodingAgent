"""环境配置测试。"""

import unittest
from unittest.mock import patch

from coding_agent.config import ConfigurationError, Settings


class SettingsTests(unittest.TestCase):
    def test_from_env_loads_all_values(self) -> None:
        environment = {
            "LLM_API_KEY": "test-key",
            "LLM_MODEL": "test-model",
            "LLM_BASE_URL": "https://example.test/v1",
        }

        with patch("coding_agent.config.load_dotenv") as load_dotenv_mock, patch.dict(
            "os.environ", environment, clear=True
        ):
            settings = Settings.from_env()

        load_dotenv_mock.assert_called_once_with()
        self.assertEqual(settings.api_key, "test-key")
        self.assertEqual(settings.model, "test-model")
        self.assertEqual(settings.base_url, "https://example.test/v1")

    def test_base_url_is_optional(self) -> None:
        environment = {"LLM_API_KEY": "test-key", "LLM_MODEL": "test-model"}

        with patch("coding_agent.config.load_dotenv"), patch.dict(
            "os.environ", environment, clear=True
        ):
            settings = Settings.from_env()

        self.assertIsNone(settings.base_url)

    def test_missing_api_key_raises_clear_error(self) -> None:
        with patch("coding_agent.config.load_dotenv"), patch.dict(
            "os.environ", {"LLM_MODEL": "test-model"}, clear=True
        ):
            with self.assertRaisesRegex(ConfigurationError, "LLM_API_KEY is not set"):
                Settings.from_env()

    def test_missing_model_raises_clear_error(self) -> None:
        with patch("coding_agent.config.load_dotenv"), patch.dict(
            "os.environ", {"LLM_API_KEY": "test-key"}, clear=True
        ):
            with self.assertRaisesRegex(ConfigurationError, "LLM_MODEL is not set"):
                Settings.from_env()


if __name__ == "__main__":
    unittest.main()
