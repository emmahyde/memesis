"""
Tests for core/codebook.py — vocabulary compression and encoding/decoding.

All tests are deterministic; no LLM calls or network requests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.codebook import (
    LANG_SHORT,
    TOOL_SHORT,
    FRAMEWORK_SHORT,
    FILE_SHORT,
    ERROR_SHORT,
    PREF_SHORT,
    encode_field_value,
    decode_field_value,
    get_codebook_summary,
    get_codebook_token_overhead,
    is_codebook_enabled,
    contains_codebook_tokens,
    _ProtectedElements,
    _extract_code_blocks,
    _extract_inline_code,
    _extract_urls,
    _restore_protected,
)


# ---------------------------------------------------------------------------
# Language encoding
# ---------------------------------------------------------------------------

class TestLanguageEncoding:
    def test_python_to_py(self):
        assert encode_field_value("Use python for scripting.") == "Use py for scripting."

    def test_typescript_to_ts(self):
        assert encode_field_value("Prefer typescript for types.") == "Prefer ts for types."

    def test_javascript_to_js(self):
        assert encode_field_value("javascript is everywhere.") == "js is everywhere."

    def test_rust_to_rs(self):
        assert encode_field_value("rust is fast.") == "rs is fast."

    def test_csharp_to_cs(self):
        assert encode_field_value("Use c# for this.") == "Use cs for this."

    def test_cpp(self):
        assert encode_field_value("c++ is complex.") == "cpp is complex."


# ---------------------------------------------------------------------------
# Tool encoding
# ---------------------------------------------------------------------------

class TestToolEncoding:
    def test_kubernetes_to_k8s(self):
        assert encode_field_value("Deploy to kubernetes.") == "Deploy to k8s."

    def test_terraform_to_tf(self):
        assert encode_field_value("Use terraform for infra.") == "Use tf for infra."

    def test_jetbrains_to_jb(self):
        assert encode_field_value("jetbrains IDE.") == "jb IDE."


# ---------------------------------------------------------------------------
# Framework encoding
# ---------------------------------------------------------------------------

class TestFrameworkEncoding:
    def test_postgresql_to_pg(self):
        assert encode_field_value("Use postgresql.") == "Use pg."

    def test_nextjs(self):
        assert encode_field_value("next.js is great.") == "nextjs is great."

    def test_sqlalchemy_to_sqla(self):
        assert encode_field_value("sqlalchemy ORM.") == "sqla ORM."


# ---------------------------------------------------------------------------
# Error pattern encoding
# ---------------------------------------------------------------------------

class TestErrorEncoding:
    def test_connection_refused(self):
        assert encode_field_value("Got connection refused.") == "Got conn_refused."

    def test_import_error(self):
        assert encode_field_value("Fix import error.") == "Fix import_err."

    def test_segmentation_fault(self):
        assert encode_field_value("Segmentation fault occurred.") == "Segfault occurred."


# ---------------------------------------------------------------------------
# Case insensitivity
# ---------------------------------------------------------------------------

class TestCaseInsensitivity:
    def test_lowercase(self):
        assert encode_field_value("python") == "py"

    def test_title_case(self):
        assert encode_field_value("Python") == "Py"

    def test_uppercase(self):
        assert encode_field_value("PYTHON") == "PY"

    def test_mixed_case_sentence(self):
        assert encode_field_value("Use Python and TypeScript.") == "Use Py and Ts."


# ---------------------------------------------------------------------------
# Round-trip
# ---------------------------------------------------------------------------

class TestRoundTrip:
    def test_simple_roundtrip(self):
        original = "Use python and typescript."
        encoded = encode_field_value(original)
        decoded = decode_field_value(encoded)
        assert decoded == original

    def test_multi_term_roundtrip(self):
        original = "Deploy kubernetes with terraform."
        encoded = encode_field_value(original)
        decoded = decode_field_value(encoded)
        assert decoded == original

    def test_case_preserved_roundtrip(self):
        original = "Use Python and Kubernetes."
        encoded = encode_field_value(original)
        decoded = decode_field_value(encoded)
        assert decoded == original


# ---------------------------------------------------------------------------
# Code block protection
# ---------------------------------------------------------------------------

class TestCodeBlockProtection:
    def test_fenced_code_not_encoded(self):
        text = "```python\nprint('hello')\n```\nUse python."
        result = encode_field_value(text)
        assert "```python\nprint('hello')\n```" in result
        assert "Use py." in result

    def test_inline_code_not_encoded(self):
        text = "Use `python` for scripting, not python2."
        result = encode_field_value(text)
        assert "`python`" in result
        assert "not python2" in result


# ---------------------------------------------------------------------------
# URL protection
# ---------------------------------------------------------------------------

class TestUrlProtection:
    def test_bare_url_not_encoded(self):
        text = "See https://python.org for python docs."
        result = encode_field_value(text)
        assert "https://python.org" in result
        assert "py docs" in result

    def test_markdown_link_not_encoded(self):
        text = "[python](https://python.org) is great."
        result = encode_field_value(text)
        assert "[python](https://python.org)" in result
        assert "is great" in result


# ---------------------------------------------------------------------------
# Codebook summary
# ---------------------------------------------------------------------------

class TestCodebookSummary:
    def test_summary_starts_with_codebook(self):
        summary = get_codebook_summary()
        assert summary.startswith("CODEBOOK:")

    def test_summary_contains_categories(self):
        summary = get_codebook_summary()
        assert "CODEBOOK:" in summary
        assert "=" in summary
        assert ":" in summary

    def test_summary_under_500_chars(self):
        summary = get_codebook_summary()
        assert len(summary) <= 500, f"Summary is {len(summary)} chars"

    def test_summary_format(self):
        summary = get_codebook_summary()
        parts = summary.replace("CODEBOOK: ", "").split("; ")
        for part in parts:
            assert "=" in part
            cat, items = part.split("=", 1)
            assert "," in items or ":" in items


# ---------------------------------------------------------------------------
# Token overhead
# ---------------------------------------------------------------------------

class TestTokenOverhead:
    def test_overhead_is_positive(self):
        assert get_codebook_token_overhead() > 0

    def test_overhead_is_reasonable(self):
        overhead = get_codebook_token_overhead()
        assert overhead < 200, f"Overhead {overhead} seems too high"


# ---------------------------------------------------------------------------
# Environment check
# ---------------------------------------------------------------------------

class TestEnvironmentCheck:
    def test_default_enabled(self, monkeypatch):
        monkeypatch.delenv("MEMESIS_CODEBOOK_ENABLED", raising=False)
        assert is_codebook_enabled() is True

    def test_explicitly_enabled(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_CODEBOOK_ENABLED", "1")
        assert is_codebook_enabled() is True

    def test_disabled_zero(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_CODEBOOK_ENABLED", "0")
        assert is_codebook_enabled() is False

    def test_disabled_false(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_CODEBOOK_ENABLED", "false")
        assert is_codebook_enabled() is False

    def test_disabled_off(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_CODEBOOK_ENABLED", "off")
        assert is_codebook_enabled() is False


# ---------------------------------------------------------------------------
# Content detection
# ---------------------------------------------------------------------------

class TestContentDetection:
    def test_detects_encoded_tokens(self):
        assert contains_codebook_tokens("Use py and ts.") is True

    def test_no_false_positives_on_plain_text(self):
        assert contains_codebook_tokens("Use python and typescript.") is False

    def test_empty_text(self):
        assert contains_codebook_tokens("") is False

    def test_none_text(self):
        assert contains_codebook_tokens(None) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_empty_string(self):
        assert encode_field_value("") == ""

    def test_whitespace_only(self):
        assert encode_field_value("   ") == "   "

    def test_no_vocab_matches(self):
        text = "The quick brown fox."
        assert encode_field_value(text) == text

    def test_partial_word_not_encoded(self):
        text = "pythonic style"
        assert encode_field_value(text) == "pythonic style"

    def test_no_double_encoding(self):
        text = "Use python."
        once = encode_field_value(text)
        twice = encode_field_value(once)
        assert once == twice

    def test_multi_word_phrase(self):
        text = "Fix connection refused errors."
        result = encode_field_value(text)
        assert "conn_refused" in result

    def test_punctuation_around_terms(self):
        text = "Use python, typescript, and rust."
        result = encode_field_value(text)
        assert "py, ts, and rs" in result


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class TestInternalHelpers:
    def test_protected_elements_counter(self):
        pe = _ProtectedElements()
        key1 = pe.add("value1")
        key2 = pe.add("value2")
        assert key1 != key2
        assert pe.placeholders[key1] == "value1"
        assert pe.placeholders[key2] == "value2"

    def test_extract_code_blocks(self):
        text = "Before\n```python\nprint(1)\n```\nAfter"
        pe = _ProtectedElements()
        result = _extract_code_blocks(text, pe)
        assert "```python" not in result
        assert len(pe.placeholders) == 1

    def test_extract_inline_code(self):
        text = "Use `pytest` for testing."
        pe = _ProtectedElements()
        result = _extract_inline_code(text, pe)
        assert "`pytest`" not in result
        assert len(pe.placeholders) == 1

    def test_extract_urls(self):
        text = "See https://example.com and [link](https://foo.bar)."
        pe = _ProtectedElements()
        result = _extract_urls(text, pe)
        assert "https://example.com" not in result
        assert "https://foo.bar" not in result
        assert len(pe.placeholders) == 2

    def test_restore_protected(self):
        pe = _ProtectedElements()
        key = pe.add("original")
        text = f"before {key} after"
        result = _restore_protected(text, pe)
        assert result == "before original after"
