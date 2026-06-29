"""Extract runnable code from a model's response (SPEC §5).

Priority: python-fenced blocks -> any-fenced blocks -> the whole response.
An empty result sets failed=True, which the scorer treats as distinct from a
wrong answer (extraction failure != the model getting the task wrong).
"""
from __future__ import annotations

import re

from .models import ExtractionResult

# ```<info>\n<body>``` ; info line may carry a language tag and extras.
_FENCE = re.compile(r"```([^\n`]*)\n(.*?)```", re.DOTALL)
_PY_LANGS = {"python", "py", "python3"}


def _blocks(response: str) -> list[tuple[str, str]]:
    blocks: list[tuple[str, str]] = []
    for info, body in _FENCE.findall(response):
        toks = info.strip().split()
        lang = toks[0].lower() if toks else ""
        blocks.append((lang, body))
    return blocks


def _join(bodies: list[str]) -> str:
    return "\n\n".join(b.strip("\n") for b in bodies).strip()


def extract(response: str) -> ExtractionResult:
    if not response or not response.strip():
        return ExtractionResult(code="", failed=True, method="none")

    blocks = _blocks(response)

    py = _join([body for lang, body in blocks if lang in _PY_LANGS])
    if py:
        return ExtractionResult(code=py, method="fenced-python")

    anyb = _join([body for _, body in blocks])
    if anyb:
        return ExtractionResult(code=anyb, method="fenced-any")

    whole = response.strip()
    if whole:
        return ExtractionResult(code=whole, method="whole")

    return ExtractionResult(code="", failed=True, method="none")
