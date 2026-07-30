# F-014 HTML Local Launch Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let users launch rendered HTML study material without MCP or a typed terminal command, using generated macOS/Linux and Windows scripts that open the browser on an automatically assigned local port.

**Architecture:** Keep `study_html.py` as the sole localhost progress API server and retain its direct-run default port `8765` for compatibility. `HtmlRenderer` copies it into each HTML generation, renders platform scripts from templates with the current project Python absolute path, and makes only the POSIX script executable. Both scripts append `--port 0` before user-supplied arguments so the Python server performs the atomic bind-and-port-assignment operation; a later user `--port` overrides it.

**Tech Stack:** Python 3.11+, `http.server`, `sys`, `shlex`, `pathlib`, `subprocess`, FastMCP response envelopes, pytest.

## Global Constraints

- Support results generated and run on the same project-configured computer only; generated scripts intentionally embed the rendering process's absolute `sys.executable`.
- Do not add an MCP start/stop tool or move progress storage out of `progress/` JSON.
- Keep `study_html.py --port 8765` as the direct-run compatibility path.
- Bind the study server only to `127.0.0.1`; let port `0` be assigned by the operating system after bind rather than probing in shell.
- Keep every MCP response in `{ok, error, data, next_action}` form and preserve existing HTML success fields, including `launch_command` and `default_url`.
- Generate `start_study.sh` and `start_study.bat` as manifest-managed HTML output paths; never overwrite files outside the renderer staging generation.
- Use test-first red/green cycles for every production behavior change.

---

### Task 1: Actual-port study server

**Files:**
- Modify: `templates/html/study_html.py:1-96`
- Modify: `tests/test_serve.py:1-123`

**Interfaces:**
- Changes: `study_html.py --port <int>` accepts `0` and prints the URL built from `HTTPServer.server_port` after bind.
- Preserves: no `--port` argument still binds port `8765`.

- [ ] **Step 1: Write the failing default-auto-port test**

Add a `test_port_zero_prints_and_serves_assigned_url(serve_dir)` helper that starts the copied script with `--port 0 --no-browser`, reads its first stdout line, extracts the port from `http://127.0.0.1:<port>/main.html`, and calls `_wait_ready(actual_port, "/main.html")`. Assert the parsed port is greater than zero and differs from the literal requested value `0`.

```python
def test_port_zero_prints_and_serves_assigned_url(serve_dir):
    proc = subprocess.Popen(
        [sys.executable, str(serve_dir / "study_html.py"), "--port", "0", "--no-browser"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, bufsize=1,
        cwd=str(serve_dir),
    )
    try:
        line = proc.stdout.readline()
        match = re.search(r"http://127\\.0\\.0\\.1:(\\d+)/main\\.html", line)
        assert match, line
        actual_port = int(match.group(1))
        assert actual_port > 0
        _wait_ready(actual_port, "/main.html")
    finally:
        proc.terminate()
        proc.wait(timeout=3)
```

- [ ] **Step 2: Run the test and verify RED**

Run: `rtk .venv/bin/python -m pytest tests/test_serve.py -q -k port_zero`

Expected: FAIL because the launcher constructs the URL before bind and reports port `0`.

- [ ] **Step 3: Bind before constructing the URL**

In `main()`, construct `HTTPServer(("127.0.0.1", args.port), StudyHandler)` before calculating the URL. Read `server.server_port`, build the URL from it, and print the ready URL with `flush=True` before scheduling `webbrowser.open`. Keep `DEFAULT_PORT = 8765`, `--no-browser`, routing, and progress validation unchanged.

```python
server = HTTPServer(("127.0.0.1", args.port), StudyHandler)
actual_port = server.server_port
url = f"http://127.0.0.1:{actual_port}/{entry}"
print(f"Study server running at {url}", flush=True)
```

- [ ] **Step 4: Run server tests and verify GREEN**

Run: `rtk .venv/bin/python -m pytest tests/test_serve.py -q`

Expected: PASS, including existing explicit-port progress API tests.

### Task 2: Rendered platform launch scripts

**Files:**
- Create: `templates/html/start_study.sh.template`
- Create: `templates/html/start_study.bat.template`
- Modify: `renderer/html_renderer.py:1-20, 217-307, 588-601`
- Modify: `templates/html/README.md:1-14`
- Modify: `tests/test_renderer.py:1-15, 198-207`

**Interfaces:**
- Changes: `_copy_assets(output_dir: Path, python_executable: str) -> None` writes static assets plus `start_study.sh` and `start_study.bat`.
- Produces: HTML staging roots `start_study.sh` (mode `0o755`) and `start_study.bat` (regular text file).
- Consumes: `sys.executable` from the rendering process; `HtmlRenderer.render()` passes it to `_copy_assets`.

- [ ] **Step 1: Write failing rendered-launcher tests**

Extend `test_assets_are_copied` or add `test_html_output_contains_project_local_launch_scripts`. Build HTML output through `_build_multi`, then assert both files exist, `start_study.sh` has an executable bit, and their contents contain the current `sys.executable`, `study_html.py`, and `--port 0`. Assert the shell script uses `exec` and `"$@"`; assert the batch script uses `%~dp0study_html.py` and `%*`.

```python
def test_html_output_contains_project_local_launch_scripts(ko_with_toc, tmp_path):
    _, out, _ = _build_multi(ko_with_toc, tmp_path)
    sh = out / "start_study.sh"
    bat = out / "start_study.bat"
    assert sh.stat().st_mode & stat.S_IXUSR
    assert sys.executable in sh.read_text(encoding="utf-8")
    assert 'exec ' in sh.read_text(encoding="utf-8")
    assert '"$@"' in sh.read_text(encoding="utf-8")
    assert sys.executable in bat.read_text(encoding="utf-8")
    assert '"%~dp0study_html.py" --port 0 %*' in bat.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run the launcher test and verify RED**

Run: `rtk .venv/bin/python -m pytest tests/test_renderer.py -q -k launch_scripts`

Expected: FAIL because the renderer currently copies only `study_html.py` and `README.md` at the output root.

- [ ] **Step 3: Add launcher templates and renderer substitution**

Create the POSIX template below. The renderer must substitute `__PDF_LEARNER_PYTHON__` with `shlex.quote(sys.executable)` before writing `start_study.sh`, then set mode `0o755` with `Path.chmod`.

```sh
#!/bin/sh
set -eu
SCRIPT_DIR=$(CDPATH= cd "$(dirname "$0")" && pwd)
exec __PDF_LEARNER_PYTHON__ "$SCRIPT_DIR/study_html.py" --port 0 "$@"
```

Create the Windows template below. The renderer must substitute the same marker with a batch-safe double-quoted executable path (`"` escaped as `""`) before writing `start_study.bat`.

```bat
@echo off
setlocal
__PDF_LEARNER_PYTHON__ "%~dp0study_html.py" --port 0 %*
if errorlevel 1 pause
```

Add a launcher-copy loop separate from `_STATIC_ROOT_FILES`, reject a missing marker with `ValueError`, and call `_copy_assets(output_dir, sys.executable)` from `HtmlRenderer.render`. Do not make templates themselves manifest paths; only the generated `.sh` and `.bat` files belong to staging.

- [ ] **Step 4: Document the generated execution path**

Change `templates/html/README.md` to present `start_study.sh` for macOS/Linux and `start_study.bat` for Windows as the normal launch path. State that the scripts use the project environment that rendered the result, automatically choose a port, open the browser, and must run on the same configured computer. Keep direct `python study_html.py --port 8765` as a troubleshooting alternative.

- [ ] **Step 5: Run renderer and manifest regression tests**

Run: `rtk .venv/bin/python -m pytest tests/test_renderer.py tests/test_md_tui_renderer.py -q`

Expected: PASS; the output manager's existing `new_managed = staging_dir.iterdir()` behavior records both generated scripts and removes them on HTML-to-TUI replacement.

### Task 3: HTML completion response and project documentation

**Files:**
- Modify: `server.py:1350-1498`
- Modify: `tests/test_server.py:1430-1470`
- Modify: `docs/contracts.md`
- Modify: `docs/business-rules.md`
- Modify: `docs/architecture.md`
- Modify: `docs/engineering-notes.md`
- Modify: `docs/operations.md`
- Modify: `docs/tracking/status.md`
- Modify: `docs/findings.md`

**Interfaces:**
- Preserves: HTML `data.launch_command`, `data.python`, `data.entry_page`, and fixed-port `data.default_url` for direct `study_html.py` compatibility.
- Adds: HTML `data.launch_scripts = {"macos_linux": "start_study.sh", "windows": "start_study.bat"}` and `data.auto_port_on_script_launch = true`.

- [ ] **Step 1: Write failing HTML finalize-response test**

Add a completed HTML workflow test that calls `finalize_study(..., "html")` and asserts the new `launch_scripts` mapping and `auto_port_on_script_launch is True`. Keep existing assertions for `launch_command`, `python`, and `default_url`.

```python
assert response["data"]["launch_scripts"] == {
    "macos_linux": "start_study.sh",
    "windows": "start_study.bat",
}
assert response["data"]["auto_port_on_script_launch"] is True
```

- [ ] **Step 2: Run the response test and verify RED**

Run: `rtk .venv/bin/python -m pytest tests/test_server.py -q -k html_launch_scripts`

Expected: FAIL because the HTML response only exposes the manual `study_html.py` command.

- [ ] **Step 3: Add launch metadata and replace the HTML next-action guidance**

Keep the current direct command fields unchanged. Add the two new fields only to HTML success data. Rewrite the HTML `next_action` to instruct users to double-click `start_study.sh` on macOS/Linux or `start_study.bat` on Windows; explain that the scripts choose a port and open a browser, and that closing the script's server window or Ctrl+C stops it. Do not mention MCP as a runtime dependency and do not make an HTTP request from `finalize_study`.

- [ ] **Step 4: Update contracts and finding record**

Document the launch-script names, same-computer/project-environment constraint, automatic script port allocation, direct-script compatibility path, loopback-only server, and unchanged `progress/` JSON storage. Mark F-014 resolved in `docs/findings.md`, update the recommended order, and set the test count in `docs/tracking/status.md` to the actual full-suite count after verification.

- [ ] **Step 5: Run focused server tests and the full suite**

Run: `rtk .venv/bin/python -m pytest tests/test_server.py tests/test_renderer.py tests/test_serve.py -q`

Expected: PASS.

Run: `rtk .venv/bin/python -m pytest -q`

Expected: PASS with the new total test count and only the existing third-party SWIG deprecation warnings.

- [ ] **Step 6: Verify and commit F-014**

Run: `rtk .venv/bin/python -m py_compile server.py renderer/html_renderer.py templates/html/study_html.py`, `rtk git diff --check`, and `rtk git status --short`.

Stage only the F-014 implementation, template, test, spec correction, plan, and documentation files; exclude user-owned `docs/findings2.md`.

```bash
git add server.py renderer/html_renderer.py templates/html/study_html.py templates/html/start_study.sh.template templates/html/start_study.bat.template templates/html/README.md tests/test_server.py tests/test_renderer.py tests/test_serve.py docs/architecture.md docs/business-rules.md docs/contracts.md docs/engineering-notes.md docs/findings.md docs/operations.md docs/tracking/status.md docs/superpowers/specs/2026-07-22-f014-html-local-launch-design.md docs/superpowers/plans/2026-07-22-f014-html-local-launch.md && git commit -m "fix: HTML 학습 자료 실행 간소화"
```

Expected: one implementation commit containing the approved F-014 change and updated findings.
