"""Tests for skill files in skills/.

Verifies that each skill SKILL.md exists with correct YAML frontmatter and
content conventions.  There is no executable logic to test — these are
structure/contract tests.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

SKILLS_DIR = Path(__file__).parent.parent / "skills"

SKILL_NAMES = [
    "backfill", "connect", "dashboard", "forget", "health",
    "ideate", "learn", "recall", "reflect", "stats", "teach",
    "threads", "usage",
]

INVOCATION_PREFIX = "/memesis:"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """
    Extract YAML frontmatter from a markdown file.

    Returns (metadata_dict, body) where metadata_dict maps key -> value
    (both stripped strings).  Returns ({}, text) if no frontmatter block.
    """
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return {}, text

    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return {}, text

    metadata: dict[str, str] = {}
    for line in lines[1:end_idx]:
        if ":" in line:
            key, value = line.split(":", 1)
            metadata[key.strip()] = value.strip()

    body = "\n".join(lines[end_idx + 1:]).strip()
    return metadata, body


# ---------------------------------------------------------------------------
# Parametrised: every skill file must exist
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_file_exists(name):
    """Each skill must exist at skills/<name>/SKILL.md."""
    skill_path = SKILLS_DIR / name / "SKILL.md"
    assert skill_path.exists(), f"Missing skill file: {skill_path}"


# ---------------------------------------------------------------------------
# Parametrised: frontmatter structure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_has_frontmatter(name):
    """Each skill file must open with a --- frontmatter block."""
    skill_path = SKILLS_DIR / name / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    assert content.startswith("---"), f"{name}/SKILL.md must start with '---' frontmatter"


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter_has_name_field(name):
    """Frontmatter must include a 'name' field."""
    skill_path = SKILLS_DIR / name / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    metadata, _ = _parse_frontmatter(content)
    assert "name" in metadata, f"{name}/SKILL.md frontmatter missing 'name' field"


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter_name_matches_filename(name):
    """Frontmatter 'name' field must match the filename (without .md)."""
    skill_path = SKILLS_DIR / name / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    metadata, _ = _parse_frontmatter(content)
    assert metadata.get("name") == name, (
        f"{name}/SKILL.md frontmatter name '{metadata.get('name')}' does not match skill directory '{name}'"
    )


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_frontmatter_has_description_field(name):
    """Frontmatter must include a non-empty 'description' field."""
    skill_path = SKILLS_DIR / name / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    metadata, _ = _parse_frontmatter(content)
    assert "description" in metadata, f"{name}/SKILL.md frontmatter missing 'description' field"
    assert metadata["description"], f"{name}/SKILL.md frontmatter 'description' must not be empty"


# ---------------------------------------------------------------------------
# Parametrised: invocation format
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_uses_full_invocation_path(name):
    """Body must reference /memesis:<name>, not shorthand."""
    skill_path = SKILLS_DIR / name / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(content)
    full_invocation = f"{INVOCATION_PREFIX}{name}"
    assert full_invocation in body, (
        f"{name}.md body must contain '{full_invocation}' (full invocation path)"
    )


@pytest.mark.parametrize("name", SKILL_NAMES)
def test_skill_no_shorthand_invocation(name):
    """Usage/example lines must use the full /memesis:<name> path, not bare /<name>."""
    skill_path = SKILLS_DIR / name / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    _, body = _parse_frontmatter(content)
    # Only flag lines that look like invocation calls (bullet items or code lines)
    # but not headings (which may read "# /learn — ..." for labelling purposes).
    invocation_lines_without_prefix = [
        line for line in body.split("\n")
        if f"/{name}" in line
        and INVOCATION_PREFIX not in line
        and not line.strip().startswith("#")  # headings are labels, not calls
    ]
    assert not invocation_lines_without_prefix, (
        f"{name}.md contains shorthand '/{name}' invocation without namespace prefix:\n"
        + "\n".join(invocation_lines_without_prefix)
    )


# ---------------------------------------------------------------------------
# Per-skill content checks
# ---------------------------------------------------------------------------


class TestLearnSkill:
    def test_learn_body_mentions_consolidated_stage(self):
        content = (SKILLS_DIR / "learn" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        assert "consolidated" in body.lower(), \
            "learn.md should mention the 'consolidated' memory stage"

    def test_learn_body_mentions_ephemeral_stage(self):
        content = (SKILLS_DIR / "learn" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        assert "ephemeral" in body.lower(), \
            "learn.md should mention the 'ephemeral' memory stage"

    def test_learn_body_has_examples_section(self):
        content = (SKILLS_DIR / "learn" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        assert "## Examples" in body or "## Example" in body, \
            "learn.md should have an Examples section"


class TestDashboardSkill:
    def _read_body(self):
        content = (SKILLS_DIR / "dashboard" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        return body

    def test_dashboard_body_mentions_stats(self):
        assert "stats" in self._read_body(), "dashboard.md should mention stats"

    def test_dashboard_body_mentions_health(self):
        assert "health" in self._read_body(), "dashboard.md should mention health"

    def test_dashboard_body_mentions_stages(self):
        body = self._read_body()
        for stage in ("instinctive", "crystallized", "consolidated"):
            assert stage in body, f"dashboard.md should mention the '{stage}' stage"


class TestForgetSkill:
    def test_forget_body_mentions_archived(self):
        content = (SKILLS_DIR / "forget" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        assert "archived" in body.lower(), \
            "forget.md should mention that memories are moved to 'archived/'"

    def test_forget_body_has_safety_section(self):
        content = (SKILLS_DIR / "forget" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        assert "## Safety" in body or "safety" in body.lower(), \
            "forget.md must include a Safety section"

    def test_forget_warns_on_instinctive_deletion(self):
        content = (SKILLS_DIR / "forget" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        assert "instinctive" in body.lower(), \
            "forget.md Safety section must warn about deleting instinctive memories"

    def test_forget_body_mentions_confirmation(self):
        content = (SKILLS_DIR / "forget" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        assert "confirm" in body.lower(), \
            "forget.md should require confirmation before deletion"

    def test_forget_body_has_examples_section(self):
        content = (SKILLS_DIR / "forget" / "SKILL.md").read_text(encoding="utf-8")
        _, body = _parse_frontmatter(content)
        assert "## Examples" in body or "## Example" in body, \
            "forget.md should have an Examples section"
