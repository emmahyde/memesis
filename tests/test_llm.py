"""Tests for core.llm — shared LLM transport.

API-key transport is intentionally disabled (`_have_api_key()` always returns
False). All non-Bedrock calls go through claude-agent-sdk on subscription
OAuth. These tests mock the SDK path (`_call_via_agent_sdk`) and the Bedrock
client; they never exercise an API-key codepath.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from core.llm import (
    BEDROCK_MODEL,
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
        assert strip_markdown_fences(text) == '{"key": "value"}'

    def test_empty_string(self):
        assert strip_markdown_fences("") == ""

    def test_plain_text_preserved(self):
        text = "This is a narrative response with no fences."
        assert strip_markdown_fences(text) == text


# ---------------------------------------------------------------------------
# _make_client (Bedrock-only branch is reachable; API-key branch unreachable
# from call_llm but the helper itself can still be exercised directly)
# ---------------------------------------------------------------------------


class TestMakeClient:
    def test_bedrock_client_when_env_set(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}):
            with patch("core.llm.anthropic.AnthropicBedrock") as mock_bedrock:
                _make_client()
                mock_bedrock.assert_called_once()

    def test_direct_client_when_env_unset(self):
        env = os.environ.copy()
        env.pop("CLAUDE_CODE_USE_BEDROCK", None)
        with patch.dict(os.environ, env, clear=True):
            with patch("core.llm.anthropic.Anthropic") as mock_direct:
                _make_client()
                mock_direct.assert_called_once()


# ---------------------------------------------------------------------------
# call_llm — OAuth (agent-SDK) path
#
# call_llm runs `asyncio.run(_call_via_agent_sdk(...))` then
# strip_markdown_fences. Patching _call_via_agent_sdk directly keeps the test
# fast and avoids spawning a real subprocess.
# ---------------------------------------------------------------------------


def _async_return(value):
    async def _coro(*_args, **_kwargs):
        return value
    return _coro


def _async_raise(exc):
    async def _coro(*_args, **_kwargs):
        raise exc
    return _coro


class TestCallLlmOAuthPath:
    def test_returns_stripped_text(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_USE_BEDROCK"}
        with patch.dict(os.environ, env, clear=True), \
             patch("core.llm._AGENT_SDK_AVAILABLE", True), \
             patch("core.llm._call_via_agent_sdk",
                   side_effect=_async_return('```json\n{"result": true}\n```')):
            assert call_llm("test prompt") == '{"result": true}'

    def test_passes_prompt_to_sdk(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_USE_BEDROCK"}
        captured = {}

        async def _spy(prompt: str) -> str:
            captured["prompt"] = prompt
            return "ok"

        with patch.dict(os.environ, env, clear=True), \
             patch("core.llm._AGENT_SDK_AVAILABLE", True), \
             patch("core.llm._call_via_agent_sdk", side_effect=_spy):
            call_llm("test prompt")
            assert captured["prompt"] == "test prompt"

    def test_exceptions_propagate(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_USE_BEDROCK"}
        with patch.dict(os.environ, env, clear=True), \
             patch("core.llm._AGENT_SDK_AVAILABLE", True), \
             patch("core.llm._call_via_agent_sdk",
                   side_effect=_async_raise(ValueError("SDK error"))):
            with pytest.raises(ValueError, match="SDK error"):
                call_llm("test")

    def test_falls_back_to_cli_when_sdk_unavailable(self):
        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_USE_BEDROCK"}
        with patch.dict(os.environ, env, clear=True), \
             patch("core.llm._AGENT_SDK_AVAILABLE", False), \
             patch("core.llm._call_via_claude_cli", return_value="cli-output") as mock_cli:
            assert call_llm("test") == "cli-output"
            mock_cli.assert_called_once_with("test")


# ---------------------------------------------------------------------------
# call_llm — Bedrock path (the only branch that respects model/max_tokens/temperature)
# ---------------------------------------------------------------------------


class TestCallLlmBedrockPath:
    def _mock_response(self, text: str) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=text)]
        return mock_msg

    def test_bedrock_model_default(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}):
            with patch("core.llm.anthropic.AnthropicBedrock") as mock_cls:
                mock_cls.return_value.messages.create.return_value = self._mock_response("ok")
                call_llm("test")
                kwargs = mock_cls.return_value.messages.create.call_args.kwargs
                assert kwargs["model"] == BEDROCK_MODEL
                assert kwargs["max_tokens"] == 8192
                assert kwargs["temperature"] == 0

    def test_bedrock_explicit_overrides(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}):
            with patch("core.llm.anthropic.AnthropicBedrock") as mock_cls:
                mock_cls.return_value.messages.create.return_value = self._mock_response("ok")
                call_llm(
                    "test",
                    model="claude-haiku-4-5-20251001",
                    max_tokens=2048,
                    temperature=0.3,
                )
                mock_cls.return_value.messages.create.assert_called_once_with(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=2048,
                    temperature=0.3,
                    messages=[{"role": "user", "content": "test"}],
                )

    def test_bedrock_strips_fences(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}):
            with patch("core.llm.anthropic.AnthropicBedrock") as mock_cls:
                mock_cls.return_value.messages.create.return_value = self._mock_response(
                    '```json\n{"x": 1}\n```'
                )
                assert call_llm("test") == '{"x": 1}'

    def test_bedrock_exceptions_propagate(self):
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}):
            with patch("core.llm.anthropic.AnthropicBedrock") as mock_cls:
                mock_cls.return_value.messages.create.side_effect = Exception("API error")
                with pytest.raises(Exception, match="API error"):
                    call_llm("test")


# ---------------------------------------------------------------------------
# Guard: API-key transport remains disabled.
# ---------------------------------------------------------------------------


class TestApiKeyTransportDisabled:
    def test_have_api_key_always_false(self):
        from core.llm import _have_api_key
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-fake"}):
            assert _have_api_key() is False


# ---------------------------------------------------------------------------
# llm_envelope trace events
# ---------------------------------------------------------------------------


class TestLlmEnvelopeTrace:
    """call_llm() emits llm_envelope trace events via core.trace.get_active_writer()."""

    def _make_mock_response(self, text: str, input_tokens: int, output_tokens: int) -> MagicMock:
        mock_msg = MagicMock()
        mock_msg.content = [MagicMock(text=text)]
        mock_msg.usage = MagicMock(input_tokens=input_tokens, output_tokens=output_tokens)
        return mock_msg

    def _make_mock_writer(self) -> MagicMock:
        writer = MagicMock()
        writer.emit = MagicMock()
        return writer

    def test_envelope_emitted_on_bedrock_path_with_token_counts(self):
        """Bedrock path: llm_envelope event has correct hash, model, and token counts."""
        import hashlib
        from core.llm import BEDROCK_MODEL

        writer = self._make_mock_writer()
        prompt = "test prompt for envelope"
        expected_hash = hashlib.sha256((BEDROCK_MODEL + prompt).encode()).hexdigest()[:16]

        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}), \
             patch("core.llm.anthropic.AnthropicBedrock") as mock_cls, \
             patch("core.llm.get_active_writer", return_value=writer):
            mock_cls.return_value.messages.create.return_value = self._make_mock_response(
                "response text", input_tokens=42, output_tokens=17
            )
            call_llm(prompt)

        writer.emit.assert_called_once()
        call_args = writer.emit.call_args
        # emit() is called with keyword args: stage=, event=, payload=
        assert call_args.kwargs["stage"] == "llm"
        assert call_args.kwargs["event"] == "llm_envelope"
        payload = call_args.kwargs["payload"]
        assert payload["prompt_hash"] == expected_hash
        assert payload["model"] == BEDROCK_MODEL
        assert payload["input_tokens"] == 42
        assert payload["output_tokens"] == 17

    def test_no_emit_when_no_active_writer(self):
        """No writer → emit is never called; call_llm still returns result."""
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}), \
             patch("core.llm.anthropic.AnthropicBedrock") as mock_cls, \
             patch("core.llm.get_active_writer", return_value=None):
            mock_cls.return_value.messages.create.return_value = self._make_mock_response(
                "result", input_tokens=1, output_tokens=1
            )
            result = call_llm("prompt")
        assert result == "result"

    def test_envelope_emitted_on_oauth_path_with_null_tokens(self):
        """OAuth path: llm_envelope event has None for token counts (no usage object)."""
        import hashlib

        writer = self._make_mock_writer()
        prompt = "oauth prompt"
        # On OAuth path, model arg is None, so model_key="" and display is DEFAULT_MODEL
        expected_hash = hashlib.sha256(("" + prompt).encode()).hexdigest()[:16]

        env = {k: v for k, v in os.environ.items() if k != "CLAUDE_CODE_USE_BEDROCK"}
        with patch.dict(os.environ, env, clear=True), \
             patch("core.llm._AGENT_SDK_AVAILABLE", True), \
             patch("core.llm._call_via_agent_sdk", side_effect=_async_return("response text")), \
             patch("core.llm.get_active_writer", return_value=writer):
            call_llm(prompt)

        writer.emit.assert_called_once()
        payload = writer.emit.call_args.kwargs["payload"]
        assert payload["prompt_hash"] == expected_hash
        assert payload["input_tokens"] is None
        assert payload["output_tokens"] is None

    def test_trace_import_error_does_not_crash_call_llm(self):
        """If core.trace raises unexpectedly, call_llm still returns normally."""
        with patch.dict(os.environ, {"CLAUDE_CODE_USE_BEDROCK": "true"}), \
             patch("core.llm.anthropic.AnthropicBedrock") as mock_cls, \
             patch("core.llm.get_active_writer", side_effect=RuntimeError("trace broken")):
            mock_cls.return_value.messages.create.return_value = self._make_mock_response(
                "safe result", input_tokens=5, output_tokens=5
            )
            result = call_llm("prompt")
        assert result == "safe result"
