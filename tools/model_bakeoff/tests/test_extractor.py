"""extractor.extract() behaviour (SPEC §5). Offline, pure."""
from __future__ import annotations

from tools.model_bakeoff import extractor


def test_python_fence_extracted_and_prose_stripped():
    resp = "Here is my solution:\n\n```python\ndef f(x):\n    return x + 1\n```\nDone."
    r = extractor.extract(resp)
    assert r.method == "fenced-python"
    assert not r.failed
    assert r.code == "def f(x):\n    return x + 1"
    assert "Here is my solution" not in r.code


def test_python_fence_with_extra_info_on_fence_line():
    resp = "```python title=sol.py\nx = 1\n```"
    r = extractor.extract(resp)
    assert r.method == "fenced-python"
    assert r.code == "x = 1"


def test_multiple_python_blocks_concatenated_in_order():
    resp = "```python\nimport math\n```\nand\n```python\ndef g():\n    return math.pi\n```"
    r = extractor.extract(resp)
    assert r.method == "fenced-python"
    assert r.code == "import math\n\ndef g():\n    return math.pi"


def test_generic_fence_when_no_language_tag():
    resp = "```\ndef h():\n    return 2\n```"
    r = extractor.extract(resp)
    assert r.method == "fenced-any"
    assert r.code == "def h():\n    return 2"


def test_python_fence_preferred_over_generic():
    resp = "```\nnot code\n```\n```python\nreal = 1\n```"
    r = extractor.extract(resp)
    assert r.method == "fenced-python"
    assert r.code == "real = 1"


def test_no_fence_returns_whole_response():
    resp = "def solo():\n    return 3"
    r = extractor.extract(resp)
    assert r.method == "whole"
    assert r.code == "def solo():\n    return 3"
    assert not r.failed


def test_empty_response_fails():
    r = extractor.extract("")
    assert r.failed
    assert r.method == "none"
    assert r.code == ""


def test_whitespace_only_response_fails():
    r = extractor.extract("   \n\t  \n")
    assert r.failed
    assert r.method == "none"
