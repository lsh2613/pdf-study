#!/usr/bin/env python3
"""pdf-study 학습 TUI (rich 기반).

- 루트에서 실행: `python study_tui.py` → 챕터 선택 메뉴
- 챕터 폴더에서 실행: `cd ch1 && python study_tui.py` → 해당 챕터로 바로 진입

pdf_study 패키지에 의존하지 않는 독립 스크립트(출력 폴더에 그대로 복사됨).
의존성 rich는 **있으면 사용, 없으면 자동 설치 시도, 그래도 안 되면(pip 부재·
오프라인·권한·externally-managed 환경 등) 평문 모드로 폴백**한다 — 어떤
환경에서도 별도 준비 없이 실행된다.
"""
from __future__ import annotations

import datetime as _dt
import json
import re as _re
import subprocess
import sys
from pathlib import Path


def _try_import_rich() -> bool:
    try:
        import rich  # noqa: F401
        return True
    except ImportError:
        return False


def _install_rich() -> bool:
    """rich 자동 설치 시도 (여러 전략). 성공해서 import 가능하면 True."""
    import importlib
    strategies = (
        [sys.executable, "-m", "pip", "install", "rich"],
        [sys.executable, "-m", "pip", "install", "--user", "rich"],
    )
    for args in strategies:
        try:
            subprocess.run(args, check=True)
        except Exception:  # noqa: BLE001  (pip 없음·오프라인·권한·PEP668 등 무엇이든)
            continue
        importlib.invalidate_caches()
        if _try_import_rich():
            return True
    return False


# rich가 없으면 자동 설치를 시도하고, 그래도 안 되면(pip 부재·오프라인·권한·
# externally-managed 환경 등) **평문 모드로 폴백**해 어떤 환경에서도 실행되게 한다.
_HAS_RICH = _try_import_rich()
if not _HAS_RICH:
    print("필수 의존성 'rich'가 없어 설치를 시도합니다… (pip install rich)", flush=True)
    _HAS_RICH = _install_rich()
    if not _HAS_RICH:
        print(
            "rich 설치에 실패해 평문 모드로 진행합니다(기능은 동일). 더 보기 좋은 "
            "화면을 원하면 'pip install rich' 후 다시 실행하세요.",
            flush=True,
        )


def _plain_console_shims():
    """rich 미설치 시 쓰는 평문 셰임 (Console/Markdown/Panel/Prompt/Confirm).

    study_tui가 실제로 쓰는 rich API 표면만 최소 흉내 낸다. 인라인 마크업
    태그([bold] 등)는 제거하고, Markdown/Panel은 평문으로 출력한다.
    """
    _TAG = _re.compile(r"\[/?[a-zA-Z][a-zA-Z0-9 _#]*\]")

    def _strip(s):
        return _TAG.sub("", s) if isinstance(s, str) else s

    class _Markdown:
        def __init__(self, text):
            self.text = str(text)

    class _Panel:
        def __init__(self, text, title="", border_style=""):
            self.text, self.title = str(text), str(title or "")

    class _Console:
        def print(self, *args):
            if not args:
                print()
                return
            for a in args:
                if isinstance(a, _Markdown):
                    print(a.text)
                elif isinstance(a, _Panel):
                    if a.title:
                        print(f"── {a.title} ──")
                    print(a.text)
                else:
                    print(_strip(str(a)))

        def rule(self, text=""):
            t = _strip(str(text))
            print("\n" + "=" * 60)
            if t:
                print(t)
            print("=" * 60)

    class _Prompt:
        @staticmethod
        def ask(prompt, choices=None, default=None):
            label = _strip(str(prompt))
            ch = f" {list(choices)}" if choices else ""
            dh = f" [{default}]" if default is not None else ""
            while True:
                try:
                    v = input(f"{label}{ch}{dh}: ").strip()
                except EOFError:
                    return default if default is not None else ""
                if not v and default is not None:
                    return str(default)
                if not choices or v in choices:
                    return v
                print(f"  {list(choices)} 중에서 입력하세요.")

    class _Confirm:
        @staticmethod
        def ask(prompt, default=False):
            label = _strip(str(prompt))
            hint = "Y/n" if default else "y/N"
            while True:
                try:
                    v = input(f"{label} [{hint}]: ").strip().lower()
                except EOFError:
                    return bool(default)
                if not v:
                    return bool(default)
                if v in ("y", "yes"):
                    return True
                if v in ("n", "no"):
                    return False

    return _Console(), _Markdown, _Panel, _Prompt, _Confirm


if _HAS_RICH:
    from rich.console import Console
    from rich.markdown import Markdown
    from rich.panel import Panel
    from rich.prompt import Confirm, Prompt
    console = Console()
else:
    console, Markdown, Panel, Prompt, Confirm = _plain_console_shims()

_TYPE_LABELS = {
    "multiple_choice": "객관식",
    "short_answer": "단답형",
    "reflection": "주관식",
    "extension": "확장형",
}
_TYPE_ORDER = ("multiple_choice", "short_answer", "reflection", "extension")


def _now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _chapter_sort_key(name: str) -> tuple[int, str]:
    """ch1, ch2, ch10이 자연스럽게 정렬되도록."""
    if name.startswith("ch") and name[2:].isdigit():
        return (int(name[2:]), name)
    return (10**9, name)


# ---------------------------------------------------------------------------
# 진도 (각 챕터 폴더의 progress.json)
# ---------------------------------------------------------------------------

def _load_progress(chapter_dir: Path) -> dict:
    prog = _load_json(chapter_dir / "progress.json", {})
    if not isinstance(prog, dict):
        prog = {}
    prog.setdefault("chapter_id", chapter_dir.name)
    prog.setdefault("answers", {})
    prog.setdefault("completed", False)
    return prog


def _save_progress(chapter_dir: Path, prog: dict) -> None:
    prog["last_updated"] = _now()
    (chapter_dir / "progress.json").write_text(
        json.dumps(prog, ensure_ascii=False, indent=2), encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# 문제 풀이
# ---------------------------------------------------------------------------

def _ask_mc(q: dict) -> dict:
    options = q.get("options") or []
    console.print(f"[bold]{q.get('question', '')}[/bold]")
    for i, opt in enumerate(options):
        console.print(f"  {i + 1}. {opt}")
    choices = [str(i + 1) for i in range(len(options))]
    sel_idx = int(Prompt.ask("정답 번호", choices=choices)) - 1
    answer_idx = int(q.get("answer_index", -1))
    correct = sel_idx == answer_idx
    if correct:
        console.print("[green]정답![/green]")
    else:
        right = options[answer_idx] if 0 <= answer_idx < len(options) else ""
        console.print(f"[red]오답.[/red] 정답: {answer_idx + 1}. {right}")
    if q.get("explanation"):
        console.print(Panel(q["explanation"], title="해설", border_style="cyan"))
    return {"selected": sel_idx, "correct": correct}


def _ask_text(q: dict, qtype: str) -> dict:
    console.print(f"[bold]{q.get('question', '')}[/bold]")
    console.print("[dim](답변 입력 후 빈 줄로 제출 — 건너뛰려면 그냥 빈 줄)[/dim]")
    lines: list[str] = []
    while True:
        try:
            line = input()
        except EOFError:
            break
        if line == "":
            break
        lines.append(line)
    text = "\n".join(lines)
    model = q.get("model_answer") or ""
    if model:
        console.print(Panel(model, title="모범답안", border_style="green"))
    return {"text": text, "viewed_answer": bool(model)}


def _run_quiz(chapter_dir: Path, quiz: dict, prog: dict) -> None:
    questions = quiz.get("questions") or {}
    answers = prog["answers"]
    for qtype in _TYPE_ORDER:
        items = questions.get(qtype) or []
        if not items:
            continue
        pending = [q for q in items if not q.get("id") or q["id"] not in answers]
        if not pending:
            continue
        console.rule(_TYPE_LABELS.get(qtype, qtype))
        for q in pending:
            qid = q.get("id") or ""
            if qtype == "multiple_choice":
                res = _ask_mc(q)
            else:
                res = _ask_text(q, qtype)
            answers[qid] = res
            _save_progress(chapter_dir, prog)
            console.print()
    mc_items = questions.get("multiple_choice") or []
    if mc_items and all(q.get("id") in answers for q in mc_items):
        mc_correct = sum(bool(answers[q.get("id")].get("correct")) for q in mc_items)
        prog["mc_score"] = {"correct": mc_correct, "total": len(mc_items)}
        console.print(f"[bold]객관식 결과: {mc_correct} / {len(mc_items)}[/bold]")
    if Confirm.ask("이 챕터를 완료로 표시할까요?", default=bool(prog.get("completed"))):
        prog["completed"] = True
    _save_progress(chapter_dir, prog)


# ---------------------------------------------------------------------------
# 화면
# ---------------------------------------------------------------------------

def run_chapter(chapter_dir) -> None:
    chapter_dir = Path(chapter_dir).resolve()
    quiz = _load_json(chapter_dir / "quiz.json", {}) or {}
    summary_path = chapter_dir / "summary.md"
    title = quiz.get("title") or chapter_dir.name
    has_quiz = any((quiz.get("questions") or {}).values())

    prog = _load_progress(chapter_dir)
    if has_quiz and prog["answers"] and not prog["completed"]:
        console.print("[dim]저장된 풀이가 있어 첫 미응답 문제부터 이어갑니다.[/dim]")
        _run_quiz(chapter_dir, quiz, prog)
        return

    while True:
        console.rule(f"[bold]{title}[/bold]")
        console.print("[r] 요약 읽기   [s] 문제 풀기   [q] 종료")
        choice = Prompt.ask("선택", choices=["r", "s", "q"], default="r")
        if choice == "r":
            if summary_path.exists():
                console.print(Markdown(summary_path.read_text(encoding="utf-8")))
            else:
                console.print("[dim]요약 파일이 없습니다.[/dim]")
        elif choice == "s":
            if not has_quiz:
                console.print("[dim]이 챕터에는 문제가 없습니다.[/dim]")
                continue
            _run_quiz(chapter_dir, quiz, _load_progress(chapter_dir))
        else:
            break


def run_root(root_dir) -> None:
    root_dir = Path(root_dir).resolve()
    chapters = sorted(
        (p for p in root_dir.iterdir() if p.is_dir() and (p / "quiz.json").exists()),
        key=lambda p: _chapter_sort_key(p.name),
    )
    if not chapters:
        console.print("[red]챕터를 찾을 수 없습니다.[/red]")
        return

    while True:
        console.rule("[bold]학습 자료[/bold]")
        for i, ch in enumerate(chapters):
            quiz = _load_json(ch / "quiz.json", {}) or {}
            prog = _load_json(ch / "progress.json", {}) or {}
            mark = "✓" if prog.get("completed") else " "
            console.print(f"  {i + 1}. [{mark}] {quiz.get('title') or ch.name}")
        console.print("  q. 종료")
        choices = [str(i + 1) for i in range(len(chapters))] + ["q"]
        sel = Prompt.ask("챕터 선택", choices=choices, default="1")
        if sel == "q":
            break
        run_chapter(chapters[int(sel) - 1])


def main() -> None:
    run_root(Path(__file__).resolve().parent)


if __name__ == "__main__":
    main()
