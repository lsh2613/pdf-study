"""최종 렌더 결과의 관리 범위, 교체, 진도 호환성을 담당한다."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import workspace
from .study_loader import load_study_data

MANIFEST_VERSION = 1
SUPPORTED_FORMATS = {"html", "md_tui"}


def _canonical_json(data: Any) -> bytes:
    return json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def render_study_fingerprint(work_id: str) -> str:
    """현재 렌더 입력이 같을 때만 같은 값을 갖는 결정적 fingerprint."""
    loaded = load_study_data(work_id)
    state = loaded["state"]
    pdf_path = Path(state.get("pdf_path") or "")
    try:
        stat = pdf_path.stat()
        pdf_identity: dict[str, Any] = {
            "path": str(pdf_path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
        }
    except OSError:
        pdf_identity = {"path": str(pdf_path), "size": None, "mtime_ns": None}

    chapters = [
        {
            "chapter_id": chapter["chapter_id"],
            "meta": chapter["meta"],
            "summary": chapter["summary"],
            "extension": chapter["extension"],
        }
        for chapter in loaded["chapters"]
    ]
    payload = {
        "pdf": pdf_identity,
        "book_info": loaded["book_info"],
        "question_options": state.get("question_options") or {},
        "chapters": chapters,
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


def _safe_managed_name(value: Any) -> str | None:
    if not isinstance(value, str) or not value:
        return None
    path = Path(value)
    if (
        path.is_absolute()
        or len(path.parts) != 1
        or value in {".", "..", ".work", workspace.OUTPUT_MANIFEST_NAME}
    ):
        return None
    return value


def _read_manifest(output_dir: Path) -> dict[str, Any] | None:
    path = output_dir / workspace.OUTPUT_MANIFEST_NAME
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(f"invalid output manifest: {path}") from exc
    if not isinstance(data, dict) or data.get("version") != MANIFEST_VERSION:
        raise ValueError(f"unsupported output manifest: {path}")
    if data.get("output_format") not in SUPPORTED_FORMATS:
        raise ValueError(f"invalid output format in manifest: {path}")
    if not isinstance(data.get("study_fingerprint"), str):
        raise ValueError(f"missing study fingerprint in manifest: {path}")
    managed = data.get("managed_paths")
    if not isinstance(managed, list):
        raise ValueError(f"invalid managed paths in manifest: {path}")
    safe_names = [_safe_managed_name(item) for item in managed]
    if any(name is None for name in safe_names) or len(set(safe_names)) != len(safe_names):
        raise ValueError(f"unsafe managed paths in manifest: {path}")
    data["managed_paths"] = [name for name in safe_names if name is not None]
    return data


def _legacy_managed_paths(output_dir: Path) -> list[str]:
    """manifest 도입 전 생성물 중 확실히 식별되는 top-level 경로만 찾는다."""
    names: set[str] = set()
    formats = workspace.legacy_output_formats(output_dir)
    exact: set[str] = set()
    if "html" in formats:
        exact.update({
            "assets", "study_html.py", "index.html", "main.html", "progress",
            "README.md",
        })
    if "md_tui" in formats:
        exact.update({"book.md", "study_tui.py", "README.md"})
    for name in exact:
        if (output_dir / name).exists():
            names.add(name)
    if output_dir.exists():
        for path in output_dir.iterdir():
            if (
                "html" in formats
                and path.is_file()
                and path.name.startswith("ch")
                and path.suffix == ".html"
            ):
                names.add(path.name)
            elif "md_tui" in formats and path.is_dir() and all(
                (path / child).exists()
                for child in ("summary.md", "quiz.json", "study_tui.py")
            ):
                names.add(path.name)
    return sorted(names)


def _write_manifest_atomic(path: Path, data: dict[str, Any]) -> None:
    tmp = tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        delete=False,
        suffix=".tmp",
    )
    tmp_path = Path(tmp.name)
    try:
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp_path, path)
    except Exception:
        tmp.close()
        if tmp_path.exists():
            tmp_path.unlink()
        raise


def _copy_compatible_progress(
    output_dir: Path,
    staging_dir: Path,
    output_format: str,
) -> None:
    if output_format == "html":
        source = output_dir / "progress"
        target = staging_dir / "progress"
        if source.is_dir() and target.is_dir():
            shutil.copytree(source, target, dirs_exist_ok=True)
        return

    for chapter_dir in staging_dir.iterdir():
        if not chapter_dir.is_dir():
            continue
        source = output_dir / chapter_dir.name / "progress.json"
        if source.is_file():
            shutil.copy2(source, chapter_dir / "progress.json")


def _remove_path(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path)
    elif path.exists() or path.is_symlink():
        path.unlink()


def install_rendered_output(
    work_id: str,
    output_format: str,
    render: Callable[[Path], None],
) -> dict[str, Any]:
    """완전한 렌더 세대를 staging에서 만든 뒤 관리 경로만 교체한다."""
    if output_format not in SUPPORTED_FORMATS:
        raise ValueError(f"unsupported output format: {output_format}")

    state = workspace.load_state(work_id)
    output_dir = Path(state["output_dir"]).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = render_study_fingerprint(work_id)
    old_manifest = _read_manifest(output_dir)
    old_manifest_path = output_dir / workspace.OUTPUT_MANIFEST_NAME
    old_manifest_bytes = old_manifest_path.read_bytes() if old_manifest_path.exists() else None
    old_managed = set(
        old_manifest["managed_paths"]
        if old_manifest is not None
        else _legacy_managed_paths(output_dir)
    )

    staging_dir = Path(tempfile.mkdtemp(
        prefix=f".pdf-learner-render-{work_id}-",
        dir=str(output_dir.parent),
    ))
    backup_dir = Path(tempfile.mkdtemp(
        prefix=f".pdf-learner-backup-{work_id}-",
        dir=str(output_dir.parent),
    ))
    installed: list[str] = []
    moved_old: list[str] = []
    try:
        render(staging_dir)
        new_managed = sorted(path.name for path in staging_dir.iterdir())
        invalid = [name for name in new_managed if _safe_managed_name(name) is None]
        if invalid:
            raise ValueError(f"renderer produced unsafe top-level paths: {invalid}")

        compatible_progress = bool(
            old_manifest
            and old_manifest.get("output_format") == output_format
            and old_manifest.get("study_fingerprint") == fingerprint
        )
        if compatible_progress:
            _copy_compatible_progress(output_dir, staging_dir, output_format)

        unmanaged_collisions = [
            name
            for name in new_managed
            if (
                ((output_dir / name).exists() or (output_dir / name).is_symlink())
                and name not in old_managed
            )
        ]
        if unmanaged_collisions:
            raise FileExistsError(
                "render output would overwrite unmanaged paths: "
                + ", ".join(unmanaged_collisions)
            )

        for name in sorted(old_managed):
            source = output_dir / name
            if source.exists() or source.is_symlink():
                os.replace(source, backup_dir / name)
                moved_old.append(name)

        for name in new_managed:
            os.replace(staging_dir / name, output_dir / name)
            installed.append(name)

        manifest = {
            "version": MANIFEST_VERSION,
            "work_id": work_id,
            "output_format": output_format,
            "study_fingerprint": fingerprint,
            "managed_paths": new_managed,
        }
        _write_manifest_atomic(old_manifest_path, manifest)
        return manifest
    except Exception:
        for name in reversed(installed):
            _remove_path(output_dir / name)
        for name in moved_old:
            backup = backup_dir / name
            if backup.exists() or backup.is_symlink():
                os.replace(backup, output_dir / name)
        if old_manifest_bytes is None:
            if old_manifest_path.exists():
                old_manifest_path.unlink()
        else:
            tmp_manifest = old_manifest_path.with_suffix(".restore.tmp")
            tmp_manifest.write_bytes(old_manifest_bytes)
            os.replace(tmp_manifest, old_manifest_path)
        raise
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)
        shutil.rmtree(backup_dir, ignore_errors=True)
