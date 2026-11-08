"""AI CLIs Remote Control learns on its own: the `rc adapt` command.

The CLIs Remote Control knows natively (claude, codex, cursor's agent, pi…)
are spread over a few hand-written tables: the instructions file the machine
context goes into (agent_context.py), the profile wrapper (web/profiles.zsh),
how a pane's conversation is found and resumed (workspaces.py) and where its
replies are read (agents.py). A CLI released after them would get none of it.

When a CLI that is not on those tables runs in a Remote Control tab or a
herdr pane (or someone runs `rc adapt <cli>`), an AI CLI that is installed
(Claude, Codex or Gemini, with the primary user's account) is handed what can
be seen of it from here (its --help, its folders in HOME, the shape of its
session files, its README) and the web, and answers with an adapter: a small
JSON with the same facts the tables hold. It is validated (paths inside a
HOME, never key or token folders, known formats only) and applied right away:
everyone's agents get the context, its tabs come back after a restart, and the
devices show a note. Native support always wins over a learned adapter.

Only names and shapes leave this machine, never file contents: tokens and
conversations stay here. A CLI whose --help says nothing about AI is marked
as not one without asking any AI.
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import profiles

SCHEMA = 1
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")
FLAG_RE = re.compile(r"^--?[a-z][a-z0-9-]{0,31}$")
FIELD_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]*(\.[A-Za-z_$][A-Za-z0-9_$]*){0,4}$")
GLOB_PART_RE = re.compile(r"^[A-Za-z0-9._*?\[\]-]{1,64}$")
VALUE_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,24}$")
KEY_RE = re.compile(r"^[A-Za-z_$][A-Za-z0-9_$]{0,39}$")
SECRET_NAME_RE = re.compile(r"auth|token|credential|secret|password|passwd|cookie|key|session[-_]?key|oauth", re.I)
# Folders an adapter may never point at, whatever an AI answers.
PROTECTED = frozenset(
    {
        ".ssh", ".gnupg", ".aws", ".azure", ".kube", ".docker", ".password-store", ".pki",
        ".netrc", ".git-credentials", ".local", ".config", ".cache", ".mozilla", ".thunderbird",
    }
)
# Under these the next part names the CLI's own folder.
SHARED_ROOTS = (".config", ".local/share", ".local/state", ".cache")

# What an AI is asked at most: per CLI, and for every CLI in a day.
RETRY_AFTER_S = 24 * 3600
MAX_TRIES = 3
DAILY_BUDGET = 6
HELP_TIMEOUT_S = 6
AI_TIMEOUT_S = 300
SCAN_EVERY_S = 30

# Running programs that are never AI CLIs, so nothing is asked about them.
NOT_AI = frozenset(
    {
        "git", "gh", "npm", "npx", "pnpm", "yarn", "bun", "bunx", "deno", "node", "python", "python3",
        "pip", "pip3", "uv", "uvx", "pipx", "cargo", "rustc", "go", "make", "cmake", "ninja", "gcc", "clang",
        "docker", "podman", "kubectl", "helm", "terraform", "sudo", "su", "env", "time", "nice", "nohup",
        "ssh", "mosh", "scp", "rsync", "curl", "wget", "tmux", "screen", "herdr", "rc", "ttyd", "cloudflared",
        "watch", "tail", "less", "more", "man", "cat", "sleep", "ping", "journalctl", "systemctl",
        "java", "ruby", "perl", "php", "dotnet", "lua", "R", "julia", "ghci", "irb", "ipython", "jupyter",
    }
)
INTERPRETERS = frozenset({"node", "bun", "deno", "python", "python3", "ruby", "perl", "tsx", "ts-node"})
AI_HINT_RE = re.compile(
    r"\b(ai|a\.i\.|llm|llms|agent|agents|agentic|assistant|copilot|model|models|prompt|prompts|chat|"
    r"anthropic|claude|openai|gpt|gemini|mistral|ollama|deepseek|qwen|coding\s+agent|mcp)\b",
    re.I,
)

_cache: dict = {"key": None, "all": {}}
_learning = threading.Lock()
_last_scan = [0.0]


def adapters_dir() -> Path:
    return profiles.base_dir() / "cli-adapters"


def bins_file() -> Path:
    """The learned CLIs' names, one per line, for web/profiles.zsh."""
    return adapters_dir() / "bins"


def _tries_file() -> Path:
    return adapters_dir() / ".tries.json"


def native() -> frozenset[str]:
    from .workspaces import PROFILE_CLIS

    return PROFILE_CLIS


# --- reading ---------------------------------------------------------------


def load_all() -> dict[str, dict]:
    """Every saved answer by CLI name, AI CLIs or not."""
    folder = adapters_dir()
    try:
        key = folder.stat().st_mtime_ns
    except OSError:
        return {}
    if _cache["key"] == key:
        return _cache["all"]
    out: dict[str, dict] = {}
    for path in folder.glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        clean = validate(data, path.stem) if isinstance(data, dict) else None
        if clean:
            out[clean["cli"]] = clean
    _cache.update(key=key, all=out)
    return out


def learned() -> dict[str, dict]:
    """The adapters of AI CLIs that are not native, by every name they run as."""
    nat = native()
    out: dict[str, dict] = {}
    for adapter in load_all().values():
        if not adapter.get("is_ai_cli"):
            continue
        for name in adapter["bins"]:
            if name not in nat:
                out.setdefault(name, adapter)
    return out


def known() -> frozenset[str]:
    return frozenset(learned())


def get(cli: str) -> dict | None:
    return learned().get(cli or "")


# --- validation ------------------------------------------------------------


def _split_own(parts: list[str]) -> int:
    """How many leading parts name a CLI's own folder (".foo", ".config/foo",
    ".local/share/foo"), 0 when the path is not inside one."""
    if parts[0] in (".config", ".cache"):
        depth = 2
    elif parts[0] == ".local" and len(parts) > 1 and parts[1] in ("share", "state"):
        depth = 3
    elif parts[0].startswith(".") and parts[0] not in PROTECTED:
        depth = 1
    else:
        return 0
    if len(parts) < depth:
        return 0
    own = parts[depth - 1]
    if own in PROTECTED or own in (".", "..") or any(ch in own for ch in "*?[]"):
        return 0
    return depth


def _rel(value: object, *, glob: bool = False) -> str:
    """A path relative to a HOME inside a CLI's own folder, or ""."""
    if not isinstance(value, str) or not value or len(value) > 160:
        return ""
    if value.startswith(("/", "~")) or "\\" in value:
        return ""
    parts = [p for p in value.split("/") if p not in ("", ".")]
    if not parts or len(parts) > 8 or ".." in parts:
        return ""
    if any(not GLOB_PART_RE.match(p) for p in parts):
        return ""
    if not glob and any(ch in value for ch in "*?[]"):
        return ""
    return "/".join(parts) if _split_own(parts) else ""


def _own_dirs(value: object) -> list[str]:
    out = []
    for item in value if isinstance(value, list) else []:
        rel = _rel(item)
        parts = rel.split("/") if rel else []
        if parts and len(parts) == _split_own(parts) and rel not in out:
            out.append(rel)
    return out[:6]


def validate(raw: dict, cli: str) -> dict | None:
    """The adapter with only what is safe and understood, or None."""
    name = raw.get("cli") if isinstance(raw.get("cli"), str) else cli
    if not NAME_RE.match(name or "") or name != cli:
        return None
    base = {"schema": SCHEMA, "cli": name, "is_ai_cli": bool(raw.get("is_ai_cli")), "source": str(raw.get("source") or "")[:20]}
    base["learned"] = int(raw["learned"]) if isinstance(raw.get("learned"), (int, float)) else 0
    if not base["is_ai_cli"]:
        base["bins"] = [name]
        return base
    bins = [b for b in raw.get("bins") or [] if isinstance(b, str) and NAME_RE.match(b)]
    bins = list(dict.fromkeys([name, *bins]))[:4]
    title = raw.get("name") if isinstance(raw.get("name"), str) else ""
    title = " ".join(title.split())[:40] or name
    dirs = _own_dirs(raw.get("home_dirs"))
    out = {**base, "name": title, "bins": bins, "home_dirs": dirs, "context_file": "", "sessions": None, "resume": None}
    ctx = _rel(raw.get("context_file"))
    if ctx and ctx.lower().endswith(".md") and any(ctx.startswith(d + "/") for d in dirs):
        out["context_file"] = ctx
    out["sessions"] = _sessions(raw.get("sessions"), dirs)
    out["resume"] = _resume(raw.get("resume"), out["sessions"])
    return out


def _sessions(raw: object, dirs: list[str]) -> dict | None:
    if not isinstance(raw, dict):
        return None
    pattern = _rel(raw.get("glob"), glob=True)
    if not pattern or not any(pattern.startswith(d + "/") for d in dirs):
        return None
    fmt = raw.get("format")
    if fmt not in ("jsonl", "json"):
        return None
    out = {"glob": pattern, "format": fmt}
    for key in ("role", "text"):
        field = raw.get(key)
        if not isinstance(field, str) or not FIELD_RE.match(field):
            return None
        out[key] = field
    items = raw.get("items")
    out["items"] = items if isinstance(items, str) and FIELD_RE.match(items) else ""
    for key, default in (("user", "user"), ("assistant", "assistant")):
        value = raw.get(key)
        out[key] = value if isinstance(value, str) and VALUE_RE.match(value) else default
    return out


def _resume(raw: object, sessions: dict | None) -> dict | None:
    if not isinstance(raw, dict):
        return None
    flag = raw.get("flag")
    by = raw.get("by")
    if not isinstance(flag, str) or not FLAG_RE.match(flag) or by not in ("id", "last"):
        return None
    out = {"flag": flag, "by": by, "id_re": ""}
    if by == "id":
        if not sessions:
            return None
        pattern = raw.get("id_re")
        if isinstance(pattern, str) and 0 < len(pattern) <= 120:
            try:
                re.compile(pattern)
                out["id_re"] = pattern
            except re.error:
                pass
    return out


# --- saving ----------------------------------------------------------------


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def write_bins() -> None:
    names = sorted(known())
    text = "".join(f"{n}\n" for n in names)
    path = bins_file()
    try:
        if path.read_text(encoding="utf-8") == text:
            return
    except OSError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(".bins.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def save(adapter: dict) -> dict | None:
    clean = validate(adapter, adapter.get("cli", ""))
    if not clean:
        return None
    _write_json(adapters_dir() / f"{clean['cli']}.json", clean)
    _cache["key"] = None
    return clean


def apply() -> None:
    """Bring everything that reads the adapters up to date."""
    from . import agent_context

    write_bins()
    try:
        agent_context.sync()
    except Exception:
        pass


def forget(cli: str) -> bool:
    """Drop what was learned of ``cli`` (its context block too); it is not
    learned again on its own until someone runs `rc adapt` for it."""
    from . import agent_context

    adapter = load_all().get(cli)
    path = adapters_dir() / f"{cli}.json"
    if not adapter or not path.exists():
        return False
    rel = adapter.get("context_file") or ""
    if rel and rel not in {row[0] for row in agent_context.TARGETS}:
        agent_context.drop_file(rel)
    _write_json(path, {"schema": SCHEMA, "cli": cli, "is_ai_cli": False, "source": "forgotten", "learned": int(time.time())})
    _cache["key"] = None
    apply()
    return True


@contextmanager
def _locked():
    adapters_dir().mkdir(parents=True, exist_ok=True)
    with open(adapters_dir() / ".lock", "a+") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


def _tries() -> dict:
    try:
        data = json.loads(_tries_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _note_try(cli: str, ok: bool, why: str = "") -> None:
    data = _tries()
    now = int(time.time())
    entry = data.get(cli) if isinstance(data.get(cli), dict) else {}
    data[cli] = {"t": now, "n": 0 if ok else int(entry.get("n") or 0) + 1, "why": why[:200]}
    data["_ai"] = [t for t in data.get("_ai") or [] if isinstance(t, (int, float)) and now - t < 86400] + [now]
    _write_json(_tries_file(), data)


def may_try(cli: str, now: float | None = None) -> bool:
    """False while a CLI waits after a failure, or the day's budget is spent."""
    now = time.time() if now is None else now
    data = _tries()
    entry = data.get(cli) if isinstance(data.get(cli), dict) else {}
    if entry and (int(entry.get("n") or 0) >= MAX_TRIES or now - float(entry.get("t") or 0) < RETRY_AFTER_S):
        return False
    spent = [t for t in data.get("_ai") or [] if isinstance(t, (int, float)) and now - t < 86400]
    return len(spent) < DAILY_BUDGET


# --- what can be seen from here --------------------------------------------


def _home() -> Path:
    """The primary user's HOME (profiles.base_dir() lives in it)."""
    return profiles.base_dir().parents[2]


def _run(cmd: list[str], timeout: float, cwd: str | None = None) -> str:
    env = {**os.environ, "HOME": str(_home()), "RC_PROFILE": "", "NO_COLOR": "1", "TERM": "dumb"}
    try:
        done = subprocess.run(
            cmd, env=env, cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
            timeout=timeout, errors="replace", start_new_session=True,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    return (done.stdout or "") + (done.stderr or "")


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*\x07", "", text)


def _candidate_dirs(cli: str, home: Path) -> list[str]:
    stem = cli.split("-")[0]
    out = []
    for item in sorted(home.iterdir()) if home.is_dir() else []:
        n = item.name
        if item.is_dir() and n.startswith(".") and n not in PROTECTED and stem in n.lower():
            out.append(n)
    for root in SHARED_ROOTS:
        base = home / root
        if base.is_dir():
            for item in sorted(base.iterdir()):
                if item.is_dir() and stem in item.name.lower():
                    out.append(f"{root}/{item.name}")
    return out[:8]


def _tree(home: Path, rel: str, limit: int = 120) -> list[str]:
    """Names (never contents) under a folder, a few levels down."""
    out: list[str] = []
    root = home / rel
    for dirpath, dirnames, filenames in os.walk(root):
        depth = Path(dirpath).relative_to(root).parts
        if len(depth) >= 3:
            dirnames[:] = []
        dirnames.sort()
        for name in sorted(filenames)[:15]:
            path = Path(dirpath) / name
            try:
                size = path.stat().st_size
            except OSError:
                continue
            out.append(f"{path.relative_to(home)} ({size} B)")
            if len(out) >= limit:
                return out
        if len(dirnames) > 12:
            out.append(f"{Path(dirpath).relative_to(home)}/… {len(dirnames)} folders")
            dirnames[:] = dirnames[:12]
    return out


def _shape(value: object, key: str = "", depth: int = 0) -> object:
    """The structure of a JSON value with its text taken out (roles and types
    stay, they are needed to read replies)."""
    if depth > 5:
        return "…"
    if isinstance(value, dict):
        out = {}
        for i, k in enumerate(list(value)[:20]):
            # A key that is not a field name is data too (an id, a path, a model).
            name = k if KEY_RE.match(k) else f"<key{i}>"
            out[name] = _shape(value[k], k, depth + 1)
        return out
    if isinstance(value, list):
        return [_shape(v, key, depth + 1) for v in value[:2]]
    if isinstance(value, str):
        if key in ("role", "type", "kind", "author", "sender", "from") and VALUE_RE.match(value):
            return value
        return "<text>"
    if isinstance(value, bool) or value is None:
        return value
    return "<number>"


def _session_shapes(home: Path, dirs: list[str]) -> list[dict]:
    found: list[tuple[float, Path]] = []
    for rel in dirs:
        for pattern in ("**/*.jsonl", "**/*.json"):
            for path in (home / rel).glob(pattern):
                try:
                    st = path.stat()
                except OSError:
                    continue
                if 0 < st.st_size < 50_000_000 and not SECRET_NAME_RE.search(path.name):
                    found.append((st.st_mtime, path))
    out = []
    for _mtime, path in sorted(found, reverse=True)[:3]:
        try:
            with path.open(encoding="utf-8", errors="replace") as handle:
                head = handle.read(200_000)
        except OSError:
            continue
        lines = []
        if path.suffix == ".jsonl":
            for line in head.splitlines()[:6]:
                try:
                    lines.append(_shape(json.loads(line)))
                except ValueError:
                    continue
        else:
            try:
                lines.append(_shape(json.loads(head)))
            except ValueError:
                continue
        if lines:
            out.append({"file": str(path.relative_to(home)), "shape": lines})
    return out


def _readme(binary: str) -> str:
    try:
        real = Path(os.path.realpath(binary))
    except OSError:
        return ""
    for parent in list(real.parents)[:5]:
        for name in ("README.md", "readme.md", "README"):
            path = parent / name
            if path.is_file():
                try:
                    return path.read_text(encoding="utf-8", errors="replace")[:6000]
                except OSError:
                    return ""
    return ""


def evidence(cli: str) -> dict:
    """What is known of ``cli`` without asking any AI."""
    binary = shutil.which(cli)
    if not binary:
        return {}
    home = _home()
    help_text = _strip_ansi(_run([binary, "--help"], HELP_TIMEOUT_S))[:8000]
    version = _strip_ansi(_run([binary, "--version"], HELP_TIMEOUT_S)).strip()[:200]
    dirs = _candidate_dirs(cli, home)
    return {
        "cli": cli,
        "path": binary,
        "realpath": os.path.realpath(binary),
        "version": version,
        "help": help_text,
        "readme": _readme(binary),
        "home_dirs_found": {d: _tree(home, d) for d in dirs},
        "session_files": _session_shapes(home, dirs),
    }


def looks_like_ai(ev: dict) -> bool:
    text = " ".join(str(ev.get(k) or "") for k in ("cli", "help", "readme", "realpath"))
    return len(AI_HINT_RE.findall(text)) >= 2


# --- asking an AI ------------------------------------------------------------

PROMPT = """You are helping Remote Control, a tool that runs several AI coding CLIs on one shared Linux machine, support a CLI it does not know yet: `{cli}`.

Find out, from the evidence below and the CLI's official documentation on the web (search for it), these facts. Never guess: leave a field out (or null) when you are not sure.

1. is_ai_cli: is `{cli}` an interactive AI coding agent / assistant CLI (like Claude Code, Codex, Gemini CLI, Aider, opencode)? A plain developer tool is not.
2. name: its product name, short.
3. bins: the executable names it is started with.
4. home_dirs: the folders, relative to $HOME, where it keeps its config, logins or data (".foo", ".config/foo", ".local/share/foo").
5. context_file: the GLOBAL (user-level, all projects) instructions / memory / rules file it reads at start, relative to $HOME, like ".claude/CLAUDE.md" or ".codex/AGENTS.md". Must be a markdown file inside one of home_dirs. Leave it out if it only reads project files.
6. sessions: where it saves each conversation, if in JSON: {{"glob": "<pattern relative to $HOME, inside home_dirs, ** allowed>", "format": "jsonl" (one JSON per line, one message per line) or "json" (one document), "items": "<dotted path to the list of messages, json only>", "role": "<dotted path to the role inside a message>", "text": "<dotted path to the text: a string, or a list of {{type, text}} parts>", "user": "<role value of the user>", "assistant": "<role value of the model>"}}. Use the session_files shapes below.
7. resume: how to reopen a conversation: {{"flag": "--resume", "by": "id"}} when the flag takes the conversation id and that id is in the session file name (then also "id_re": a regex with one group that takes the id out of the file path, if the name is not just the id), or {{"flag": "--continue", "by": "last"}} when a bare flag reopens the latest one.

Answer with ONLY one JSON object, no prose:
{{"cli": "{cli}", "is_ai_cli": true, "name": "...", "bins": ["..."], "home_dirs": ["..."], "context_file": "...", "sessions": {{...}}, "resume": {{...}}}}

Evidence gathered on this machine (file names and JSON shapes only; contents were removed):
{evidence}
"""


def runners() -> list[tuple[str, list[str]]]:
    """The AI CLIs that can answer, best first, as (name, argv before the prompt)."""
    out = []
    claude = shutil.which("claude")
    if claude:
        out.append(("claude", [claude, "--no-session-persistence", "--output-format", "text",
                               "--allowedTools", "WebSearch", "WebFetch",
                               "--disallowedTools", "Bash", "Edit", "Write", "NotebookEdit", "-p"]))
    codex = shutil.which("codex")
    if codex:
        out.append(("codex", [codex, "exec", "--sandbox", "read-only", "--skip-git-repo-check"]))
    gemini = shutil.which("gemini")
    if gemini:
        out.append(("gemini", [gemini, "-p"]))
    return out


def parse_answer(text: str) -> dict | None:
    text = (text or "").strip()
    candidates = [text]
    candidates += re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if "{" in text:
        candidates.append(text[text.find("{") : text.rfind("}") + 1])
    for chunk in candidates:
        try:
            data = json.loads(chunk)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def ask_ai(cli: str, ev: dict) -> tuple[dict | None, str]:
    """(raw answer, which AI) from the first AI CLI that gives one."""
    prompt = PROMPT.format(cli=cli, evidence=json.dumps(ev, ensure_ascii=False, indent=1)[:40000])
    with tempfile.TemporaryDirectory(prefix="rc-adapt-") as empty:
        for name, argv in runners():
            answer = parse_answer(_run([*argv, prompt], AI_TIMEOUT_S, cwd=empty))
            if answer:
                return answer, name
    return None, ""


# --- learning ----------------------------------------------------------------


def learn(cli: str, force: bool = False, notify: bool = True) -> tuple[dict | None, str]:
    """Learn ``cli`` (or say why not). Returns (adapter, message)."""
    if not NAME_RE.match(cli or ""):
        return None, f"{cli!r} no es un nombre de programa válido"
    if cli in native():
        return None, f"{cli} ya tiene soporte nativo"
    with _locked():
        have = load_all().get(cli)
        if have and not force:
            return (have if have.get("is_ai_cli") else None), (
                f"{cli} ya está aprendido" if have.get("is_ai_cli") else f"{cli} está marcado como no-IA (usa --force)"
            )
        ev = evidence(cli)
        if not ev:
            return None, f"{cli} no está en el PATH"
        if not force and not looks_like_ai(ev):
            save({"cli": cli, "is_ai_cli": False, "source": "heuristic", "learned": int(time.time())})
            return None, f"{cli} no parece un CLI de IA"
        if not runners():
            _note_try(cli, False, "no AI CLI installed")
            return None, "No hay ningún CLI de IA instalado para investigarlo (claude, codex o gemini)"
        raw, by = ask_ai(cli, ev)
        if not raw:
            _note_try(cli, False, "no answer")
            return None, f"Ninguna IA dio una respuesta válida sobre {cli}"
        adapter = save({**raw, "cli": cli, "source": by, "learned": int(time.time())})
        if not adapter:
            _note_try(cli, False, "invalid answer")
            return None, f"La respuesta sobre {cli} no pasó la validación"
        _note_try(cli, True)
    apply()
    if adapter.get("is_ai_cli") and notify:
        _announce(adapter)
    if not adapter.get("is_ai_cli"):
        return None, f"{cli} no es un CLI de IA (según {by})"
    return adapter, f"Aprendí a usar {adapter['name']} ({summary(adapter)})"


def summary(adapter: dict) -> str:
    parts = []
    if adapter.get("context_file"):
        parts.append(f"contexto en ~/{adapter['context_file']}")
    parts.append("perfiles")
    if adapter.get("resume"):
        parts.append("reanudar")
    if adapter.get("sessions"):
        parts.append("leer respuestas")
    return ", ".join(parts)


def _announce(adapter: dict) -> None:
    from .agents import add_note

    try:
        add_note({"t": time.time(), "kind": "adapter", "cli": adapter["cli"], "name": adapter["name"], "what": summary(adapter)})
    except OSError:
        pass


# --- noticing new CLIs ---------------------------------------------------------


def _name_of(argv: list[str]) -> str:
    if not argv:
        return ""
    name = os.path.basename(argv[0]).lstrip("-")
    if name in INTERPRETERS:
        for arg in argv[1:]:
            if not arg.startswith("-"):
                name = re.sub(r"\.(c|m)?(js|ts)$|\.py$", "", os.path.basename(arg))
                break
        else:
            return ""
    return name


def _worth_asking(name: str) -> bool:
    from .workspaces import AUTO_RESUME, SHELLS

    if not NAME_RE.match(name or "") or name in NOT_AI or name in SHELLS or name in AUTO_RESUME:
        return False
    if name in native() or name in load_all():
        return False
    return bool(shutil.which(name))


def candidates() -> set[str]:
    """Programs running in tabs and herdr panes that may be new AI CLIs."""
    from .agents import _herdr
    from .history import TMUX_SOCKET, _tmux
    from .workspaces import foreground_argv

    names: set[str] = set()
    raw = _tmux(TMUX_SOCKET, "list-panes", "-a", "-F", "#{session_name}\t#{pane_pid}", text=True)
    for line in (raw.stdout if raw.returncode == 0 else "").splitlines():
        parts = line.split("\t")
        if len(parts) == 2 and parts[1].isdigit() and not parts[0].startswith("hv-"):
            names.add(_name_of(foreground_argv(int(parts[1]))))
    for pane in ((_herdr("pane", "list") or {}).get("panes") or []):
        if not isinstance(pane, dict) or not pane.get("pane_id"):
            continue
        # herdr's own agent names are not command names (its "cursor" is
        # Cursor Agent's `agent`): the process in the foreground is what counts.
        info = (_herdr("pane", "process-info", "--pane", pane["pane_id"]) or {}).get("process_info") or {}
        shell = info.get("shell_pid")
        if isinstance(shell, int) and shell > 0:
            names.add(_name_of(foreground_argv(shell)))
    return {n for n in names if _worth_asking(n)}


def scan(now: float | None = None) -> None:
    """Called every few seconds (proxy.py): learns one new CLI in the background."""
    now = time.monotonic() if now is None else now
    if now - _last_scan[0] < SCAN_EVERY_S or _learning.locked():
        return
    _last_scan[0] = now
    todo = sorted(n for n in candidates() if may_try(n))
    if not todo:
        return

    def work(name: str) -> None:
        with _learning:
            try:
                learn(name)
            except Exception:
                pass

    threading.Thread(target=work, args=(todo[0],), daemon=True).start()


# --- using an adapter ----------------------------------------------------------


def _dig(value: object, path: str) -> object:
    for key in path.split(".") if path else []:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _text_of(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str) and item.get("type", "text") in (
                "text", "input_text", "output_text", "markdown",
            ):
                parts.append(item["text"])
        return "\n\n".join(p for p in parts if p.strip())
    return ""


def turns(path: Path, adapter: dict) -> list[tuple[str, str]]:
    """(role, text) of every message in a session file, read as the adapter says."""
    spec = adapter.get("sessions") or {}
    if not spec:
        return []
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    items: list = []
    if spec["format"] == "jsonl":
        for line in raw.splitlines():
            try:
                items.append(json.loads(line))
            except ValueError:
                continue
    else:
        try:
            doc = json.loads(raw)
        except ValueError:
            return []
        found = _dig(doc, spec.get("items") or "") if spec.get("items") else doc
        items = found if isinstance(found, list) else []
    roles = {spec["user"]: "user", spec["assistant"]: "assistant"}
    out = []
    for item in items:
        role = roles.get(str(_dig(item, spec["role"]) or ""))
        text = _text_of(_dig(item, spec["text"])) if role else ""
        if role and text.strip():
            out.append((role, text))
    return out


def session_files(adapter: dict, home: Path, since: float = 0) -> list[Path]:
    spec = adapter.get("sessions") or {}
    if not spec:
        return []
    found = []
    for path in home.glob(spec["glob"]):
        try:
            mtime = path.stat().st_mtime
        except OSError:
            continue
        if mtime >= since - 2:
            found.append((mtime, path))
    return [p for _m, p in sorted(found, reverse=True)[:8]]


def _started(pid: int) -> float:
    try:
        return os.stat(f"/proc/{pid}").st_mtime
    except OSError:
        return 0.0


def session_id(adapter: dict, pid: int, home: Path) -> str:
    """The conversation a running learned CLI is in, for its resume flag."""
    from .workspaces import RESUME_RE

    resume = adapter.get("resume") or {}
    if resume.get("by") != "id":
        return ""
    files = session_files(adapter, home)
    opened = set()
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                opened.add(os.readlink(f"/proc/{pid}/fd/{fd}"))
            except OSError:
                continue
    except OSError:
        pass
    pick = next((f for f in files if str(f) in opened), None)
    since = _started(pid)
    if pick is None and since:
        for f in files:
            try:
                if f.stat().st_mtime >= since:
                    pick = f
                    break
            except OSError:
                continue
    if pick is None:
        return ""
    sid = pick.stem
    if resume.get("id_re"):
        match = re.search(resume["id_re"], str(pick))
        sid = (match.group(1) if match and match.groups() else match.group(0)) if match else ""
    return sid if RESUME_RE.match(sid or "") else ""


def resume_line(cli: str, sid: str) -> str:
    adapter = get(cli)
    resume = (adapter or {}).get("resume") or {}
    if not resume:
        return ""
    if resume["by"] == "last":
        return f"{cli} {resume['flag']}"
    from .workspaces import RESUME_RE

    return f"{cli} {resume['flag']} {sid}" if RESUME_RE.match(sid or "") else cli


# --- `rc adapt` ------------------------------------------------------------------


def command(args) -> int:
    if args.list or not args.cli:
        rows = load_all()
        if not rows:
            print("No learned CLIs yet. They are learned when one runs in a tab or pane, or with `rc adapt <cli>`.")
            return 0
        for name, adapter in sorted(rows.items()):
            when = time.strftime("%Y-%m-%d", time.localtime(adapter.get("learned") or 0))
            if adapter.get("is_ai_cli"):
                print(f"{name:<16} {adapter['name']:<20} {summary(adapter)}  [{adapter.get('source') or '?'}, {when}]")
            else:
                print(f"{name:<16} {'(not an AI CLI)':<20} [{adapter.get('source') or '?'}, {when}]")
        return 0
    if args.forget:
        if forget(args.cli):
            print(f"Forgot {args.cli}.")
            return 0
        print(f"rc: {args.cli} was not learned", file=sys.stderr)
        return 1
    if args.show:
        adapter = load_all().get(args.cli)
        print(json.dumps(adapter, ensure_ascii=False, indent=1) if adapter else f"{args.cli}: nothing learned")
        return 0 if adapter else 1
    adapter, message = learn(args.cli, force=args.force)
    print(message)
    return 0 if adapter else 1
