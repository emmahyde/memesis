"""Tests for core.llm — shared LLM transport.

Bedrock transport has been removed. API-key transport is intentionally
disabled. All calls go through claude-agent-sdk on subscription OAuth, with
`claude -p` subprocess as the fallback. These tests mock the SDK path
(`_call_via_agent_sdk`) and the CLI helper directly; they never exercise a
real API-key codepath.
"""

import json
import os
from unittest.mock import MagicMock, patch

import pytest

from core.llm import (
    _call_via_claude_cli,
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
# _call_via_claude_cli JSON usage parsing
# ---------------------------------------------------------------------------


class TestCallViaClaudeCli:
    def test_parses_json_usage_payload(self):
        payload = {
            "result": "hello",
            "usage": {
                "input_tokens": 12,
                "output_tokens": 5,
                "cache_read_input_tokens": 7,
            },
        }
        proc = MagicMock(returncode=0, stdout=json.dumps(payload), stderr="")
        with (
            patch("core.llm.shutil.which", return_value="/usr/bin/claude"),
            patch("core.llm.subprocess.run", return_value=proc),
        ):
            text, in_toks, out_toks, cache_read = _call_via_claude_cli("prompt")
        assert text == "hello"
        assert in_toks == 12
        assert out_toks == 5
        assert cache_read == 7

    def test_falls_back_when_stdout_is_not_json(self):
        proc = MagicMock(returncode=0, stdout="plain text response", stderr="")
        with (
            patch("core.llm.shutil.which", return_value="/usr/bin/claude"),
            patch("core.llm.subprocess.run", return_value=proc),
        ):
            text, in_toks, out_toks, cache_read = _call_via_claude_cli("prompt")
        assert text == "plain text response"
        assert in_toks is None
        assert out_toks is None
        assert cache_read is None


# ---------------------------------------------------------------------------
# call_llm — OAuth (agent-SDK) path
#
# call_llm runs `asyncio.run(_call_via_agent_sdk(...))` then
# strip_markdown_fences. Patching _call_via_agent_sdk directly keeps the test
# fast and avoids spawning a real subprocess.
# ---------------------------------------------------------------------------


def _async_return_sdk(text, in_toks=None, out_toks=None):
    """Mock for _call_via_agent_sdk: returns the (text, in, out) tuple shape."""

    async def _coro(*_args, **_kwargs):
        return text, in_toks, out_toks

    return _coro


def _async_raise(exc):
    async def _coro(*_args, **_kwargs):
        raise exc

    return _coro


class TestCallLlmOAuthPath:
    def test_returns_stripped_text(self):
        with (
            patch.dict(os.environ, os.environ.copy(), clear=True),
            patch("core.llm._AGENT_SDK_AVAILABLE", True),
            patch(
                "core.llm._call_via_agent_sdk",
                side_effect=_async_return_sdk('```json\n{"result": true}\n```'),
            ),
        ):
            assert call_llm("test prompt") == '{"result": true}'

    def test_passes_prompt_to_sdk(self):
        captured = {}

        async def _spy(
            prompt: str,
            system_prompt: str | None = None,
        ) -> tuple[str, int | None, int | None]:
            captured["prompt"] = prompt
            return "ok", None, None

        with (
            patch.dict(os.environ, os.environ.copy(), clear=True),
            patch("core.llm._AGENT_SDK_AVAILABLE", True),
            patch("core.llm._call_via_agent_sdk", side_effect=_spy),
        ):
            call_llm("test prompt")
            assert captured["prompt"] == "test prompt"

    def test_exceptions_propagate(self):
        with (
            patch.dict(os.environ, os.environ.copy(), clear=True),
            patch("core.llm._AGENT_SDK_AVAILABLE", True),
            patch(
                "core.llm._call_via_agent_sdk",
                side_effect=_async_raise(ValueError("SDK error")),
            ),
        ):
            with pytest.raises(ValueError, match="SDK error"):
                call_llm("test")

    def test_falls_back_to_cli_when_sdk_unavailable(self):
        with (
            patch.dict(os.environ, os.environ.copy(), clear=True),
            patch("core.llm._AGENT_SDK_AVAILABLE", False),
            patch(
                "core.llm._call_via_claude_cli",
                return_value=("cli-output", None, None, None),
            ) as mock_cli,
        ):
            assert call_llm("test") == "cli-output"
            mock_cli.assert_called_once_with("test", system_prompt_path=None)


# ---------------------------------------------------------------------------
# llm_envelope trace events
# ---------------------------------------------------------------------------


class TestLlmEnvelopeTrace:
    """call_llm() emits llm_envelope trace events via core.trace.get_active_writer()."""

    def _make_mock_writer(self) -> MagicMock:
        writer = MagicMock()
        writer.emit = MagicMock()
        return writer

    def test_envelope_emitted_on_oauth_path_with_null_tokens(self):
        """OAuth path: llm_envelope event has None for token counts (no usage object)."""
        import hashlib

        writer = self._make_mock_writer()
        prompt = "oauth prompt"
        # On OAuth path, model arg is None, so model_key=""
        expected_hash = hashlib.sha256(("" + prompt).encode()).hexdigest()[:16]

        with (
            patch.dict(os.environ, os.environ.copy(), clear=True),
            patch("core.llm._AGENT_SDK_AVAILABLE", True),
            patch(
                "core.llm._call_via_agent_sdk",
                side_effect=_async_return_sdk("response text"),
            ),
            patch("core.llm.get_active_writer", return_value=writer),
        ):
            call_llm(prompt)

        writer.emit.assert_called_once()
        payload = writer.emit.call_args.kwargs["payload"]
        assert payload["prompt_hash"] == expected_hash
        assert payload["input_tokens"] is None
        assert payload["output_tokens"] is None
        assert payload["cache_read_input_tokens"] is None

    def test_no_emit_when_no_active_writer(self):
        """No writer → emit is never called; call_llm still returns result."""
        with (
            patch.dict(os.environ, os.environ.copy(), clear=True),
            patch("core.llm._AGENT_SDK_AVAILABLE", True),
            patch(
                "core.llm._call_via_agent_sdk",
                side_effect=_async_return_sdk("result"),
            ),
            patch("core.llm.get_active_writer", return_value=None),
        ):
            result = call_llm("prompt")
        assert result == "result"

    def test_trace_import_error_does_not_crash_call_llm(self):
        """If core.trace raises unexpectedly, call_llm still returns normally."""
        with (
            patch.dict(os.environ, os.environ.copy(), clear=True),
            patch("core.llm._AGENT_SDK_AVAILABLE", True),
            patch(
                "core.llm._call_via_agent_sdk",
                side_effect=_async_return_sdk("safe result"),
            ),
            patch(
                "core.llm.get_active_writer", side_effect=RuntimeError("trace broken")
            ),
        ):
            result = call_llm("prompt")
        assert result == "safe result"
