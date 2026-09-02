import re

from retail_agent.errors import GuardrailBlockedError

_INJECTION_PATTERNS = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(ignore|disregard|forget)\b[^.\n]{0,40}\b(previous|prior|above|earlier)\b[^.\n]{0,20}\binstructions\b",
        r"\b(ignore|disregard|bypass)\b[^.\n]{0,20}\b(your|the)\b[^.\n]{0,20}"
        r"\b(rules|restrictions|instructions|guardrails|safeguards)\b",
        r"\byou are now\b",
        r"\bnew instructions\s*:",
        r"\bsystem prompt\b",
        r"\breveal (your|the) (system )?prompt\b",
        r"\bpretend (you are|to be)\b",
        r"\bdeveloper mode\b",
        r"\bjailbreak\b",
    )
)


def check_user_input(text: str) -> None:
    """Raise GuardrailBlockedError if text matches a known prompt-injection
    or jailbreak pattern.

    A regex/keyword denylist, not a semantic classifier — deliberately, to
    keep this a cheap, deterministic, zero-latency check ahead of the first
    model call (docs/design.md §3/§6). It catches the common, mechanical
    forms of "override your instructions" phrasing; it will not catch a
    subtler or paraphrased attempt. That residual risk is why tool output is
    also treated as untrusted data in the system prompt, rather than relying
    on this check alone to stop every injection path.

    Args:
        text: The raw user message for this turn.

    Raises:
        GuardrailBlockedError: `text` matches a known injection pattern.
    """
    for pattern in _INJECTION_PATTERNS:
        if pattern.search(text):
            raise GuardrailBlockedError(f"Blocked pattern: {pattern.pattern}")
