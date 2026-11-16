"""Check that your course workspace is set up correctly.

Run it from the root of your workspace (the `labs` folder):

    uv run --env-file .env python tools/doctor.py

It prints PASS or FIX for each check, writes setup/report.json, and exits
with status 1 if anything needs fixing. The report never contains your key.
"""
import importlib.metadata
import json
import os
import platform
import re
import subprocess
import sys
import time
import tomllib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

DOCTOR_VERSION = "1.0.0"
ROOT = Path(__file__).resolve().parent.parent
REPORT = ROOT / "setup" / "report.json"
MODEL = "claude-haiku-5-5"
PACKAGES = ["anthropic", "tiktoken", "requests"]

checks = []
model_call = None


def record(check_id, ok, detail, fix="", lesson=None):
    checks.append({
        "id": check_id,
        "status": "pass" if ok else "fail",
        "detail": detail,
        "fix": "" if ok else fix,
        "lesson": None if ok else lesson,
    })


def git(*args):
    """Run a git command in the workspace. Returns (exit code, output)."""
    try:
        result = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, timeout=20)
    except FileNotFoundError:
        return None, ""
    return result.returncode, result.stdout.strip()


def pinned_python():
    path = ROOT / ".python-version"
    if not path.exists():
        return None
    return path.read_text().strip()


def check_python():
    pin = pinned_python()
    running = f"{sys.version_info.major}.{sys.version_info.minor}"
    if pin is None:
        record("python_version", False, f"running Python {platform.python_version()}",
               "The .python-version file is missing. Run `git checkout -- .python-version` to restore it.", 6)
    elif running == pin:
        record("python_version", True, f"Python {platform.python_version()} (pinned {pin})")
    else:
        record("python_version", False, f"running Python {platform.python_version()}, but the workspace pins {pin}",
               "Run the doctor with `uv run`, from inside the labs folder, so uv picks the pinned Python.", 6)


def check_venv():
    expected = (ROOT / ".venv").resolve()
    actual = Path(sys.prefix).resolve()
    if actual == expected:
        record("venv", True, "running inside the workspace's .venv")
    else:
        record("venv", False, f"running from {actual}, not the workspace's .venv",
               "Run `uv sync`, then run the doctor with `uv run` from inside the labs folder.", 6)


def check_packages():
    found, missing = [], []
    for name in PACKAGES:
        try:
            __import__(name)
            found.append(f"{name} {importlib.metadata.version(name)}")
        except ImportError:
            missing.append(name)
    if missing:
        record("packages", False, f"missing: {', '.join(missing)}",
               "Run `uv sync` inside the labs folder. Don't install packages with pip.", 6)
    else:
        record("packages", True, ", ".join(found))
    return not missing


def check_tokenizer():
    try:
        import tiktoken
        tokens = tiktoken.get_encoding("o200k_base").encode("Hello, world!")
        record("tokenizer", True, f"o200k_base loaded ({len(tokens)} tokens in 'Hello, world!')")
    except Exception as err:
        record("tokenizer", False, f"couldn't load o200k_base: {type(err).__name__}: {err}",
               "tiktoken downloads the tokenizer once. Check your internet connection and run the doctor again.", 6)


def check_git():
    code, version = git("--version")
    if code is None:
        record("git_installed", False, "git not found", "Install Git, then open a new terminal.", 4)
        return False
    record("git_installed", True, version)

    _, name = git("config", "user.name")
    _, email = git("config", "user.email")
    if name and email:
        record("git_identity", True, f"{name} <{email}>")
    else:
        record("git_identity", False, "user.name or user.email isn't set",
               'Run `git config --global user.name "Your Name"` and `git config --global user.email "you@example.com"`.', 4)

    code, url = git("remote", "get-url", "origin")
    if code == 0 and "github.com" in url:
        record("git_remote", True, re.sub(r"//[^@/]+@", "//", url))
    else:
        record("git_remote", False, "this folder has no GitHub remote called origin",
               "Make sure you're in the labs folder you cloned from GitHub.", 6)
    return True


def check_env_file(have_git):
    env = ROOT / ".env"
    if not env.exists():
        record("env_file", False, ".env not found in the labs folder",
               "Run `cp .env.example .env` and fill in your key and base URL.", 7)
        return
    if not have_git:
        record("env_file", False, "can't check .env without Git", "Install Git first.", 4)
        return
    ignored, _ = git("check-ignore", "-q", ".env")
    tracked, _ = git("ls-files", "--error-unmatch", ".env")
    if tracked == 0:
        record("env_file", False, ".env is tracked by Git",
               "Run `git rm --cached .env`, commit, and revoke your key on the platform: it may have been pushed.", 7)
    elif ignored != 0:
        record("env_file", False, ".env exists but Git doesn't ignore it",
               "Restore the workspace's .gitignore with `git checkout -- .gitignore`.", 7)
    else:
        record("env_file", True, ".env exists, is ignored and isn't tracked")


def expected_gateway_host():
    try:
        config = tomllib.loads((ROOT / "pyproject.toml").read_text())
        host = config.get("tool", {}).get("course", {}).get("gateway_host", "")
    except (OSError, tomllib.TOMLDecodeError):
        return None
    return host if host and not host.startswith("<") else None


def check_api_settings():
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    base_url = os.environ.get("ANTHROPIC_BASE_URL", "")
    problems = []
    if not key:
        problems.append("ANTHROPIC_API_KEY isn't set")
    if not base_url:
        problems.append("ANTHROPIC_BASE_URL isn't set")
    else:
        parsed = urlparse(base_url)
        host = expected_gateway_host()
        if parsed.scheme != "https":
            problems.append("ANTHROPIC_BASE_URL must start with https://")
        elif host and parsed.hostname != host:
            problems.append(f"ANTHROPIC_BASE_URL points to {parsed.hostname}, not the course gateway {host}")
    if problems:
        record("api_settings", False, "; ".join(problems),
               "Run the doctor with `--env-file .env`, and check both values in .env against the platform. "
               "A value already set in your terminal wins over .env.", 7)
        return False
    masked = key[:10] + "..." + key[-4:] if len(key) > 16 else "(set)"
    record("api_settings", True, f"key {masked}, base URL {base_url}")
    return True


def check_model_call():
    global model_call
    try:
        import anthropic
    except ImportError:
        record("model_call", False, "anthropic isn't installed", "Run `uv sync`.", 6)
        return
    client = anthropic.Anthropic(max_retries=1, timeout=30)
    start = time.perf_counter()
    try:
        message = client.messages.create(
            model=MODEL,
            max_tokens=5,
            messages=[{"role": "user", "content": "Reply with the single word: ready"}],
        )
    except anthropic.AuthenticationError:
        record("model_call", False, "the gateway rejected your key (401)",
               "Copy your key from the platform into .env again. If it still fails, create a new key.", 7)
        return
    except anthropic.PermissionDeniedError:
        record("model_call", False, "your key isn't allowed to make this request (403)",
               "Check your key's status and spending on the platform.", 7)
        return
    except anthropic.APIConnectionError as err:
        record("model_call", False, f"couldn't reach {client.base_url}: {err}",
               "Check ANTHROPIC_BASE_URL in .env and your internet connection.", 7)
        return
    except anthropic.APIStatusError as err:
        record("model_call", False, f"the gateway returned {err.status_code}: {err.message}",
               "Wait a minute and run the doctor again. If it keeps failing, ask for help with your report.", 1)
        return
    latency_ms = round((time.perf_counter() - start) * 1000)
    text = "".join(block.text for block in message.content if block.type == "text").strip()
    model_call = {
        "request_id": message.id,
        "model": message.model,
        "stop_reason": message.stop_reason,
        "input_tokens": message.usage.input_tokens,
        "output_tokens": message.usage.output_tokens,
        "latency_ms": latency_ms,
        "reply": text[:40],
    }
    record("model_call", True,
           f"{message.model} replied {text!r} in {latency_ms} ms "
           f"({message.usage.input_tokens} tokens in, {message.usage.output_tokens} out, request {message.id})")


def environment():
    if os.environ.get("CODESPACES") == "true":
        return "codespace"
    if sys.platform == "linux" and "microsoft" in platform.release().lower():
        return "wsl"
    return "local"


def write_report():
    report = {
        "schema": 1,
        "doctor_version": DOCTOR_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "environment": environment(),
        "system": {
            "os": platform.system(),
            "os_release": platform.release(),
            "machine": platform.machine(),
            "python": platform.python_version(),
        },
        "checks": checks,
        "model_call": model_call,
        "all_passed": all(c["status"] == "pass" for c in checks),
    }
    REPORT.parent.mkdir(exist_ok=True)
    REPORT.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main():
    os.chdir(ROOT)
    check_python()
    check_venv()
    if check_packages():
        check_tokenizer()
    have_git = check_git()
    check_env_file(have_git)
    if check_api_settings():
        check_model_call()
    else:
        record("model_call", False, "skipped: fix your API settings first", "Fix api_settings above.", 7)

    report = write_report()
    print()
    for c in checks:
        label = "PASS" if c["status"] == "pass" else "FIX "
        print(f"{label}  {c['id']:<15} {c['detail']}")
        if c["status"] != "pass":
            where = f" (see lesson {c['lesson']})" if c["lesson"] else ""
            print(f"      {'':<15} -> {c['fix']}{where}")
    print()
    failed = sum(c["status"] != "pass" for c in checks)
    if report["all_passed"]:
        print(f"All {len(checks)} checks passed. Report written to setup/report.json.")
        return 0
    print(f"{failed} of {len(checks)} checks need fixing. Fix the first one, then run the doctor again.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
