"""Buffer-level regression tests for the Shift+letter / shifted-symbol leak.

Issue #87390 / #86866: mapping ``ESC[27;2;<cp>~`` -> ``'A'`` in
``ANSI_SEQUENCES`` decodes the KEY, but prompt_toolkit's default
``Keys.Any`` self-insert binding inserts ``event.data`` — the RAW matched
bytes, not the decoded character. So Shift+A typed ``[27;2;65~`` into the
prompt despite the mapping.

These tests drive a real ``Application`` + ``TextArea`` over
``create_pipe_input()`` and assert the final buffer text — the string the
user would have submitted. Key-level assertions cannot see this class of
bug (they pass before and after the fix), which is exactly how the
parse-level suite in ``test_modify_other_keys_aliases.py`` missed it.

The ``_install_literal_key_data_patch`` fixture is per-test because the
patch is a class-level monkeypatch on ``Vt100Parser``; the autouse
fixture in the sibling test file restores only ``ANSI_SEQUENCES``.
"""

from __future__ import annotations

import asyncio

import pytest
import prompt_toolkit.input.vt100_parser as vt100_mod

from prompt_toolkit.application import Application
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout
from prompt_toolkit.output import DummyOutput
from prompt_toolkit.widgets import TextArea

from hermes_cli.pt_input_extras import install_literal_key_data_patch


# Pristine prompt_toolkit handler, captured at import time before any
# fixture installs the patch — used by the negative control.
_PRISTINE_CALL_HANDLER = vt100_mod.Vt100Parser._call_handler


@pytest.fixture(autouse=True)
def _aliases_and_data_patch():
    """Mirror the CLI startup installs (cli.py:79-94) — alias tables AND
    the parser data patch — then restore both. The sibling file's autouse
    fixture restores only ANSI_SEQUENCES; the class-level _call_handler
    patch must be undone here or it leaks across tests."""
    from prompt_toolkit.input.ansi_escape_sequences import ANSI_SEQUENCES

    from hermes_cli.pt_input_extras import (
        install_cmd_backspace_alias,
        install_ctrl_enter_alias,
        install_ignored_terminal_sequences,
        install_modify_other_keys_aliases,
        install_shift_enter_alias,
    )

    saved = dict(ANSI_SEQUENCES)
    install_shift_enter_alias()
    install_ctrl_enter_alias()
    install_cmd_backspace_alias()
    install_modify_other_keys_aliases()  # also installs the data patch
    install_ignored_terminal_sequences()
    # The install above already applied the data patch; verify it landed
    # (idempotent re-install returns False by design).
    assert getattr(
        vt100_mod.Vt100Parser._call_handler, "_hermes_literal_key_data", False
    ), "parser data patch must be live in the test env"
    yield
    ANSI_SEQUENCES.clear()
    ANSI_SEQUENCES.update(saved)
    vt100_mod.Vt100Parser._call_handler = _PRISTINE_CALL_HANDLER


def _submitted(seq: str) -> str:
    """Type ``seq`` through a real Application + TextArea and return the
    resulting buffer text — exactly what the user would have submitted."""
    ta = TextArea(multiline=True)
    kb = KeyBindings()  # mirrors Hermes: custom kb, defaults merged by Application

    async def main() -> str:
        with create_pipe_input() as pipe:
            app = Application(
                layout=Layout(ta),
                key_bindings=kb,
                input=pipe,
                output=DummyOutput(),
                full_screen=False,
            )

            async def drive() -> None:
                pipe.send_text(seq)
                await asyncio.sleep(0.15)
                app.exit()

            t = asyncio.ensure_future(drive())
            await app.run_async()
            await t
        return ta.text

    return asyncio.run(main())


# ---------------------------------------------------------------------------
# Shift+letter — the reported bug (#87390)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "seq,char",
    [
        ("\x1b[27;2;65~", "A"),   # Shift+A — already-shifted codepoint (Ghostty)
        ("\x1b[27;2;97~", "A"),   # Shift+a — unshifted codepoint (other emitters)
        ("\x1b[27;2;87~", "W"),   # Shift+W from the original issue report
        ("\x1b[27;2;113~", "Q"),  # Shift+q — kitty-style unshifted cp
        ("\x1b[113;2u", "Q"),     # kitty CSI-u form
    ],
)
def test_shift_letter_types_capital(seq, char):
    """Shift+letter must type the capital letter, not the escape sequence."""
    assert _submitted(seq) == char


def test_shift_letter_sentence():
    """A full sentence with capitals must come through cleanly — the
    'Hello World' reproduction from the upstream fix."""
    assert _submitted("\x1b[27;2;72~ello \x1b[27;2;87~orld") == "Hello World"


# ---------------------------------------------------------------------------
# Shifted symbols — Ghostty escapes any codepoint in 0x40-0x7F under Shift
# (src/input/key_encode.zig, v1.3.1); the tilde codepoint is the
# already-shifted layout-resolved text, so cp -> chr(cp) is correct.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cp,char",
    [
        (64, "@"), (91, "["), (92, "\\"), (93, "]"), (94, "^"), (95, "_"),
        (96, "`"), (123, "{"), (124, "|"), (125, "}"), (126, "~"),
    ],
)
def test_shifted_symbol_types_character(cp, char):
    """Shift+symbol must type the symbol, not the escape sequence."""
    assert _submitted(f"\x1b[27;2;{cp}~") == char


# ---------------------------------------------------------------------------
# Hold-the-line cases — must remain untouched by the data patch
# ---------------------------------------------------------------------------

def test_plain_typing_unchanged():
    assert _submitted("hi there") == "hi there"


def test_ctrl_c_never_leaks_raw_bytes():
    """Ctrl+C is a Keys enum (not a str), so the patch must not touch it —
    and the raw CSI bytes must never land in the buffer."""
    result = _submitted("\x1b[27;5;99~")
    assert "\x1b[27;5;99~" not in result
    assert "[27;5;99~" not in result


def test_enter_in_multiline_inserts_newline():
    """Enter in a multiline TextArea inserts a newline (Hermes uses
    multiline=True at cli.py:18967) — unaffected by the patch."""
    assert _submitted("\r") == "\n"


def test_shift_enter_alias_preserved():
    """Shift+Enter maps to the (Escape, ControlM) tuple — the raw CSI
    bytes must never land in the buffer."""
    assert "\x1b[27;2;13~" not in _submitted("\x1b[27;2;13~")


# ---------------------------------------------------------------------------
# Negative control: this test class must FAIL against unpatched code.
# ---------------------------------------------------------------------------

def test_negative_control_patch_is_what_makes_it_pass():
    """Without the data patch, Shift+A inserts the raw bytes — prove the
    harness can see the bug by swapping the pristine handler back in.
    (Aliases stay installed, so the full raw sequence including ESC lands
    in the buffer — matching the live symptom and the e2e probe.)"""
    vt100_mod.Vt100Parser._call_handler = _PRISTINE_CALL_HANDLER
    try:
        assert _submitted("\x1b[27;2;65~") == "\x1b[27;2;65~", (
            "negative control: unpatched parser must leak the raw sequence"
        )
    finally:
        # Re-apply the patch for the next test.
        install_literal_key_data_patch()
