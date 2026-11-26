import unittest
from unittest.mock import Mock, patch

from core import llm


class ProviderTokenBudgetTests(unittest.TestCase):
    def test_groq_budget_is_capped_before_request(self):
        response = Mock(status_code=200, text="")
        response.json.return_value = {
            "choices": [{"message": {"content": '{"title":"ok"}'}, "finish_reason": "stop"}]
        }
        config = {"name": "groq", "model": "qwen-test", "base_url": "https://api.groq.com/openai/v1",
                  "api_key": "test", "max_tokens": 16384}
        with patch.object(llm.requests, "post", return_value=response) as post:
            result = llm.complete(config, "prompt", max_tokens=32768, attempts=1)
        self.assertEqual(result, '{"title":"ok"}')
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 16384)

    def test_configured_groq_provider_carries_its_default_token_cap(self):
        with patch.dict("os.environ", {"GROQ_API_KEY": "test-key"}, clear=True):
            config = llm._provider_config("groq")
        self.assertEqual(config["max_tokens"], 16384)

    def test_api_reported_model_limit_retries_same_model_with_reduced_budget(self):
        rejected = Mock(status_code=400, text=(
            "`max_tokens` must be less than or equal to 16384, "
            "the maximum value for max_tokens is 16384"
        ))
        accepted = Mock(status_code=200, text="")
        accepted.json.return_value = {
            "choices": [{"message": {"content": '{"title":"recovered"}'}, "finish_reason": "stop"}]
        }
        config = {"name": "custom", "model": "qwen-test", "base_url": "https://llm.example/v1",
                  "api_key": "test"}
        with patch.object(llm.requests, "post", side_effect=[rejected, accepted]) as post:
            result = llm.complete(config, "prompt", max_tokens=32768, attempts=1)
        self.assertEqual(result, '{"title":"recovered"}')
        self.assertEqual(post.call_count, 2)
        self.assertEqual(post.call_args_list[0].kwargs["json"]["max_tokens"], 32768)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["max_tokens"], 16384)
        self.assertEqual(post.call_args_list[1].kwargs["json"]["model"], "qwen-test")

    def test_explicit_provider_cap_can_be_set_by_environment(self):
        response = Mock(status_code=200, text="")
        response.json.return_value = {
            "choices": [{"message": {"content": '{"title":"ok"}'}, "finish_reason": "stop"}]
        }
        config = {"name": "custom", "model": "model", "base_url": "https://llm.example/v1",
                  "api_key": "test"}
        with patch.dict("os.environ", {"LLM_MAX_TOKENS_CUSTOM": "4096"}), \
             patch.object(llm.requests, "post", return_value=response) as post:
            llm.complete(config, "prompt", max_tokens=32768, attempts=1)
        self.assertEqual(post.call_args.kwargs["json"]["max_tokens"], 4096)


if __name__ == "__main__":
    unittest.main()
