"""
Shared LLM transport — client selection and response cleaning.

Centralizes the Anthropic API call pattern used across consolidator,
crystallizer, threads, and self_reflection.  Handles Bedrock vs direct
client selection and markdown fence stripping.  Does NOT parse JSON or
implement retry — those are caller responsibilities because the expected
response shape and error policy differ per caller.
"""

import os

import anthropic

# Model constants — one place to update when the model changes.
DEFAULT_MODEL = "claude-sonnet-4-6"
BEDROCK_MODEL = "us.anthropic.claude-sonnet-4-6"


def strip_markdown_fences(text: str) -> str:
    """
    Remove markdown code fences from LLM response text.

    Handles both ````` ```json ````` and bare ````` ``` ````` opening fences.
    Returns the text unchanged if no fences are present.

    Args:
        text: Raw text from the LLM, possibly wrapped in fences.

    Returns:
        Cleaned text with fences stripped.
    """
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        # Remove opening fence (```json or ```)
        lines = lines[1:]
        # Remove closing fence
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


def _make_client():
    """
    Create the appropriate Anthropic client based on environment.

    Returns:
        anthropic.AnthropicBedrock if CLAUDE_CODE_USE_BEDROCK is set,
        anthropic.Anthropic otherwise.
    """
    if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
        return anthropic.AnthropicBedrock()
    return anthropic.Anthropic()


def call_llm(
    prompt: str,
    *,
    max_tokens: int = 1024,
    temperature: float = 0,
    model: str | None = None,
) -> str:
    """
    Call the Anthropic Messages API and return the response text.

    Handles client selection (Bedrock vs direct) and strips markdown
    fences from the response.  Does NOT parse JSON or retry — callers
    own their response parsing and error handling.

    Args:
        prompt: The full prompt to send.
        max_tokens: Maximum tokens in the response.
        temperature: Sampling temperature (0 for deterministic).
        model: Override model ID.  If None, selects based on
               CLAUDE_CODE_USE_BEDROCK env var.

    Returns:
        The response text with markdown fences stripped.

    Raises:
        anthropic.APIError: On API failures (propagated, not caught).
    """
    client = _make_client()

    if model is None:
        if os.environ.get("CLAUDE_CODE_USE_BEDROCK"):
            model = BEDROCK_MODEL
        else:
            model = DEFAULT_MODEL

    response = client.messages.create(
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = response.content[0].text
    return strip_markdown_fences(raw)
