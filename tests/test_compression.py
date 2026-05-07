"""
Tests for core/compression.py — deterministic headlinese-style compression.

All tests are rule-based; no LLM calls or network requests.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from core.compression import (
    ValidationResult,
    compress_memory_content,
    compress_memory_for_stage,
    compress_to_brevity_code,
    get_stage_depth,
    validate_compression,
    compression_ratio,
    _split_frontmatter,
    _ProtectedElements,
    _extract_code_blocks,
    _extract_inline_code,
    _extract_urls,
    _extract_file_paths,
    _extract_commands,
    _extract_dates_and_numbers,
    _drop_words,
    _replace_verbose,
    _drop_throat_clearing,
    _merge_redundant_bullets,
    _shift_simple_present,
    _clean_extra_whitespace,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_memory():
    """A realistic memory content with frontmatter + markdown body."""
    return """---
name: Python Testing Patterns
description: pytest conventions for this project
type: memory
---

You should always use pytest for all testing in this project.
Make sure to run `pytest -v` before committing any changes.

The project uses the following structure:
- Tests are in the `tests/` directory.
- Fixtures are defined in `tests/conftest.py`.
- Coverage is checked with `pytest --cov`.

Note that you must install dependencies first:
```bash
pip install -e ".[test]"
pytest tests/
```

See https://docs.pytest.org for more details.

The user prefers snake_case for Python variable names.
It is important to avoid type: ignore comments; fix types properly.

Key files:
- /Users/emmahyde/projects/memesis/tests/conftest.py
- ./tests/test_models.py
- ../shared/fixtures.py

Last updated: 2024-03-15
Version: v2.1.3
"""


@pytest.fixture
def frontmatter_only():
    return """---
name: Title
description: Summary
type: memory
---
"""


@pytest.fixture
def code_only():
    return """```python
def hello():
    print("world")
```
"""


# ---------------------------------------------------------------------------
# compress_memory_content — basic functionality
# ---------------------------------------------------------------------------

class TestCompressBasic:
    def test_empty_content(self):
        assert compress_memory_content("") == ""
        assert compress_memory_content("   ") == "   "

    def test_invalid_depth_raises(self):
        with pytest.raises(ValueError):
            compress_memory_content("hello", depth="extreme")

    def test_all_depths_accepted(self):
        text = "The quick brown fox jumps over the lazy dog."
        for depth in ("lite", "moderate", "aggressive"):
            result = compress_memory_content(text, depth=depth)
            assert isinstance(result, str)

    def test_articles_dropped(self):
        text = "The cat sat on a mat and looked at an apple."
        result = compress_memory_content(text, depth="lite")
        assert "the" not in result.lower() or result == text
        # "The" and "a" and "an" should be removed
        assert " The " not in result
        assert " a " not in result.lower()
        assert " an " not in result.lower()

    def test_auxiliaries_dropped_moderate(self):
        text = "The system is running and has been tested."
        result = compress_memory_content(text, depth="moderate")
        assert "is" not in result.lower().split()
        assert "has" not in result.lower().split()
        assert "been" not in result.lower().split()

    def test_filler_phrases_dropped(self):
        text = "You should always use pytest for testing."
        result = compress_memory_content(text, depth="lite")
        assert "you should" not in result.lower()

    def test_verbose_replacements_moderate(self):
        text = "We need to implement a solution for this bug."
        result = compress_memory_content(text, depth="moderate")
        assert "implement a solution for" not in result.lower()
        assert "fix" in result.lower()


# ---------------------------------------------------------------------------
# Frontmatter preservation
# ---------------------------------------------------------------------------

class TestFrontmatterPreservation:
    def test_frontmatter_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert result.startswith("---")
        assert "name: Python Testing Patterns" in result
        assert "description: pytest conventions for this project" in result
        assert "type: memory" in result

    def test_frontmatter_only_not_compressed(self, frontmatter_only):
        result = compress_memory_content(frontmatter_only, depth="aggressive")
        assert result.strip() == frontmatter_only.strip()

    def test_no_frontmatter_works(self):
        text = "The system is running and has been tested."
        result = compress_memory_content(text, depth="moderate")
        assert "is" not in result.lower().split()
        assert "has" not in result.lower().split()
        assert "been" not in result.lower().split()


# ---------------------------------------------------------------------------
# Code block preservation
# ---------------------------------------------------------------------------

class TestCodeBlockPreservation:
    def test_fenced_code_block_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert "```bash" in result
        assert "pip install -e" in result
        assert "pytest tests/" in result

    def test_indented_code_block_intact(self):
        text = """Some text.

    def indented():
        pass

More text."""
        result = compress_memory_content(text, depth="aggressive")
        assert "    def indented():" in result
        assert "        pass" in result

    def test_inline_code_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert "`pytest -v`" in result
        assert "`tests/`" in result
        assert "`tests/conftest.py`" in result

    def test_code_only_not_compressed(self, code_only):
        result = compress_memory_content(code_only, depth="aggressive")
        assert result.strip() == code_only.strip()


# ---------------------------------------------------------------------------
# URL preservation
# ---------------------------------------------------------------------------

class TestUrlPreservation:
    def test_bare_url_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert "https://docs.pytest.org" in result

    def test_markdown_link_intact(self):
        text = "See [pytest docs](https://docs.pytest.org) for details."
        result = compress_memory_content(text, depth="aggressive")
        assert "[pytest docs](https://docs.pytest.org)" in result


# ---------------------------------------------------------------------------
# File path preservation
# ---------------------------------------------------------------------------

class TestPathPreservation:
    def test_absolute_path_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert "/Users/emmahyde/projects/memesis/tests/conftest.py" in result

    def test_relative_path_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert "./tests/test_models.py" in result

    def test_parent_path_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert "../shared/fixtures.py" in result


# ---------------------------------------------------------------------------
# Number / date / version preservation
# ---------------------------------------------------------------------------

class TestNumberPreservation:
    def test_iso_date_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert "2024-03-15" in result

    def test_version_number_intact(self, sample_memory):
        result = compress_memory_content(sample_memory, depth="aggressive")
        assert "v2.1.3" in result

    def test_standalone_numbers_intact(self):
        text = "There are 42 tests and 3.14 coverage."
        result = compress_memory_content(text, depth="aggressive")
        assert "42" in result
        assert "3.14" in result

    def test_numbers_with_units_intact(self):
        text = "Memory limit is 16gb and timeout is 500ms."
        result = compress_memory_content(text, depth="aggressive")
        assert "16gb" in result.lower()
        assert "500ms" in result.lower()


# ---------------------------------------------------------------------------
# Command preservation
# ---------------------------------------------------------------------------

class TestCommandPreservation:
    def test_git_command_intact(self):
        text = "Run git commit -m 'message' before pushing."
        result = compress_memory_content(text, depth="aggressive")
        assert "git commit -m" in result

    def test_npm_command_intact(self):
        text = "Use npm install to add dependencies."
        result = compress_memory_content(text, depth="aggressive")
        assert "npm install" in result

    def test_pytest_command_intact(self):
        text = "Run pytest -x to fail fast."
        result = compress_memory_content(text, depth="aggressive")
        assert "pytest -x" in result


# ---------------------------------------------------------------------------
# Depth levels
# ---------------------------------------------------------------------------

class TestDepthLevels:
    def test_lite_less_aggressive_than_moderate(self):
        text = "The system is running and has been tested extensively."
        lite = compress_memory_content(text, depth="lite")
        moderate = compress_memory_content(text, depth="moderate")
        assert len(lite) >= len(moderate)

    def test_moderate_less_aggressive_than_aggressive(self):
        text = "The user was working on a large number of bugs and was using pytest."
        moderate = compress_memory_content(text, depth="moderate")
        aggressive = compress_memory_content(text, depth="aggressive")
        assert len(moderate) >= len(aggressive)

    def test_lite_drops_articles_and_fillers(self):
        text = "The cat sat on a mat. You should use pytest."
        result = compress_memory_content(text, depth="lite")
        assert "the" not in result.lower().split()
        assert "you should" not in result.lower()

    def test_moderate_drops_auxiliaries(self):
        text = "The system is running and has been tested."
        result = compress_memory_content(text, depth="moderate")
        assert "is" not in result.lower().split()
        assert "has" not in result.lower().split()
        assert "been" not in result.lower().split()

    def test_aggressive_shifts_tense(self):
        text = "The bug was fixed and the user was working on tests."
        result = compress_memory_content(text, depth="aggressive")
        assert "was fixed" not in result.lower()
        assert "was working" not in result.lower()

    def test_aggressive_merges_redundant_bullets(self):
        text = """- Use pytest for testing.
- Use pytest for all tests.
- Something else entirely."""
        result = compress_memory_content(text, depth="aggressive")
        # Two redundant bullets should merge to one
        bullet_count = result.count("- ")
        assert bullet_count <= 2

    def test_aggressive_drops_parentheticals(self):
        text = "The system (which is very old) needs an update."
        result = compress_memory_content(text, depth="aggressive")
        assert "(which is very old)" not in result


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class TestValidation:
    def test_valid_compression_passes(self, sample_memory):
        compressed = compress_memory_content(sample_memory, depth="moderate")
        result = validate_compression(sample_memory, compressed, depth="moderate")
        assert result.is_valid
        assert len(result.errors) == 0

    def test_frontmatter_modification_fails(self, sample_memory):
        compressed = compress_memory_content(sample_memory, depth="moderate")
        # Tamper with frontmatter
        tampered = compressed.replace("name: Python Testing Patterns", "name: X")
        result = validate_compression(sample_memory, tampered, depth="moderate")
        assert not result.is_valid
        assert any("frontmatter" in e.lower() for e in result.errors)

    def test_code_block_loss_fails(self, sample_memory):
        compressed = compress_memory_content(sample_memory, depth="moderate")
        tampered = compressed.replace("```bash", "")
        result = validate_compression(sample_memory, tampered, depth="moderate")
        assert not result.is_valid
        assert any("code" in e.lower() for e in result.errors)

    def test_url_loss_fails(self, sample_memory):
        compressed = compress_memory_content(sample_memory, depth="moderate")
        tampered = compressed.replace("https://docs.pytest.org", "")
        result = validate_compression(sample_memory, tampered, depth="moderate")
        assert not result.is_valid
        assert any("url" in e.lower() for e in result.errors)

    def test_path_loss_fails(self, sample_memory):
        compressed = compress_memory_content(sample_memory, depth="moderate")
        tampered = compressed.replace("./tests/test_models.py", "")
        result = validate_compression(sample_memory, tampered, depth="moderate")
        assert not result.is_valid
        assert any("path" in e.lower() for e in result.errors)

    def test_no_compression_fails(self):
        text = "Short."
        result = validate_compression(text, text, depth="moderate")
        assert not result.is_valid
        assert any("did not reduce" in e.lower() for e in result.errors)

    def test_validation_result_fields(self, sample_memory):
        compressed = compress_memory_content(sample_memory, depth="moderate")
        result = validate_compression(sample_memory, compressed, depth="moderate")
        assert isinstance(result, ValidationResult)
        assert result.original_size > 0
        assert result.compressed_size > 0
        assert result.compression_ratio > 0
        assert isinstance(result.errors, list)
        assert isinstance(result.warnings, list)

    def test_lite_ratio_warning(self):
        text = "The cat sat."
        compressed = compress_memory_content(text, depth="lite")
        result = validate_compression(text, compressed, depth="lite")
        # Very short text may not achieve 1.05x
        if result.compression_ratio < 1.05:
            assert any("1.05x" in w for w in result.warnings)

    def test_moderate_ratio_warning(self):
        text = "The system is running and has been tested extensively."
        compressed = compress_memory_content(text, depth="moderate")
        result = validate_compression(text, compressed, depth="moderate")
        if result.compression_ratio < 1.1:
            assert any("1.1x" in w for w in result.warnings)

    def test_aggressive_ratio_warning(self):
        text = "The user was working on a large number of bugs."
        compressed = compress_memory_content(text, depth="aggressive")
        result = validate_compression(text, compressed, depth="aggressive")
        if result.compression_ratio < 1.2:
            assert any("1.2x" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_only_frontmatter(self, frontmatter_only):
        result = compress_memory_content(frontmatter_only, depth="aggressive")
        assert result.strip() == frontmatter_only.strip()

    def test_only_code_blocks(self, code_only):
        result = compress_memory_content(code_only, depth="aggressive")
        assert result.strip() == code_only.strip()

    def test_empty_body_after_frontmatter(self):
        text = """---
name: Title
type: memory
---
"""
        result = compress_memory_content(text, depth="aggressive")
        assert result.strip() == text.strip()

    def test_single_word(self):
        text = "Hello"
        result = compress_memory_content(text, depth="aggressive")
        assert result == text  # Nothing to compress

    def test_all_protected_content(self):
        text = "`code` https://example.com /path/to/file 2024-01-01 v1.0"
        result = compress_memory_content(text, depth="aggressive")
        assert "`code`" in result
        assert "https://example.com" in result
        assert "/path/to/file" in result
        assert "2024-01-01" in result
        assert "v1.0" in result

    def test_markdown_headers_preserved(self):
        text = "# Header\n## Subheader\nContent here."
        result = compress_memory_content(text, depth="aggressive")
        assert "# Header" in result
        assert "## Subheader" in result

    def test_blockquotes_preserved(self):
        text = "> This is a quote.\n> Another line."
        result = compress_memory_content(text, depth="aggressive")
        assert "> This is a quote." in result
        assert "> Another line." in result

    def test_horizontal_rule_preserved(self):
        text = "Before\n\n---\n\nAfter"
        result = compress_memory_content(text, depth="aggressive")
        assert "---" in result


# ---------------------------------------------------------------------------
# Compression ratio targets
# ---------------------------------------------------------------------------

class TestCompressionRatio:
    def test_moderate_achieves_target_on_realistic_content(self, sample_memory):
        compressed = compress_memory_content(sample_memory, depth="moderate")
        ratio = compression_ratio(sample_memory, compressed)
        assert ratio >= 1.1, f"Moderate compression only achieved {ratio:.2f}x"

    def test_aggressive_achieves_target_on_realistic_content(self, sample_memory):
        compressed = compress_memory_content(sample_memory, depth="aggressive")
        ratio = compression_ratio(sample_memory, compressed)
        assert ratio >= 1.1, f"Aggressive compression only achieved {ratio:.2f}x"

    def test_aggressive_on_long_text(self):
        text = """The system is running and has been tested extensively.
You should always use pytest for all testing in this project.
Make sure to run the tests before committing any changes.
It is important to note that the user prefers snake_case.
The project was implemented a solution for many bugs.
Due to the fact that there are a large number of tests,
at this point in time we have approximately 100% coverage.
Prior to merging, subsequent to review, all checks must pass.
The user was working on a large number of bugs and was using pytest.
"""
        compressed = compress_memory_content(text, depth="aggressive")
        ratio = compression_ratio(text, compressed)
        assert ratio >= 1.25, f"Aggressive on long text only achieved {ratio:.2f}x"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

class TestInternalHelpers:
    def test_split_frontmatter_with_frontmatter(self):
        text = "---\nname: X\n---\n\nBody"
        fm, body = _split_frontmatter(text)
        assert fm == "---\nname: X\n---"
        assert body == "\nBody"

    def test_split_frontmatter_without_frontmatter(self):
        text = "Just body text."
        fm, body = _split_frontmatter(text)
        assert fm == ""
        assert body == "Just body text."

    def test_split_frontmatter_no_closing_delimiter(self):
        text = "---\nname: X\n\nBody"
        fm, body = _split_frontmatter(text)
        assert fm == ""
        assert body == text

    def test_drop_words(self):
        text = "The cat and the dog"
        result = _drop_words(text, {"the", "and"})
        assert "the" not in result.lower()
        assert "and" not in result.lower()
        assert "cat" in result
        assert "dog" in result

    def test_replace_verbose(self):
        text = "We need to utilize this tool."
        result = _replace_verbose(text)
        assert "utilize" not in result.lower()
        assert "use" in result.lower()

    def test_drop_throat_clearing(self):
        text = "You should always test first."
        result = _drop_throat_clearing(text)
        assert "you should" not in result.lower()

    def test_merge_redundant_bullets(self):
        text = """- Use pytest for testing.
- Use pytest for all tests.
- Different point."""
        result = _merge_redundant_bullets(text)
        assert result.count("- ") <= 2

    def test_shift_simple_present(self):
        text = "The bug was being fixed and they were working on tests."
        result = _shift_simple_present(text)
        assert "was being" not in result.lower()
        assert "is being" in result.lower() or "being" in result.lower()
        assert "were working" not in result.lower()
        assert "are working" in result.lower()

    def test_clean_extra_whitespace(self):
        text = "  hello   world  \n\n\n  foo  bar  "
        result = _clean_extra_whitespace(text)
        assert "  " not in result
        assert result == "hello world\nfoo bar"

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

    def test_extract_file_paths(self):
        text = "Check /src/main.py and ./config.yaml."
        pe = _ProtectedElements()
        result = _extract_file_paths(text, pe)
        assert "/src/main.py" not in result
        assert "./config.yaml" not in result
        assert len(pe.placeholders) == 2

    def test_extract_commands(self):
        text = "Run git commit -m 'msg' and npm install."
        pe = _ProtectedElements()
        result = _extract_commands(text, pe)
        assert "git commit" not in result
        assert "npm install" not in result
        assert len(pe.placeholders) == 2

    def test_extract_dates_and_numbers(self):
        text = "Date 2024-01-15 and 3.14 and 16gb."
        pe = _ProtectedElements()
        result = _extract_dates_and_numbers(text, pe)
        assert "2024-01-15" not in result
        assert "3.14" not in result
        assert "16gb" not in result.lower()
        assert len(pe.placeholders) == 3


# ---------------------------------------------------------------------------
# Stage-adaptive compression depth
# ---------------------------------------------------------------------------

class TestStageDepth:
    def test_ephemeral_returns_off(self):
        assert get_stage_depth("ephemeral") == "off"

    def test_consolidated_returns_lite(self):
        assert get_stage_depth("consolidated") == "lite"

    def test_crystallized_returns_moderate(self):
        assert get_stage_depth("crystallized") == "moderate"

    def test_instinctive_returns_aggressive(self):
        assert get_stage_depth("instinctive") == "aggressive"

    def test_unknown_stage_raises(self):
        with pytest.raises(ValueError):
            get_stage_depth("unknown")

    def test_env_override_honored(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_COMPRESSION_DEPTH", "aggressive")
        assert get_stage_depth("ephemeral") == "aggressive"
        assert get_stage_depth("consolidated") == "aggressive"

    def test_env_override_off(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_COMPRESSION_DEPTH", "off")
        assert get_stage_depth("instinctive") == "off"

    def test_env_override_invalid_raises(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_COMPRESSION_DEPTH", "extreme")
        with pytest.raises(ValueError):
            get_stage_depth("consolidated")

    def test_env_override_unset_uses_stage_default(self, monkeypatch):
        monkeypatch.delenv("MEMESIS_COMPRESSION_DEPTH", raising=False)
        assert get_stage_depth("consolidated") == "lite"
        assert get_stage_depth("crystallized") == "moderate"


class TestCompressMemoryForStage:
    def test_ephemeral_returns_uncompressed(self):
        text = "The quick brown fox jumps over the lazy dog."
        result = compress_memory_for_stage(text, "ephemeral")
        assert result == text

    def test_consolidated_uses_lite(self):
        text = "The cat sat on a mat and looked at an apple."
        result = compress_memory_for_stage(text, "consolidated")
        assert "the" not in result.lower().split()

    def test_crystallized_uses_moderate(self):
        text = "The system is running and has been tested."
        result = compress_memory_for_stage(text, "crystallized")
        assert "is" not in result.lower().split()
        assert "has" not in result.lower().split()

    def test_instinctive_uses_aggressive(self):
        text = "The bug was fixed and the user was working on tests."
        result = compress_memory_for_stage(text, "instinctive")
        assert "was fixed" not in result.lower()

    def test_env_override_overrides_stage(self, monkeypatch):
        monkeypatch.setenv("MEMESIS_COMPRESSION_DEPTH", "off")
        text = "The cat sat on a mat."
        result = compress_memory_for_stage(text, "instinctive")
        assert result == text


class TestBrevityCode:
    def test_abbreviates_frontmatter_keys(self):
        content = """---
name: Python Testing Patterns
description: pytest conventions for this project
type: memory
---

You should always use pytest for all testing in this project.
"""
        result = compress_to_brevity_code(content)
        assert result.startswith("---")
        assert "nm:" in result
        assert "dsc:" in result
        assert "typ:" in result
        assert "name:" not in result
        assert "description:" not in result

    def test_codebook_encodes_values(self):
        content = """---
name: Python Testing Patterns
description: Use pytest for testing Python code
type: memory
---

Some body text.
"""
        result = compress_to_brevity_code(content)
        assert "py" in result.lower()

    def test_no_trailing_whitespace(self):
        content = """---
name: Title
description: Summary
type: memory
---

Body text here.
"""
        result = compress_to_brevity_code(content)
        assert not result.endswith(" ")
        assert not result.endswith("\n")

    def test_compact_body(self):
        content = """---
name: Title
description: Summary
type: memory
---

The system is running and has been tested extensively.
"""
        result = compress_to_brevity_code(content)
        lines = result.splitlines()
        body_lines = [l for l in lines if not l.startswith("---") and l.strip()]
        assert body_lines
        assert "is" not in body_lines[-1].lower().split()

    def test_no_frontmatter_works(self):
        content = "The system is running and has been tested."
        result = compress_to_brevity_code(content)
        assert "is" not in result.lower().split()

    def test_empty_body(self):
        content = """---
name: Title
description: Summary
type: memory
---
"""
        result = compress_to_brevity_code(content)
        assert "nm:" in result
        assert "---" in result
