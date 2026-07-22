# F-014 Task 1 Report: Actual-port study server

## TDD evidence

1. Added `test_port_zero_prints_and_serves_assigned_url` to `tests/test_serve.py` before changing production code.
2. RED command:

   ```text
   rtk env PYTHONUNBUFFERED=1 .venv/bin/python -m pytest tests/test_serve.py -q -k port_zero
   ```

   Result: `1 failed, 6 deselected`. The launcher printed `http://localhost:0/main.html`, so the test correctly failed to find the required loopback URL with an assigned port.

   The exact brief command without `PYTHONUNBUFFERED=1` blocked while reading the first piped stdout line because the pre-change launcher did not flush that line. The implementation adds the required `flush=True`.

3. Implemented the minimal bind-first change, then verified GREEN.

## Changed files

- `templates/html/study_html.py`
  - Binds `HTTPServer` to `127.0.0.1` before URL construction.
  - Uses `server.server_port` for the URL, including `--port 0`.
  - Flushes the ready URL before scheduling browser opening.
  - Preserves `DEFAULT_PORT = 8765`, `--no-browser`, routing, and progress behavior.
- `tests/test_serve.py`
  - Adds the port-zero URL and readiness regression test.

## Test commands and results

```text
rtk .venv/bin/python -m pytest tests/test_serve.py -q
7 passed, 5 warnings in 0.88s

rtk .venv/bin/python -m pytest -q --disable-warnings
271 passed, 5 warnings in 95.02s (0:01:35)

rtk git diff --check
passed with no whitespace errors
```

## Self-review

- `--port 0` now reports the OS-assigned port from `HTTPServer.server_port`.
- The server and generated URL are explicitly loopback-bound/addressed with `127.0.0.1`.
- The URL is printed and flushed before browser scheduling, allowing callers to read readiness reliably.
- The default remains 8765 when `--port` is omitted.
- The change is limited to the requested launcher and test, plus this report.
- No unrelated edits were reverted; the pre-existing untracked `.venv` remains uncommitted.

## Concerns

- The full suite emits five existing dependency deprecation warnings involving `SwigPyPacked`/`SwigPyObject`; no test failures or new warnings were observed.
