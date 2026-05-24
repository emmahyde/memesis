"""Tests for system-prompt support in core.llm.

Covers _load_system_prompt resolution/caching and verifies call_llm threads
the resolved system prompt into every transport. No real LLM calls.
"""

import os

import pytest

from core import llm


SYSTEM_PROMPT_NAMES = [
    "base",
    "extraction",
    "consolidation",
    "curation",
]


@pytest.fixture(autouse=True)
def _clear_system_prompt_cache():
    """Each test sees a cold cache — caching is itself under test."""
    llm._system_prompt_cache.clear()
    yield
    llm._system_prompt_cache.clear()


# --- _load_system_prompt -----------------------------------------------------


def test_load_returns_none_for_none():
    assert llm._load_system_prompt(None) is None
    assert llm._load_system_prompt("") is None


@pytest.mark.parametrize("name", SYSTEM_PROMPT_NAMES)
def test_bundled_prompt_resolves_and_is_nonempty(name):
    resolved = llm._load_system_prompt(name)
    assert resolved is not None
    path, text = resolved
    assert path.endswith(os.path.join("core", "system_prompts", f"{name}.md"))
    assert os.path.isabs(path)
    assert len(text) > 0
    assert text == text.strip()


def test_dot_md_suffix_is_accepted():
    by_name = llm._load_system_prompt("base")
    llm._system_prompt_cache.clear()
    by_suffix = llm._load_system_prompt("base.md")
    assert by_name is not None and by_suffix is not None
    assert by_name[1] == by_suffix[1]


def test_absolute_path_resolves():
    abs = os.path.join(llm._SYSTEM_PROMPT_DIR, "base.md")
    resolved = llm._load_system_prompt(abs)
    assert resolved is not None
    assert resolved[0] == os.path.abspath(abs)


def test_missing_prompt_raises():
    with pytest.raises(FileNotFoundError):
        llm._load_system_prompt("definitely-not-a-real-prompt-zzz")


def test_result_is_cached():
    first = llm._load_system_prompt("curation")
    second = llm._load_system_prompt("curation")
    assert first is second  # same tuple object — served from cache


# --- call_llm threading ------------------------------------------------------


def _force_oauth(monkeypatch):
    monkeypatch.delenv("CLAUDE_CODE_USE_BEDROCK", raising=False)


def test_call_llm_passes_system_prompt_text_to_sdk(monkeypatch):
    captured = {}

    async def fake_sdk(prompt, system_prompt=None):
        captured["system_prompt"] = system_prompt
        return ("result-text", None, None)

    _force_oauth(monkeypatch)
    monkeypatch.setattr(llm, "_AGENT_SDK_AVAILABLE", True)
    monkeypatch.setattr(llm, "_call_via_agent_sdk", fake_sdk)

    out = llm.call_llm("hello", system_prompt_file="extraction")

    assert out == "result-text"
    assert captured["system_prompt"] is not None
    assert "extraction stage" in captured["system_prompt"]


def test_call_llm_none_system_prompt_passes_none_to_sdk(monkeypatch):
    captured = {}

    async def fake_sdk(prompt, system_prompt=None):
        captured["system_prompt"] = system_prompt
        return ("result-text", None, None)

    _force_oauth(monkeypatch)
    monkeypatch.setattr(llm, "_AGENT_SDK_AVAILABLE", True)
    monkeypatch.setattr(llm, "_call_via_agent_sdk", fake_sdk)

    llm.call_llm("hello")  # no system_prompt_file

    assert captured["system_prompt"] is None


def test_call_llm_passes_system_prompt_path_to_cli(monkeypatch):
    captured = {}

    def fake_cli(prompt, *, system_prompt_path=None, **kw):
        captured["path"] = system_prompt_path
        return "cli-result", None, None, None

    _force_oauth(monkeypatch)
    monkeypatch.setattr(llm, "_AGENT_SDK_AVAILABLE", False)
    monkeypatch.setattr(llm, "_call_via_claude_cli", fake_cli)

    out = llm.call_llm("hello", system_prompt_file="base")

    assert out == "cli-result"
    assert captured["path"] is not None
    assert captured["path"].endswith(os.path.join("core", "system_prompts", "base.md"))


def test_call_llm_bad_system_prompt_raises(monkeypatch):
    _force_oauth(monkeypatch)
    monkeypatch.setattr(llm, "_AGENT_SDK_AVAILABLE", True)
    with pytest.raises(FileNotFoundError):
        llm.call_llm("hello", system_prompt_file="no-such-prompt-xyz")
