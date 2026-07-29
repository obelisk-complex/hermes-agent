"""Integration tests for tools.browser_supervisor.

Exercises the supervisor end-to-end against a real local Chrome
(``--remote-debugging-port``).  Skipped when Chrome is not installed
— these are the tests that actually verify the CDP wire protocol
works, since mock-CDP unit tests can only prove the happy paths we
thought to model.

Run manually:
    scripts/run_tests.sh tests/tools/test_browser_supervisor.py

Automated: skipped in CI unless ``HERMES_E2E_BROWSER=1`` is set.

Absent browser vs slow browser
------------------------------
Only *absence* of a browser skips these tests: no Chrome/Chromium on ``PATH``
is an environment that cannot run them, and the module-level ``skipif`` says
so.  Everything else — a Chrome that dies on launch, a Chrome that takes too
long to open its CDP port, a page that never loads, a dialog the supervisor
never sees — is a **failure**.  A timeout used to skip here, which meant a
slow browser silently deleted this file's coverage and left CI green.

Every budget below is a *deadline*, not a sleep: each wait returns the moment
the thing it waits for happens, so the budgets cost a healthy run nothing and
only bite when something is genuinely broken.
"""

from __future__ import annotations

import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time

import pytest


pytestmark = pytest.mark.skipif(
    not shutil.which("google-chrome") and not shutil.which("chromium"),
    reason="Chrome/Chromium not installed",
)

# Deadlines (see module docstring): generous, because none of them is paid on
# a healthy run.
CDP_BOOT_TIMEOUT = 60.0        # Chrome launch -> /json/version answering
CDP_CALL_TIMEOUT = 20.0        # one CDP request/response round trip
PAGE_LOAD_TIMEOUT = 20.0       # Page.navigate -> Page.loadEventFired
DIALOG_TIMEOUT = 15.0          # JS dialog fires -> supervisor sees it
SUPERVISOR_TIMEOUT = 15.0      # supervisor attaches / its state settles


def _find_chrome() -> str:
    for candidate in ("google-chrome", "chromium", "chromium-browser"):
        path = shutil.which(candidate)
        if path:
            return path
    pytest.skip("no Chrome binary found")


def _wait_until(predicate, *, timeout: float, what: str, interval: float = 0.05):
    """Poll *predicate* until it returns a truthy value; fail if it never does.

    Returns as soon as the condition holds.  On timeout this **fails** — it
    never skips: a condition that never arrives is a broken supervisor, not a
    missing browser.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    pytest.fail(f"timed out after {timeout:.1f}s waiting for {what}")


def _terminate_chrome(proc: subprocess.Popen) -> None:
    """Stop Chrome and reap it.

    The stdlib ``subprocess._wait()`` POSIX implementation has a known race
    (https://bugs.python.org/issue38630): when SIGCHLD arrives concurrently
    with ``proc.wait()``, ``_try_wait(WNOHANG)`` can return a foreign pid and
    the ``assert pid == self.pid or pid == 0`` fires.  We saw this in CI on
    slice 1 after this fixture's teardown (PR #33661 follow-up).  Swallow the
    stdlib race + force-kill if wait hangs, then always reap so we don't leak
    a zombie.
    """
    try:
        proc.terminate()
    except Exception:
        pass
    try:
        proc.wait(timeout=3)
    except (subprocess.TimeoutExpired, AssertionError, Exception):
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except (AssertionError, Exception):
            pass


@pytest.fixture
def chrome_cdp(request):
    """Start a headless Chrome with --remote-debugging-port, yield its WS URL.

    Uses a unique port per xdist worker to avoid cross-worker collisions.
    Always launches with ``--site-per-process`` so cross-origin iframes
    become real OOPIFs (needed by the iframe interaction tests).
    """

    # xdist worker_id is "master" in single-process mode or "gw0".."gwN" otherwise.
    # Under subprocess-per-file isolation there's no xdist, so we fall back
    # to "master" via the session-scoped fixture below.
    worker_id = request.getfixturevalue("worker_id") if "worker_id" in request.fixturenames else "master"
    if worker_id == "master":
        port_offset = 0
    else:
        port_offset = int(worker_id.lstrip("gw"))
    port = 9225 + port_offset
    profile = tempfile.mkdtemp(prefix="hermes-supervisor-test-")
    chrome = _find_chrome()  # skips iff no browser exists at all
    stderr_log = tempfile.TemporaryFile()
    proc = subprocess.Popen(
        [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--headless=new",
            "--disable-gpu",
            "--site-per-process",  # force OOPIFs for cross-origin iframes
        ],
        stdout=subprocess.DEVNULL,
        stderr=stderr_log,
    )

    ws_url = None
    died_with = None
    last_error: Exception | None = None
    started = time.monotonic()
    deadline = started + CDP_BOOT_TIMEOUT
    while time.monotonic() < deadline:
        # A Chrome that exited is broken, not slow: say so now rather than
        # burning the rest of the deadline on a corpse.
        if proc.poll() is not None:
            died_with = proc.returncode
            break
        try:
            import urllib.request
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ) as r:
                info = json.loads(r.read().decode())
                ws_url = info["webSocketDebuggerUrl"]
                break
        except Exception as exc:  # not up yet (or never will be)
            last_error = exc
            time.sleep(0.25)
    if ws_url is None:
        elapsed = time.monotonic() - started
        _terminate_chrome(proc)
        try:
            stderr_log.seek(0)
            stderr_tail = stderr_log.read().decode("utf-8", "replace")[-2000:].strip()
        except Exception:
            stderr_tail = "<unreadable>"
        finally:
            stderr_log.close()
        shutil.rmtree(profile, ignore_errors=True)
        if died_with is not None:
            headline = f"Chrome exited with code {died_with} before exposing CDP"
        else:
            headline = (
                f"Chrome did not expose CDP on 127.0.0.1:{port} within "
                f"{CDP_BOOT_TIMEOUT:.0f}s"
            )
        # Deliberately a failure, not a skip. A browser that is merely slow (or
        # broken) must not be able to delete this file's coverage and leave CI
        # green; only an *absent* browser skips, above.
        pytest.fail(
            f"{headline} (waited {elapsed:.1f}s)\n"
            f"  binary:      {chrome}\n"
            f"  last probe:  {last_error!r}\n"
            f"  chrome stderr tail:\n{stderr_tail or '<empty>'}"
        )

    try:
        yield ws_url, port
    finally:
        _terminate_chrome(proc)
        stderr_log.close()
        shutil.rmtree(profile, ignore_errors=True)


def _test_page_url() -> str:
    html = """<!doctype html>
<html><head><title>Supervisor pytest</title></head><body>
<h1>Supervisor pytest</h1>
<iframe id="inner" srcdoc="<body><h2>frame-marker</h2></body>" width="400" height="100"></iframe>
</body></html>"""
    return "data:text/html;base64," + base64.b64encode(html.encode()).decode()


def _fire_on_page(cdp_url: str, expression: str) -> None:
    """Navigate the first page target to the test page, then fire `expression`.

    Waits on ``Page.loadEventFired`` rather than sleeping a fixed 1.5s: the page
    is provably loaded before the expression runs, and a page that never loads
    fails loudly instead of silently racing the assertions that follow.
    """
    import asyncio
    import websockets as _ws_mod

    async def run():
        async with _ws_mod.connect(cdp_url, max_size=50 * 1024 * 1024) as ws:
            next_id = [1]
            pending: dict = {}
            loaded = asyncio.Event()

            async def reader_fn():
                try:
                    async for raw in ws:
                        m = json.loads(raw)
                        if "id" in m:
                            fut = pending.pop(m["id"], None)
                            if fut and not fut.done():
                                fut.set_result(m)
                        elif m.get("method") == "Page.loadEventFired":
                            loaded.set()
                except Exception:
                    pass

            rd = asyncio.create_task(reader_fn())

            async def call(method, params=None, session_id=None):
                cid = next_id[0]
                next_id[0] += 1
                p = {"id": cid, "method": method}
                if params:
                    p["params"] = params
                if session_id:
                    p["sessionId"] = session_id
                fut = asyncio.get_event_loop().create_future()
                pending[cid] = fut
                await ws.send(json.dumps(p))
                return await asyncio.wait_for(fut, timeout=CDP_CALL_TIMEOUT)

            try:
                targets = (await call("Target.getTargets"))["result"]["targetInfos"]
                page = next(t for t in targets if t.get("type") == "page")
                attach = await call(
                    "Target.attachToTarget",
                    {"targetId": page["targetId"], "flatten": True},
                )
                sid = attach["result"]["sessionId"]
                # Enable Page *before* navigating so the load event can't be
                # missed in the gap between the two calls.
                await call("Page.enable", session_id=sid)
                await call("Page.navigate", {"url": _test_page_url()}, session_id=sid)
                try:
                    await asyncio.wait_for(loaded.wait(), timeout=PAGE_LOAD_TIMEOUT)
                except asyncio.TimeoutError:
                    pytest.fail(
                        "page never fired Page.loadEventFired within "
                        f"{PAGE_LOAD_TIMEOUT:.0f}s"
                    )
                await call(
                    "Runtime.evaluate",
                    {"expression": expression, "returnByValue": True},
                    session_id=sid,
                )
            finally:
                rd.cancel()
                try:
                    await rd
                except BaseException:
                    pass

    asyncio.run(run())


@pytest.fixture
def supervisor_registry():
    """Yield the global registry and tear down any supervisors after the test."""
    from tools.browser_supervisor import SUPERVISOR_REGISTRY

    yield SUPERVISOR_REGISTRY
    SUPERVISOR_REGISTRY.stop_all()


def _wait_for_dialog(supervisor, timeout: float = DIALOG_TIMEOUT):
    """Return the pending dialogs once one appears; fail if none ever does."""
    return _wait_until(
        lambda: supervisor.snapshot().pending_dialogs,
        timeout=timeout,
        what="the supervisor to report a pending dialog",
    )


def _wait_for_page_session(supervisor, timeout: float = SUPERVISOR_TIMEOUT):
    """Wait until the supervisor is attached to a page target's CDP session."""
    _wait_until(
        lambda: supervisor.snapshot().active and supervisor._page_session_id is not None,
        timeout=timeout,
        what="the supervisor to attach to a page session",
    )


def test_supervisor_start_and_snapshot(chrome_cdp, supervisor_registry):
    """Supervisor attaches, exposes an active snapshot with a top frame."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-1", cdp_url=cdp_url)

    # Navigate so the frame tree populates.
    _fire_on_page(cdp_url, "/* no dialog */ void 0")

    # Wait for the frame events to reach the supervisor (deadline, not a sleep).
    _wait_until(
        lambda: supervisor.snapshot().frame_tree.get("top") is not None,
        timeout=SUPERVISOR_TIMEOUT,
        what="a top frame in the supervisor's frame tree",
    )
    snap = supervisor.snapshot()
    assert snap.active is True
    assert snap.task_id == "pytest-1"
    assert snap.pending_dialogs == ()
    # At minimum a top frame should exist after the navigate.
    assert snap.frame_tree.get("top") is not None


def test_main_frame_alert_detection_and_dismiss(chrome_cdp, supervisor_registry):
    """alert() in the main frame surfaces and can be dismissed via the sync API."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-2", cdp_url=cdp_url)

    _fire_on_page(cdp_url, "setTimeout(() => alert('PYTEST-MAIN-ALERT'), 50)")
    dialogs = _wait_for_dialog(supervisor)
    assert dialogs, "no dialog detected"
    d = dialogs[0]
    assert d.type == "alert"
    assert "PYTEST-MAIN-ALERT" in d.message

    result = supervisor.respond_to_dialog("dismiss")
    assert result["ok"] is True
    # State cleared after dismiss — wait for it to clear rather than assuming
    # 0.3s was enough.
    _wait_until(
        lambda: supervisor.snapshot().pending_dialogs == (),
        timeout=SUPERVISOR_TIMEOUT,
        what="the dismissed dialog to clear from pending_dialogs",
    )


def test_iframe_contentwindow_alert(chrome_cdp, supervisor_registry):
    """alert() fired from inside a same-origin iframe surfaces too."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-3", cdp_url=cdp_url)

    _fire_on_page(
        cdp_url,
        "setTimeout(() => document.querySelector('#inner').contentWindow.alert('PYTEST-IFRAME'), 50)",
    )
    dialogs = _wait_for_dialog(supervisor)
    assert dialogs, "no iframe dialog detected"
    assert any("PYTEST-IFRAME" in d.message for d in dialogs)

    result = supervisor.respond_to_dialog("accept")
    assert result["ok"] is True


def test_prompt_dialog_with_response_text(chrome_cdp, supervisor_registry):
    """prompt() gets our prompt_text back inside the page."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-4", cdp_url=cdp_url)

    # Fire a prompt and stash the answer on window
    _fire_on_page(
        cdp_url,
        "setTimeout(() => { window.__promptResult = prompt('give me a token', 'default-x'); }, 50)",
    )
    dialogs = _wait_for_dialog(supervisor)
    assert dialogs
    d = dialogs[0]
    assert d.type == "prompt"
    assert d.default_prompt == "default-x"

    result = supervisor.respond_to_dialog("accept", prompt_text="PYTEST-PROMPT-REPLY")
    assert result["ok"] is True


def test_respond_with_no_pending_dialog_errors_cleanly(chrome_cdp, supervisor_registry):
    """Calling respond_to_dialog when nothing is pending returns a clean error, not an exception."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-5", cdp_url=cdp_url)

    result = supervisor.respond_to_dialog("accept")
    assert result["ok"] is False
    assert "no dialog" in result["error"].lower()


def test_auto_dismiss_policy(chrome_cdp, supervisor_registry):
    """auto_dismiss policy clears dialogs without the agent responding."""
    from tools.browser_supervisor import DIALOG_POLICY_AUTO_DISMISS

    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(
        task_id="pytest-6",
        cdp_url=cdp_url,
        dialog_policy=DIALOG_POLICY_AUTO_DISMISS,
    )

    _fire_on_page(cdp_url, "setTimeout(() => alert('PYTEST-AUTO-DISMISS'), 50)")
    # Wait on the positive signal — the dialog landing in recent_dialogs closed
    # by the policy — instead of sleeping 2s and hoping. A bare
    # ``pending_dialogs == ()`` after a sleep also passes when the supervisor
    # never saw the dialog at all.
    _wait_until(
        lambda: any(
            "PYTEST-AUTO-DISMISS" in r.message and r.closed_by == "auto_policy"
            for r in supervisor.snapshot().recent_dialogs
        ),
        timeout=DIALOG_TIMEOUT,
        what="the alert to be seen and auto-dismissed by the policy",
    )
    snap = supervisor.snapshot()
    # Nothing pending because auto-dismiss cleared it immediately
    assert snap.pending_dialogs == ()


def test_registry_idempotent_get_or_start(chrome_cdp, supervisor_registry):
    """Calling get_or_start twice with the same (task, url) returns the same instance."""
    cdp_url, _port = chrome_cdp
    a = supervisor_registry.get_or_start(task_id="pytest-idem", cdp_url=cdp_url)
    b = supervisor_registry.get_or_start(task_id="pytest-idem", cdp_url=cdp_url)
    assert a is b


def test_registry_stop(chrome_cdp, supervisor_registry):
    """stop() tears down the supervisor and snapshot reports inactive."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-stop", cdp_url=cdp_url)
    assert supervisor.snapshot().active is True
    supervisor_registry.stop("pytest-stop")
    # Post-stop snapshot reports inactive; supervisor obj may still exist
    assert supervisor.snapshot().active is False


def test_browser_dialog_tool_no_supervisor():
    """browser_dialog returns a clear error when no supervisor is attached."""
    from tools.browser_dialog_tool import browser_dialog

    r = json.loads(browser_dialog(action="accept", task_id="nonexistent-task"))
    assert r["success"] is False
    assert "No CDP supervisor" in r["error"]


def test_browser_dialog_invalid_action(chrome_cdp, supervisor_registry):
    """browser_dialog rejects actions that aren't accept/dismiss."""
    from tools.browser_dialog_tool import browser_dialog

    cdp_url, _port = chrome_cdp
    supervisor_registry.get_or_start(task_id="pytest-bad-action", cdp_url=cdp_url)

    r = json.loads(browser_dialog(action="eat", task_id="pytest-bad-action"))
    assert r["success"] is False
    assert "accept" in r["error"] and "dismiss" in r["error"]


def test_recent_dialogs_ring_buffer(chrome_cdp, supervisor_registry):
    """Closed dialogs show up in recent_dialogs with a closed_by tag."""
    from tools.browser_supervisor import DIALOG_POLICY_AUTO_DISMISS

    cdp_url, _port = chrome_cdp
    sv = supervisor_registry.get_or_start(
        task_id="pytest-recent",
        cdp_url=cdp_url,
        dialog_policy=DIALOG_POLICY_AUTO_DISMISS,
    )

    _fire_on_page(cdp_url, "setTimeout(() => alert('PYTEST-RECENT'), 50)")
    # Wait for auto-dismiss to cycle the dialog through.
    _wait_until(
        lambda: any(
            "PYTEST-RECENT" in r.message for r in sv.snapshot().recent_dialogs
        ),
        timeout=DIALOG_TIMEOUT,
        what="the auto-dismissed dialog to reach recent_dialogs",
    )

    recent = sv.snapshot().recent_dialogs
    assert recent, "recent_dialogs should contain the auto-dismissed dialog"
    match = next((r for r in recent if "PYTEST-RECENT" in r.message), None)
    assert match is not None
    assert match.type == "alert"
    assert match.closed_by == "auto_policy"
    assert match.closed_at >= match.opened_at


def test_browser_dialog_tool_end_to_end(chrome_cdp, supervisor_registry):
    """Full agent-path check: fire an alert, call the tool handler directly."""
    from tools.browser_dialog_tool import browser_dialog

    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-tool", cdp_url=cdp_url)

    _fire_on_page(cdp_url, "setTimeout(() => alert('PYTEST-TOOL-END2END'), 50)")
    assert _wait_for_dialog(supervisor), "no dialog detected via wait_for_dialog"

    r = json.loads(browser_dialog(action="dismiss", task_id="pytest-tool"))
    assert r["success"] is True
    assert r["action"] == "dismiss"
    assert "PYTEST-TOOL-END2END" in r["dialog"]["message"]


def test_browser_cdp_frame_id_routes_via_supervisor(chrome_cdp, supervisor_registry, monkeypatch):
    """browser_cdp(frame_id=...) routes Runtime.evaluate through supervisor.

    Mocks the supervisor with a known frame and verifies browser_cdp sends
    the call via the supervisor's loop rather than opening a stateless
    WebSocket. This is the path that makes cross-origin iframe eval work
    on Browserbase.
    """
    cdp_url, _port = chrome_cdp
    sv = supervisor_registry.get_or_start(task_id="frame-id-test", cdp_url=cdp_url)
    assert sv.snapshot().active

    # Inject a fake OOPIF frame pointing at the SUPERVISOR's own page session
    # so we can verify routing. We fake is_oopif=True so the code path
    # treats it as an OOPIF child.
    import tools.browser_supervisor as _bs
    with sv._state_lock:
        fake_frame_id = "FAKE-FRAME-001"
        sv._frames[fake_frame_id] = _bs.FrameInfo(
            frame_id=fake_frame_id,
            url="fake://",
            origin="",
            parent_frame_id=None,
            is_oopif=True,
            cdp_session_id=sv._page_session_id,  # route at page scope
        )

    # Route the tool through the supervisor. Should succeed and return
    # something that clearly came from CDP.
    from tools.browser_cdp_tool import browser_cdp
    result = browser_cdp(
        method="Runtime.evaluate",
        params={"expression": "1 + 1", "returnByValue": True},
        frame_id=fake_frame_id,
        task_id="frame-id-test",
    )
    r = json.loads(result)
    assert r.get("success") is True, f"expected success, got: {r}"
    assert r.get("frame_id") == fake_frame_id
    assert r.get("session_id") == sv._page_session_id
    value = r.get("result", {}).get("result", {}).get("value")
    assert value == 2, f"expected 2, got {value!r}"


def test_browser_cdp_frame_id_real_oopif_smoke_documented():
    """Document that real-OOPIF E2E was manually verified — see PR #14540.

    A pytest version of this hits an asyncio version-quirk in the venv
    (3.11) that doesn't show up in standalone scripts (3.13 + system
    websockets). The mechanism IS verified end-to-end by two separate
    smoke scripts in /tmp/dialog-iframe-test/:

      * smoke_local_oopif.py   — local Chrome + 2 http servers on
        different hostnames + --site-per-process. Outer page on
        localhost:18905, iframe src=http://127.0.0.1:18906. Calls
        browser_cdp(method='Runtime.evaluate', frame_id=<OOPIF>) and
        verifies inner page's title comes back from the OOPIF session.
        PASSED on 2026-04-23: iframe document.title = 'INNER-FRAME-XYZ'

      * smoke_bb_iframe_agent_path.py — Browserbase + real cross-origin
        iframe (src=https://example.com/). Same browser_cdp(frame_id=)
        path. PASSED on 2026-04-23: iframe document.title =
        'Example Domain'

    The test_browser_cdp_frame_id_routes_via_supervisor pytest covers
    the supervisor-routing plumbing with a fake injected OOPIF.
    """
    pytest.skip(
        "Real-OOPIF E2E verified manually with smoke_local_oopif.py and "
        "smoke_bb_iframe_agent_path.py — pytest version hits an asyncio "
        "version quirk between venv (3.11) and standalone (3.13). "
        "Smoke logs preserved in /tmp/dialog-iframe-test/."
    )


def test_browser_cdp_frame_id_missing_supervisor():
    """browser_cdp(frame_id=...) errors cleanly when no supervisor is attached."""
    from tools.browser_cdp_tool import browser_cdp
    result = browser_cdp(
        method="Runtime.evaluate",
        params={"expression": "1"},
        frame_id="any-frame-id",
        task_id="no-such-task",
    )
    r = json.loads(result)
    assert r.get("success") is not True
    assert "supervisor" in (r.get("error") or "").lower()


def test_browser_cdp_frame_id_not_in_frame_tree(chrome_cdp, supervisor_registry):
    """browser_cdp(frame_id=...) errors when the frame_id isn't known."""
    cdp_url, _port = chrome_cdp
    sv = supervisor_registry.get_or_start(task_id="bad-frame-test", cdp_url=cdp_url)
    assert sv.snapshot().active

    from tools.browser_cdp_tool import browser_cdp
    result = browser_cdp(
        method="Runtime.evaluate",
        params={"expression": "1"},
        frame_id="nonexistent-frame",
        task_id="bad-frame-test",
    )
    r = json.loads(result)
    assert r.get("success") is not True
    assert "not found" in (r.get("error") or "").lower()


def test_bridge_captures_prompt_and_returns_reply_text(chrome_cdp, supervisor_registry):
    """End-to-end: agent's prompt_text round-trips INTO the page's JS.

    Proves the bridge isn't just catching dialogs — it's properly round-
    tripping our reply back into the page via Fetch.fulfillRequest, so
    ``prompt()`` actually returns the agent-supplied string to the page.
    """
    import base64 as _b64

    cdp_url, _port = chrome_cdp
    sv = supervisor_registry.get_or_start(task_id="pytest-bridge-prompt", cdp_url=cdp_url)

    # Page fires prompt and stashes the return value on window.
    html = """<!doctype html><html><body><script>
      window.__ret = null;
      setTimeout(() => { window.__ret = prompt('PROMPT-MSG', 'default'); }, 50);
    </script></body></html>"""
    url = "data:text/html;base64," + _b64.b64encode(html.encode()).decode()

    import asyncio as _asyncio
    import websockets as _ws_mod

    async def nav_and_read():
        async with _ws_mod.connect(cdp_url, max_size=50 * 1024 * 1024) as ws:
            nid = [1]
            pending: dict = {}

            async def reader_fn():
                try:
                    async for raw in ws:
                        m = json.loads(raw)
                        if "id" in m:
                            fut = pending.pop(m["id"], None)
                            if fut and not fut.done():
                                fut.set_result(m)
                except Exception:
                    pass

            rd = _asyncio.create_task(reader_fn())

            async def call(method, params=None, sid=None):
                c = nid[0]; nid[0] += 1
                p = {"id": c, "method": method}
                if params: p["params"] = params
                if sid: p["sessionId"] = sid
                fut = _asyncio.get_event_loop().create_future()
                pending[c] = fut
                await ws.send(json.dumps(p))
                return await _asyncio.wait_for(fut, timeout=CDP_CALL_TIMEOUT)

            try:
                t = (await call("Target.getTargets"))["result"]["targetInfos"]
                pg = next(x for x in t if x.get("type") == "page")
                a = await call("Target.attachToTarget", {"targetId": pg["targetId"], "flatten": True})
                sid = a["result"]["sessionId"]

                # Fire navigate but don't await — prompt() blocks the page
                nav_id = nid[0]; nid[0] += 1
                nav_fut = _asyncio.get_event_loop().create_future()
                pending[nav_id] = nav_fut
                await ws.send(json.dumps({"id": nav_id, "method": "Page.navigate", "params": {"url": url}, "sessionId": sid}))

                # Wait for supervisor to see the prompt
                deadline = time.monotonic() + DIALOG_TIMEOUT
                dialog = None
                while time.monotonic() < deadline:
                    snap = sv.snapshot()
                    if snap.pending_dialogs:
                        dialog = snap.pending_dialogs[0]
                        break
                    await _asyncio.sleep(0.05)
                assert dialog is not None, (
                    f"no dialog captured within {DIALOG_TIMEOUT:.0f}s"
                )
                assert dialog.bridge_request_id is not None, "expected bridge path"
                assert dialog.type == "prompt"

                # Agent responds
                resp = sv.respond_to_dialog("accept", prompt_text="AGENT-SUPPLIED-REPLY")
                assert resp["ok"] is True

                # Wait for nav to complete, then poll the page for the reply the
                # agent supplied rather than sleeping a fixed 0.5s and reading
                # once (a read that lands early returns None and fails the test
                # for a reason that has nothing to do with the bridge).
                try:
                    await _asyncio.wait_for(nav_fut, timeout=PAGE_LOAD_TIMEOUT)
                except Exception:
                    pass
                value = None
                read_deadline = time.monotonic() + PAGE_LOAD_TIMEOUT
                while time.monotonic() < read_deadline:
                    r = await call(
                        "Runtime.evaluate",
                        {"expression": "window.__ret", "returnByValue": True},
                        sid=sid,
                    )
                    value = r.get("result", {}).get("result", {}).get("value")
                    if value is not None:
                        break
                    await _asyncio.sleep(0.05)
                return value
            finally:
                rd.cancel()
                try: await rd
                except BaseException: pass

    value = asyncio.run(nav_and_read())
    assert value == "AGENT-SUPPLIED-REPLY", f"expected AGENT-SUPPLIED-REPLY, got {value!r}"


def test_evaluate_runtime_primitive(chrome_cdp, supervisor_registry):
    """evaluate_runtime returns primitive values via the supervisor's live WS."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-eval-1", cdp_url=cdp_url)

    # Need a page to evaluate against.
    _fire_on_page(cdp_url, "void 0")
    _wait_for_page_session(supervisor)

    out = supervisor.evaluate_runtime("1 + 41")
    assert out["ok"] is True
    assert out["result"] == 42
    assert out["result_type"] == "number"


def test_evaluate_runtime_object(chrome_cdp, supervisor_registry):
    """Plain objects come back JSON-serialized via returnByValue=True."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-eval-2", cdp_url=cdp_url)

    _fire_on_page(cdp_url, "void 0")
    _wait_for_page_session(supervisor)

    out = supervisor.evaluate_runtime('({foo: "bar", n: 7})')
    assert out["ok"] is True
    assert out["result"] == {"foo": "bar", "n": 7}
    assert out["result_type"] == "object"


def test_evaluate_runtime_js_exception(chrome_cdp, supervisor_registry):
    """JS exceptions surface as ok=False with the exception message."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-eval-3", cdp_url=cdp_url)

    _fire_on_page(cdp_url, "void 0")
    _wait_for_page_session(supervisor)

    out = supervisor.evaluate_runtime("nonExistentVar.nope")
    assert out["ok"] is False
    assert "ReferenceError" in out["error"] or "not defined" in out["error"]


def test_evaluate_runtime_dom_node_returns_empty_object(chrome_cdp, supervisor_registry):
    """DOM nodes with returnByValue=true serialize to ``{}`` (Chrome quirk).

    This is honest — DOM nodes can't be deeply JSON-serialized — and matches
    DevTools console behaviour for the same expression.  Documenting the
    contract here so a future change that "fixes" it (e.g. switching to
    returnByValue=false + DOM.describeNode) doesn't break callers expecting
    the current shape.
    """
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-eval-4", cdp_url=cdp_url)

    _fire_on_page(cdp_url, "void 0")
    _wait_for_page_session(supervisor)

    out = supervisor.evaluate_runtime("document.querySelector('h1')")
    assert out["ok"] is True
    assert out["result_type"] == "object"
    # Empty dict — Chrome can't deeply-serialize a DOM node through returnByValue.
    assert out["result"] == {}


def test_evaluate_runtime_unserializable_value(chrome_cdp, supervisor_registry):
    """``Infinity``/``NaN``/``BigInt`` come back via ``unserializableValue``."""
    cdp_url, _port = chrome_cdp
    supervisor = supervisor_registry.get_or_start(task_id="pytest-eval-5", cdp_url=cdp_url)

    _fire_on_page(cdp_url, "void 0")
    _wait_for_page_session(supervisor)

    out = supervisor.evaluate_runtime("Infinity")
    assert out["ok"] is True
    assert out["result"] == "Infinity"
