"""Local coding agent.

It can list, read, search, patch, and run commands inside one project folder,
then ask the model to fix what failed. It stops at a step cap. It does not
solve captchas, rotate accounts, or leave the allowed project root.
"""

import argparse
import hashlib
import json
import os
import re
import shlex
import subprocess
from pathlib import Path

import llm_bridge

PROJECT_ROOT = Path(__file__).resolve().parent
HARD_CAP = 12
DEFAULT_STEPS = 8
COMMAND_TIMEOUT = 40

SECRET_DIRS = {
    "cookies",
    "node_modules",
    "venv",
    ".git",
    "__pycache__",
    "agent_sessions",
}
SECRET_FILES = {
    ".env",
    ".env.local",
    ".env.production",
    "auth_session.json",
    "id_rsa",
    "id_ed25519",
}
BLOCKED_BINS = {
    "sudo",
    "su",
    "bash",
    "sh",
    "zsh",
    "dash",
    "fish",
    "curl",
    "wget",
    "nc",
    "ncat",
    "ssh",
    "scp",
    "dd",
    "mkfs",
    "reboot",
    "shutdown",
    "mount",
    "umount",
    "systemctl",
}
BLOCKED_SNIPPETS = ("/etc/", "/root", ".env", "auth_session", "id_rsa", "sudo ", "rm -rf /", "..")


class AgentError(RuntimeError):
    pass


def allowed_root() -> Path:
    configured = os.getenv("AGENT_ROOT", str(Path.home() / "Documents" / "Project"))
    return Path(configured).resolve()


def resolve_workspace(requested: str | None) -> Path:
    root = allowed_root()
    raw = requested or os.getenv("AGENT_WORKSPACE", str(PROJECT_ROOT))
    path = Path(raw)
    path = path.resolve() if path.is_absolute() else (root / path).resolve()
    if path != root and not path.is_relative_to(root):
        raise AgentError(f"Workspace must stay inside {root}")
    if not path.is_dir():
        raise AgentError("Workspace folder does not exist")
    return path


def _blocked_part(part: str) -> bool:
    name = part.lower()
    return name in SECRET_DIRS or name in SECRET_FILES or name.startswith(".env") or name.endswith(".pem")


def resolve_inside(workspace: Path, relative: str) -> Path:
    raw = (relative or ".").strip()
    if raw.startswith(("/", "~")):
        raise AgentError("Use a path inside the workspace")
    path = (workspace / raw).resolve()
    if path != workspace and not path.is_relative_to(workspace):
        raise AgentError("Path escapes the workspace")
    parts = path.relative_to(workspace).parts
    if any(_blocked_part(part) for part in parts):
        raise AgentError("That path is blocked")
    current = workspace
    for part in parts:
        current = current / part
        if current.is_symlink():
            raise AgentError("Symlinks are blocked")
    return path


def _clip(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + "\n…[truncated]"


def _sketch(workspace: Path, limit: int = 80) -> str:
    lines = []
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = sorted(name for name in dirnames if not _blocked_part(name))
        rel = Path(dirpath).relative_to(workspace)
        depth = 0 if rel == Path(".") else len(rel.parts)
        if depth > 2:
            dirnames.clear()
            continue
        for name in sorted(filenames):
            if _blocked_part(name):
                continue
            shown = name if rel == Path(".") else str(rel / name)
            lines.append(shown)
            if len(lines) >= limit:
                return "\n".join(lines) + "\n…"
    return "\n".join(lines) or "(empty workspace)"


def _scrub_env() -> dict:
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(word in upper for word in ("TOKEN", "PASSWORD", "SECRET", "COOKIE", "API_KEY")):
            continue
        if key in {"EMAIL", "PASSWORD"}:
            continue
        env[key] = value
    return env


def list_path(workspace: Path, relative: str) -> str:
    path = resolve_inside(workspace, relative)
    if not path.exists():
        raise AgentError("Path does not exist")
    if path.is_file():
        return path.name
    names = []
    for child in sorted(path.iterdir(), key=lambda item: item.name):
        if _blocked_part(child.name) or child.is_symlink():
            continue
        suffix = "/" if child.is_dir() else ""
        names.append(child.name + suffix)
        if len(names) >= 200:
            break
    return "\n".join(names) or "(empty)"


def read_file(workspace: Path, relative: str) -> str:
    path = resolve_inside(workspace, relative)
    if not path.is_file():
        raise AgentError("File does not exist")
    if path.stat().st_size > 200_000:
        raise AgentError("File is too large to read")
    data = path.read_bytes()
    if b"\0" in data[:1024]:
        raise AgentError("Binary files are blocked")
    return _clip(data.decode("utf-8", "replace"), 20_000)


def search_text(workspace: Path, query: str) -> str:
    needle = (query or "").strip()
    if len(needle) < 2:
        raise AgentError("Search query is too short")
    hits = []
    for dirpath, dirnames, filenames in os.walk(workspace):
        dirnames[:] = [name for name in dirnames if not _blocked_part(name)]
        for name in filenames:
            if _blocked_part(name):
                continue
            path = Path(dirpath) / name
            if path.is_symlink() or not path.is_file() or path.stat().st_size > 200_000:
                continue
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if needle not in text:
                continue
            rel = path.relative_to(workspace)
            for number, line in enumerate(text.splitlines(), start=1):
                if needle in line:
                    hits.append(f"{rel}:{number}: {line.strip()[:180]}")
                    if len(hits) >= 40:
                        return "\n".join(hits)
    return "\n".join(hits) or "No matches"


def write_file(workspace: Path, relative: str, content: str) -> str:
    path = resolve_inside(workspace, relative)
    if path == workspace:
        raise AgentError("Refusing to write the workspace root")
    if len(content or "") > 200_000:
        raise AgentError("Content is too large")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Wrote {path.relative_to(workspace)} ({len(content)} chars)"


def patch_file(workspace: Path, relative: str, old: str, new: str) -> str:
    if not old:
        raise AgentError("Patch old text is empty")
    path = resolve_inside(workspace, relative)
    if not path.is_file():
        raise AgentError("File does not exist")
    text = path.read_text(encoding="utf-8")
    count = text.count(old)
    if count == 0:
        raise AgentError("Old text was not found")
    if count > 1:
        raise AgentError(f"Old text matched {count} times. Include more context.")
    path.write_text(text.replace(old, new, 1), encoding="utf-8")
    return f"Patched {path.relative_to(workspace)}"


def run_command(workspace: Path, command: str) -> str:
    command = (command or "").strip()
    if not command:
        raise AgentError("Command is empty")
    lowered = command.lower()
    if any(snippet in lowered for snippet in BLOCKED_SNIPPETS):
        raise AgentError("Command blocked")
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        raise AgentError(f"Could not parse the command: {exc}") from exc
    if not argv:
        raise AgentError("Command is empty")
    program = Path(argv[0]).name
    if program in BLOCKED_BINS:
        raise AgentError(f"{program} is blocked")
    for arg in argv:
        if arg.startswith(("/", "~")):
            raise AgentError("Absolute paths in commands are blocked")
    try:
        completed = subprocess.run(
            argv,
            cwd=workspace,
            env=_scrub_env(),
            capture_output=True,
            text=True,
            timeout=COMMAND_TIMEOUT,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AgentError(f"Command not found: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise AgentError(f"Command timed out after {COMMAND_TIMEOUT}s") from exc
    output = (completed.stdout or "") + (("\n" + completed.stderr) if completed.stderr else "")
    status = "passed" if completed.returncode == 0 else f"exit {completed.returncode}"
    return _clip(f"{status}\n{output}".strip(), 8_000)


def dispatch(workspace: Path, action: dict) -> str:
    name = str(action.get("action") or "").strip().lower()
    if name == "list":
        return list_path(workspace, str(action.get("path") or "."))
    if name == "read":
        return read_file(workspace, str(action.get("path") or ""))
    if name == "search":
        return search_text(workspace, str(action.get("query") or ""))
    if name == "write":
        return write_file(workspace, str(action.get("path") or ""), str(action.get("content") or ""))
    if name == "patch":
        return patch_file(
            workspace,
            str(action.get("path") or ""),
            str(action.get("old") or ""),
            str(action.get("new") if action.get("new") is not None else ""),
        )
    if name == "run":
        return run_command(workspace, str(action.get("command") or ""))
    if name == "done":
        summary = str(action.get("summary") or "").strip()
        if not summary:
            raise AgentError("done requires a summary")
        return summary
    raise AgentError("Unknown action. Use list, read, search, write, patch, run, or done.")


def parse_action(text: str) -> dict:
    raw = (text or "").strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1)
    else:
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            raw = raw[start:end + 1]
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise AgentError(f"Reply was not JSON: {exc}") from exc
    if not isinstance(data, dict) or "action" not in data:
        raise AgentError("Reply JSON needs an action field")
    return data


def _prompt(goal: str, workspace: Path, sketch: str, notes: list[str]) -> str:
    history = "\n\n".join(notes[-4:]) or "No actions yet."
    return (
        "You are a local coding agent. Reply with one JSON object and no other text.\n"
        "Actions:\n"
        '{"action":"list","path":"."}\n'
        '{"action":"read","path":"relative/file"}\n'
        '{"action":"search","query":"text"}\n'
        '{"action":"patch","path":"file","old":"exact text","new":"replacement"}\n'
        '{"action":"write","path":"file","content":"full file"}\n'
        '{"action":"run","command":"python3 -m pytest -q"}\n'
        '{"action":"done","summary":"what changed and the check result"}\n'
        "Rules: inspect before editing. Prefer patch. After edits, run the check. "
        "If it fails, read the error and patch again. Finish with done only when the check passes "
        "or you cannot proceed. Never request .env, keys, cookies, or paths outside the workspace.\n\n"
        f"Workspace: {workspace}\n"
        f"Files:\n{sketch}\n\n"
        f"Goal:\n{goal}\n\n"
        f"Recent results:\n{history}"
    )


def run_agent(
    goal: str,
    workspace: str | None = None,
    max_steps: int = DEFAULT_STEPS,
    complete_fn=None,
) -> dict:
    goal = (goal or "").strip()
    if not goal:
        raise AgentError("Prompt is empty")
    steps_allowed = max(1, min(int(max_steps or DEFAULT_STEPS), HARD_CAP))
    folder = resolve_workspace(workspace)
    sketch = _sketch(folder)
    digest = hashlib.sha256(str(folder).encode()).hexdigest()[:12]
    session_name = f"agent-{digest}"
    caller = complete_fn or (
        lambda prompt, session_name: llm_bridge.complete(
            prompt,
            session_name=session_name,
            thinking=False,
            search=False,
        )
    )
    notes: list[str] = []
    steps = []
    for number in range(1, steps_allowed + 1):
        raw = caller(_prompt(goal, folder, sketch, notes), session_name=session_name)
        try:
            action = parse_action(raw)
            detail = dispatch(folder, action)
            ok = True
        except AgentError as exc:
            action = {"action": "invalid"}
            detail = str(exc)
            ok = False
        name = str(action.get("action") or "invalid")
        steps.append({"n": number, "action": name, "ok": ok, "detail": _clip(detail, 1500)})
        notes.append(f"Step {number} {name} ok={ok}\n{_clip(detail, 2000)}")
        notes[:] = notes[-4:]
        if name == "done" and ok:
            return {
                "success": True,
                "summary": detail,
                "response": detail,
                "steps": steps,
                "stopped": "done",
                "workspace": str(folder),
                "provider": llm_bridge.provider_name(),
            }
    summary = "Stopped at the step cap before the agent finished."
    return {
        "success": False,
        "summary": summary,
        "response": summary,
        "steps": steps,
        "stopped": "step_cap",
        "workspace": str(folder),
        "provider": llm_bridge.provider_name(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the local Orbit agent on one project folder.")
    parser.add_argument("goal")
    parser.add_argument("--workspace", default=None)
    parser.add_argument("--steps", type=int, default=DEFAULT_STEPS)
    args = parser.parse_args(argv)
    result = run_agent(args.goal, args.workspace, args.steps)
    for step in result["steps"]:
        print(f"[{step['n']}] {step['action']} {'ok' if step['ok'] else 'failed'}")
        print(step["detail"])
        print()
    print(result["summary"])
    return 0 if result["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
