#!/usr/bin/env python3
"""Apply the pdf-learner MCP server entry to Codex CLI."""
from __future__ import annotations

import argparse
import os
import re
import shutil
import stat
import subprocess
import tempfile
import tomllib
from pathlib import Path


class ConfigError(RuntimeError):
    """Raised when an existing MCP config cannot be safely updated."""


GRANULAR_APPROVALS_ENABLED = {
    "sandbox_approval": True,
    "rules": True,
    "mcp_elicitations": True,
    "request_permissions": True,
    "skill_approval": True,
}

_CODEX_ELICITATION_POLICY = (
    "[approval_policy.granular]\n"
    "sandbox_approval = true\n"
    "rules = true\n"
    "mcp_elicitations = true\n"
    "request_permissions = true\n"
    "skill_approval = true"
)

_CODEX_ELICITATION_LAUNCH_GUIDANCE = (
    "Launch Codex without --dangerously-bypass-approvals-and-sandbox, --yolo, "
    "or -a never; those CLI flags override config.toml and suppress MCP forms. "
    "For full filesystem access with MCP forms, use: "
    "codex --sandbox danger-full-access"
)

_CODEX_PDF_LEARNER_DEFAULT_TOOL_APPROVAL = (
    'default_tools_approval_mode = "approve"'
)


def apply_codex_cli_config(
    *,
    command: str,
    cache_dir: Path,
    codex_bin: str,
) -> None:
    """Register and verify the server through Codex CLI's supported interface."""
    add_command = [
        codex_bin,
        "mcp",
        "add",
        "--env",
        f"PDF_LEARNER_PADDLEOCR_CACHE={cache_dir}",
        "pdf-learner",
        "--",
        command,
        "-m",
        "pdf_learner",
    ]
    get_command = [codex_bin, "mcp", "get", "pdf-learner"]
    for cli_command, action in ((add_command, "register"), (get_command, "verify")):
        try:
            completed = subprocess.run(
                cli_command,
                text=True,
                capture_output=True,
                check=False,
            )
        except OSError as exc:
            raise ConfigError(f"Codex CLI {action} failed: {exc}") from exc
        if completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise ConfigError(f"Codex CLI {action} failed: {detail or 'unknown error'}")


def _atomic_write(path: Path, content: bytes, mode: int) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary_path, mode)
        os.replace(temporary_path, path)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _snapshot(path: Path) -> tuple[bool, bytes | None, int]:
    if not path.exists():
        return False, None, 0o600
    return True, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)


def _restore(path: Path, snapshot: tuple[bool, bytes | None, int]) -> None:
    existed, content, mode = snapshot
    if existed:
        assert content is not None
        _atomic_write(path, content, mode)
    elif path.exists():
        path.unlink()


def _insert_minimal_elicitation_policy(text: str) -> str:
    """Add the policy table after root assignments and before existing tables."""
    first_table = re.search(r"(?m)^[ \t]*\[[^\r\n]", text)
    insertion = first_table.start() if first_table else len(text)
    prefix = text[:insertion]
    separator = "" if not prefix or prefix.endswith(("\n", "\r")) else "\n"
    return prefix + separator + _CODEX_ELICITATION_POLICY + "\n" + text[insertion:]


def _replace_known_invalid_elicitation_policy(text: str) -> str | None:
    """Repair only the legacy multiline inline policy emitted by this setup flow."""
    if re.search(r"(?m)^[ \t]*\[approval_policy(?:\.granular)?\]", text):
        return None

    pattern = re.compile(
        r"""(?mx)
        ^[ \t]*approval_policy[ \t]*=[ \t]*\{[ \t]*
        granular[ \t]*=[ \t]*\{[ \t]*\r?\n
        (?:
            [ \t]*(?:mcp_elicitations|rules|sandbox_approval|request_permissions|skill_approval)
            [ \t]*=[ \t]*(?:true|false)[ \t]*,?[ \t]*(?:\#[^\r\n]*)?\r?\n
        )+
        [ \t]*\}[ \t]*\}[ \t]*(?:\#[^\r\n]*)?(?:\r?\n|$)
        """
    )
    match = pattern.search(text)
    if match is None or len(pattern.findall(text)) != 1:
        return None

    return _insert_minimal_elicitation_policy(
        text[:match.start()] + text[match.end():]
    )


def _enable_granular_approvals(text: str) -> str:
    """Enable every required granular approval category without removing other keys."""
    table_header = re.search(
        r"(?m)^[ \t]*\[approval_policy\.granular\][ \t]*(?:\#[^\r\n]*)?(?:\r?\n|$)",
        text,
    )
    if table_header is not None:
        next_header = re.search(r"(?m)^[ \t]*\[[^\r\n]+\]", text[table_header.end():])
        table_end = table_header.end() + (
            next_header.start() if next_header else len(text[table_header.end():])
        )
        table_text = text[table_header.end():table_end]
        updated_table = table_text
        missing = []
        for field in GRANULAR_APPROVALS_ENABLED:
            assignment = re.search(
                rf"(?m)^(?P<indent>[ \t]*){field}[ \t]*=[^\r\n]*(?P<newline>\r?\n|$)",
                updated_table,
            )
            if assignment is None:
                missing.append(f"{field} = true\n")
                continue
            replacement = (
                f"{assignment.group('indent')}{field} = true"
                f"{assignment.group('newline')}"
            )
            updated_table = (
                updated_table[:assignment.start()]
                + replacement
                + updated_table[assignment.end():]
            )
        if missing:
            updated_table = "".join(missing) + updated_table
        return text[:table_header.end()] + updated_table + text[table_end:]

    inline = re.search(
        r"""(?mx)
        ^(?P<prefix>[ \t]*approval_policy[ \t]*=[ \t]*\{[ \t]*
        granular[ \t]*=[ \t]*\{)(?P<body>[^\r\n{}]*)(?P<suffix>\}[ \t]*\})
        (?P<trailing>[ \t]*(?:\#[^\r\n]*)?)(?P<newline>\r?\n|$)
        """,
        text,
    )
    if inline is None:
        raise ConfigError(
            "could not safely locate the granular approval_policy to enable "
            "approval prompts"
        )

    body = inline.group("body")
    updated_body = body
    missing = []
    for field in GRANULAR_APPROVALS_ENABLED:
        assignment = re.search(
            rf"\b{field}[ \t]*=[ \t]*(?:true|false)\b", updated_body
        )
        if assignment is None:
            missing.append(f"{field} = true")
            continue
        updated_body = (
            updated_body[:assignment.start()]
            + f"{field} = true"
            + updated_body[assignment.end():]
        )
    if missing:
        updated_body = " " + ", ".join(missing) + ", " + updated_body.lstrip()
    return (
        text[:inline.start()]
        + inline.group("prefix")
        + updated_body
        + inline.group("suffix")
        + inline.group("trailing")
        + inline.group("newline")
        + text[inline.end():]
    )


def _write_codex_policy(
    config_path: Path,
    snapshot: tuple[bool, bytes | None, int],
    updated: str,
) -> None:
    backup_path = config_path.with_name(config_path.name + ".pdf-learner.bak")
    try:
        config_path.parent.mkdir(parents=True, exist_ok=True)
        if snapshot[0]:
            shutil.copy2(config_path, backup_path)
        _atomic_write(config_path, updated.encode("utf-8"), snapshot[2])
    except OSError as exc:
        try:
            _restore(config_path, snapshot)
        except OSError as restore_exc:
            raise ConfigError(
                f"{config_path}: approval policy update failed ({exc}); "
                f"rollback failed ({restore_exc})"
            ) from restore_exc
        raise ConfigError(
            f"{config_path}: approval policy update failed ({exc}); "
            "changes were rolled back"
        ) from exc


def ensure_codex_elicitation_allowed(config_path: Path) -> bool:
    """Ensure the global policy allows MCP forms without changing other categories."""
    if config_path.is_symlink() or (config_path.exists() and not config_path.is_file()):
        raise ConfigError(f"{config_path}: expected a regular non-symlink TOML file")
    if not config_path.exists():
        _write_codex_policy(config_path, (False, None, 0o600), _CODEX_ELICITATION_POLICY + "\n")
        return True

    snapshot = _snapshot(config_path)
    assert snapshot[1] is not None
    try:
        text = snapshot[1].decode("utf-8")
        data = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        if not isinstance(exc, tomllib.TOMLDecodeError):
            raise ConfigError(f"{config_path}: invalid TOML ({exc})") from exc
        repaired = _replace_known_invalid_elicitation_policy(text)
        if repaired is None:
            raise ConfigError(f"{config_path}: invalid TOML ({exc})") from exc
        try:
            data = tomllib.loads(repaired)
        except tomllib.TOMLDecodeError as repair_exc:
            raise ConfigError(
                f"{config_path}: known approval policy repair produced invalid TOML ({repair_exc})"
            ) from repair_exc
        _write_codex_policy(config_path, snapshot, repaired)
        return True

    policy = data.get("approval_policy")
    if isinstance(policy, str) and policy in {"on-request", "untrusted"}:
        return False
    if isinstance(policy, dict):
        granular = policy.get("granular")
        if granular == GRANULAR_APPROVALS_ENABLED:
            return False
        if not isinstance(granular, dict):
            raise ConfigError(
                f"{config_path}: granular approval_policy has an unsupported shape"
            )
        _write_codex_policy(
            config_path,
            snapshot,
            _enable_granular_approvals(text),
        )
        return True
    if policy not in {None, "never"}:
        raise ConfigError(
            f"{config_path}: unsupported approval_policy value {policy!r}"
        )

    if policy is None:
        updated = _insert_minimal_elicitation_policy(text)
    else:
        first_table = re.search(r"(?m)^[ \t]*\[[^\r\n]", text)
        root_text = text[:first_table.start()] if first_table else text
        assignment = re.search(
            r"""(?mx)
            ^(?P<indent>[ \t]*)
            approval_policy[ \t]*=[ \t]*
            (?P<quote>["'])never(?P=quote)
            (?P<trailing>[ \t]*(?:\#[^\r\n]*)?)
            (?P<newline>\r?\n|$)
            """,
            root_text,
        )
        if assignment is None:
            raise ConfigError(
                f"{config_path}: could not safely locate the root "
                "approval_policy = \"never\" assignment"
            )
        trailing = assignment.group("trailing").strip()
        preserved_comment = (
            f"{assignment.group('indent')}{trailing}{assignment.group('newline')}"
            if trailing.startswith("#")
            else ""
        )
        without_policy = (
            text[:assignment.start()]
            + preserved_comment
            + text[assignment.end():]
        )
        updated = _insert_minimal_elicitation_policy(without_policy)
    _write_codex_policy(config_path, snapshot, updated)
    return True


def ensure_codex_pdf_learner_tools_auto_approved(config_path: Path) -> bool:
    """Set the local pdf-learner server's default MCP approval to approve.

    The Codex CLI creates the server table through ``codex mcp add``.  This
    narrowly updates that table afterwards, leaving every other MCP server and
    any per-tool overrides intact.
    """
    if not config_path.exists():
        raise ConfigError(
            f"{config_path}: Codex MCP registration did not create config.toml"
        )
    if not config_path.is_file() or config_path.is_symlink():
        raise ConfigError(f"{config_path}: expected a regular non-symlink TOML file")

    snapshot = _snapshot(config_path)
    assert snapshot[1] is not None
    try:
        text = snapshot[1].decode("utf-8")
        data = tomllib.loads(text)
    except (UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"{config_path}: invalid TOML ({exc})") from exc

    server = data.get("mcp_servers", {}).get("pdf-learner")
    if not isinstance(server, dict):
        raise ConfigError(
            f"{config_path}: Codex MCP registration did not create the pdf-learner server"
        )
    if server.get("default_tools_approval_mode") == "approve":
        return False

    header = re.search(
        r"(?m)^[ \t]*\[mcp_servers\.pdf-learner\][ \t]*(?:\#[^\r\n]*)?(?:\r?\n|$)",
        text,
    )
    if header is None:
        raise ConfigError(
            f"{config_path}: could not safely locate the pdf-learner MCP server table"
        )
    next_header = re.search(r"(?m)^[ \t]*\[[^\r\n]+\]", text[header.end():])
    table_end = header.end() + (next_header.start() if next_header else len(text[header.end():]))
    table_text = text[header.end():table_end]
    assignment = re.search(
        r"(?m)^(?P<indent>[ \t]*)default_tools_approval_mode[ \t]*=[^\r\n]*(?P<newline>\r?\n|$)",
        table_text,
    )
    if assignment is None:
        updated = (
            text[:header.end()]
            + _CODEX_PDF_LEARNER_DEFAULT_TOOL_APPROVAL
            + "\n"
            + text[header.end():]
        )
    else:
        replacement = (
            f"{assignment.group('indent')}{_CODEX_PDF_LEARNER_DEFAULT_TOOL_APPROVAL}"
            f"{assignment.group('newline')}"
        )
        updated_table = (
            table_text[:assignment.start()] + replacement + table_text[assignment.end():]
        )
        updated = text[:header.end()] + updated_table + text[table_end:]

    backup_path = config_path.with_name(config_path.name + ".pdf-learner.bak")
    try:
        shutil.copy2(config_path, backup_path)
        _atomic_write(config_path, updated.encode("utf-8"), snapshot[2])
    except OSError as exc:
        try:
            _restore(config_path, snapshot)
        except OSError as restore_exc:
            raise ConfigError(
                f"{config_path}: tool approval update failed ({exc}); "
                f"rollback failed ({restore_exc})"
            ) from restore_exc
        raise ConfigError(
            f"{config_path}: tool approval update failed ({exc}); changes were rolled back"
        ) from exc
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--command", required=True)
    parser.add_argument("--cache-dir", required=True, type=Path)
    args = parser.parse_args()

    try:
        codex_bin = shutil.which("codex")
        if codex_bin is None:
            raise ConfigError("Codex CLI was not found on PATH")
        codex_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
        policy_updated = ensure_codex_elicitation_allowed(codex_home / "config.toml")
        apply_codex_cli_config(
            command=args.command,
            cache_dir=args.cache_dir,
            codex_bin=codex_bin,
        )
        tool_approval_updated = ensure_codex_pdf_learner_tools_auto_approved(
            codex_home / "config.toml"
        )
    except (ConfigError, OSError) as exc:
        print(f"❌ Failed to update MCP config: {exc}", file=os.sys.stderr)
        return 1

    if policy_updated:
        print("✅ Updated Codex approval policy to allow MCP elicitation prompts")
    else:
        print(
            "ℹ️ Codex global config does not block MCP elicitation "
            "(CLI, profile, or managed overrides may still apply)"
        )
    print("✅ Successfully registered and verified Codex CLI MCP config")
    if tool_approval_updated:
        print("✅ Configured all pdf-learner MCP tools for automatic approval")
    else:
        print("ℹ️ All pdf-learner MCP tools are already automatically approved")
    print(f"⚠️ {_CODEX_ELICITATION_LAUNCH_GUIDANCE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
