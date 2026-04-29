"""Tests for core.llm — shared LLM transport."""

import os
from unittest.mock import MagicMock, patch

import pytest

from core.llm import (
    BEDROCK_MODEL,
    DEFAULT_MODEL,
    _make_client,
    call_llm,
    strip_markdown_fences,
)


# ---------------------------------------------------------------------------
# strip_markdown_fences
# ---------------------------------------------------------------------------


class TestStripMarkdownFences:
    def test_json_fence(self):
        text = '```json\n{"key": "value"}\n```'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_bare_fence(self):
        text = '```\n{"key": "value"}\n```'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_no_fence(self):
        text = '{"key": "value"}'
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_whitespace_around_fences(self):
        text = '  \n```json\n{"key": "value"}\n```\n  '
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_multiline_content(self):
        text = '```json\n{\n  "a": 1,\n  "b": 2\n}\n```'
        result = strip_markdown_fences(text)
        assert '"a": 1' in result
        assert '"b": 2' in result

    def test_no_closing_fence(self):
        text = '```json\n{"key": "value"}'
        # Should still strip the opening fence line
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_empty_string(self):
        assert strip_markdown_fences("") == ""

    def test_plain_text_preserved(self):
        text = "This is a narrative response with no fences."
        assert strip_markdown_fences(text) == text


# ---------------------------------------------------------------------------
# _make_client
# ---------------------------------------------------------------------------


class TestMakeClient:
    def test_bedrock_client_when_env_set(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}):
            with patch("core.llm.anthropic.AnthropicBedrock") as mock_bedrock:
                client = _make_client()
                mock_bedrock.assert_called_once()

    def test_direct_client_when_env_unset(self):
        env = os.environ.copy()
        env.pop("CLAUDE_CODE_USE_BEDROCK", None)
        with patch.dict(os.environ, env, clear=True):
            with patch("core.llm.anthropic.Anthropic") as mock_direct:
                client = _make_client()
                mock_direct.assert_called_once()


# ---------------------------------------------------------------------------
# call_llm
# ---------------------------------------------------------------------------


class TestCallLlm:
    def _mock_response(self, text: str) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=text)]
        return mock_msg

    def test_returns_stripped_text(self):
        with patch("core.llm.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = self._mock_response(
                '```json\n{"result": true}\n```'
            )
            result = call_llm("test prompt")
            assert result == '{"result": true}'

    def test_passes_correct_params(self):
        with patch("core.llm.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = self._mock_response("ok")
            call_llm("test prompt", max_tokens=2048, temperature=0.3)

            mock_cls.return_value.messages.create.assert_called_once_with(
                model=DEFAULT_MODEL,
                max_tokens=2048,
                temperature=0.3,
                messages=[{"role": "user", "content": "test prompt"}],
            )

    def test_default_model_selection(self):
        with patch("core.llm.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = self._mock_response("ok")
            call_llm("test")
            call_args = mock_cls.return_value.messages.create.call_args
            assert call_args.kwargs["model"] == DEFAULT_MODEL

    def test_bedrock_model_selection(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}):
            with patch("core.llm.anthropic.AnthropicBedrock") as mock_cls:
                mock_cls.return_value.messages.create.return_value = self._mock_response("ok")
                call_llm("test")
                call_args = mock_cls.return_value.messages.create.call_args
                assert call_args.kwargs["model"] == BEDROCK_MODEL

    def test_explicit_model_override(self):
        with patch("core.llm.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = self._mock_response("ok")
            call_llm("test", model="claude-haiku-4-5-20251001")
            call_args = mock_cls.return_value.messages.create.call_args
            assert call_args.kwargs["model"] == "claude-haiku-4-5-20251001"

    def test_exceptions_propagate(self):
        """call_llm does NOT catch API errors — callers handle them."""
        with patch("core.llm.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.side_effect = Exception("API error")
            with pytest.raises(Exception, match="API error"):
                call_llm("test")

    def test_default_max_tokens(self):
        with patch("core.llm.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = self._mock_response("ok")
            call_llm("test")
            call_args = mock_cls.return_value.messages.create.call_args
            assert call_args.kwargs["max_tokens"] == 8192

    def test_default_temperature(self):
        with patch("core.llm.anthropic.Anthropic") as mock_cls:
            mock_cls.return_value.messages.create.return_value = self._mock_response("ok")
            call_llm("test")
            call_args = mock_cls.return_value.messages.create.call_args
            assert call_args.kwargs["temperature"] == 0
