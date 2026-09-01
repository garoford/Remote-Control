from __future__ import annotations

import subprocess
from pathlib import Path

UPDATED_EXIT = 10


def _installed_root() -> Path:
    return Path.home() / ".local" / "share" / "remote-control"


def running_from_install() -> bool:
    installed = _installed_root() / "remote_control"
    try:
        return Path(__file__).resolve().parent.samefile(installed)
    except OSError:
        return False


def update_script() -> Path | None:
    installed = _installed_root() / "update.sh"
    if installed.is_file():
        return installed
    checkout = Path(__file__).resolve().parent.parent / "update.sh"
    if checkout.is_file():
        return checkout
    return None


def check_and_apply() -> bool:
    """Run update.sh. Returns True if installed files changed."""
    script = update_script()
    if script is None:
        return False
    try:
        proc = subprocess.run(
            ["bash", str(script)],
            check=False,
            capture_output=True,
            timeout=180,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == UPDATED_EXIT
