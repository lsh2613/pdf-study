# 06. 동시성 처리 (병렬 모드)

`execution_mode="parallel"`일 때 메인 LLM이 여러 sub-agent를 동시에 디스패치하면, 결과 저장 호출(`save_chapter_result` 등)이 동시에 MCP 서버에 들어온다. 이때 `state.json`의 read-modify-write 경합을 막아야 한다.

## 핵심 관찰

MCP 서버는 **단일 Python 프로세스**다. 다른 프로세스가 `state.json`을 건드리지 않으므로 OS 레벨 파일 락(`fcntl`/`portalocker`) 불필요. 단일 프로세스 내 동시성만 차단하면 충분.

```
[Main LLM]
  ├─ sub-agent 1 ─┐
  ├─ sub-agent 2 ─┼─→ MCP 서버 (단일 Python 프로세스)
  ├─ sub-agent 3 ─┤      ↓ in-memory lock + atomic write
  ├─ sub-agent 4 ─┤   state.json
  └─ sub-agent 5 ─┘
```

## 정책

- `state.json`은 **단일 파일** 유지 (가독성).
- `save_chapter_result` / `save_extension_result` / 기타 state 수정 함수는 **work_id별 `threading.Lock`**으로 read-modify-write를 직렬화.
- 쓰기는 **atomic rename**(`tempfile` → `os.replace`)로 부분 손상 방지.

## 구현 스케치 (workspace.py)

```python
import threading, json, tempfile, os
from pathlib import Path

_locks: dict[str, threading.Lock] = {}
_locks_meta = threading.Lock()

def _get_lock(work_id: str) -> threading.Lock:
    with _locks_meta:
        if work_id not in _locks:
            _locks[work_id] = threading.Lock()
        return _locks[work_id]

def update_chapter_status(work_id: str, chapter_id: str, **updates):
    state_path = work_dir(work_id) / "state.json"
    with _get_lock(work_id):
        state = json.loads(state_path.read_text(encoding='utf-8'))
        state["chapters"][chapter_id].update(updates)
        _atomic_write_json(state_path, state)

def _atomic_write_json(path: Path, data: dict):
    tmp = tempfile.NamedTemporaryFile(
        mode='w', encoding='utf-8',
        dir=path.parent, delete=False, suffix='.tmp'
    )
    try:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.close()
        os.replace(tmp.name, path)
    except Exception:
        os.unlink(tmp.name)
        raise
```

## 비동기 주의

FastMCP 도구를 `async def`로 정의하면 `asyncio.Lock`을 써야 한다. 본 프로젝트는 **state 수정 함수는 동기 통일**, `search_extension_context`(Exa 호출)만 async로 둔다.

## 모드별 책임 분담

| 모드 | 디스패치 | state 안전성 보장 주체 |
|---|---|---|
| `sequential` (기본) | 메인 LLM이 한 챕터씩 처리 | 자연스럽게 순차적이므로 보장됨 |
| `parallel` | 메인 LLM이 최대 5개 동시 spawn | MCP 서버의 in-memory lock + atomic write |
