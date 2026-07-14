"""阶段一无需外部服务即可运行的代码契约测试。"""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]


class Phase1ContractTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_ask_user_passes_real_question(self):
        source = self.read("predModels/Tools/ask_user_tool.py")
        self.assertIn("_input_handler(question)", source)
        self.assertNotIn('_input_handler("👉 请回复: ")', source)

    def test_password_policy_is_argon2_only(self):
        source = self.read("app/services/user_service.py")
        self.assertIn("PasswordHasher", source)
        self.assertIn('stored.startswith("$argon2")', source)
        self.assertNotIn("import hashlib", source)
        self.assertNotIn("is_legacy", source)
    def test_request_models_do_not_accept_user_id(self):
        source = self.read("app/schemas/chat.py")
        self.assertNotIn("user_id: int", source)

    def test_routes_use_current_user_dependency(self):
        for relative in ("app/routers/chat.py", "app/routers/sessions.py"):
            source = self.read(relative)
            self.assertIn("get_current_user", source)
            self.assertNotIn("req.user_id", source)

    def test_database_schema_has_user_isolation_and_no_test_event(self):
        source = self.read("sql/memory_schema.sql")
        self.assertIn("user_id", source)
        self.assertNotIn("EVERY 10 SECOND", source)

    def test_frontend_uses_bearer_token(self):
        source = self.read("app/main.py")
        self.assertIn("Authorization", source)
        self.assertIn("access_token", source)
        self.assertNotIn("user_id: userId", source)

    def test_prompt_requires_truthful_tool_execution(self):
        source = self.read("Agent/prompt.py")
        self.assertIn("工具执行真实性", source)
        self.assertIn("未实际调用工具前，禁止声称", source)
        self.assertIn("必须先调用对应工具并获得结果", source)
    def test_prediction_cache_separates_historical_backtest(self):
        cache_source = self.read("predModels/Tools/cache_manager.py")
        predictor_source = self.read("predModels/Tools/pv_predictor.py")
        migration_source = self.read("sql/migrations/002_prediction_weather_data_mode.sql")
        self.assertIn("historical_actual", cache_source)
        self.assertIn("get_prediction_data_mode", predictor_source)
        self.assertIn("read_archive_cache(station_id, predict_date)", predictor_source)
        self.assertIn("weather_data_mode", migration_source)
        self.assertIn("uk_station_time_mode", migration_source)


if __name__ == "__main__":
    unittest.main()
