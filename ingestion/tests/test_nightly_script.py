"""scripts/nightly.sh, run for real against a stand-in Python and osascript.

The retry logic lives in the shell script, and launchd fires it unattended, so a
mistake here shows up as a day silently skipped or a notification every evening.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from datetime import date
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def executable(path: Path, body: str) -> None:
    path.write_text("#!/bin/bash\n" + body)
    path.chmod(path.stat().st_mode | stat.S_IEXEC)


@pytest.fixture
def sandbox(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "ingestion").mkdir()
    (tmp_path / ".venv/bin").mkdir(parents=True)
    (tmp_path / "bin").mkdir()
    for name in ("nightly.sh", "local-stack.sh"):
        shutil.copy(REPO / "scripts" / name, tmp_path / "scripts" / name)
    executable(tmp_path / "bin/osascript", f'echo "$*" >> "{tmp_path}/notified"\n')
    return tmp_path


def run(box: Path, *, status: int, hour: int, line: str = "") -> int:
    executable(box / ".venv/bin/python",
               f'echo run >> "{box}/runs"\necho "{line}"\nexit {status}\n')
    env = {**os.environ, "NIGHTLY_HOUR": f"{hour:02d}",
           "PATH": f"{box}/bin:{os.environ['PATH']}"}
    # local-stack.sh puts Homebrew first on PATH; osascript is not there, so
    # the stand-in in bin/ still wins over /usr/bin.
    return subprocess.run(["bash", str(box / "scripts/nightly.sh")], env=env).returncode


def runs(box: Path) -> int:
    path = box / "runs"
    return len(path.read_text().splitlines()) if path.exists() else 0


def notified(box: Path) -> str:
    path = box / "notified"
    return path.read_text() if path.exists() else ""


def test_a_finished_evening_stops_the_later_slots(sandbox):
    assert run(sandbox, status=0, hour=19) == 0
    assert run(sandbox, status=0, hour=20) == 0
    assert run(sandbox, status=0, hour=22) == 0
    assert runs(sandbox) == 1


def test_an_incomplete_run_tries_again_and_stays_quiet(sandbox):
    assert run(sandbox, status=75, hour=19, line="[x] INCOMPLETE could not fetch") == 75
    assert notified(sandbox) == ""
    assert run(sandbox, status=0, hour=20) == 0
    assert runs(sandbox) == 2
    assert notified(sandbox) == ""


def test_the_last_slot_failing_does_notify(sandbox):
    run(sandbox, status=75, hour=23, line="[x] INCOMPLETE could not fetch 2026-11-02")
    assert "INCOMPLETE could not fetch 2026-11-02" in notified(sandbox)


def test_a_real_failure_notifies_at_once_and_is_retried(sandbox):
    run(sandbox, status=1, hour=19, line="[x] FAILED universe")
    assert "FAILED universe" in notified(sandbox)
    run(sandbox, status=0, hour=20)
    assert runs(sandbox) == 2


def test_a_morning_catch_up_does_not_cancel_tonights_run(sandbox):
    assert run(sandbox, status=0, hour=8) == 0
    assert run(sandbox, status=0, hour=19) == 0
    assert runs(sandbox) == 2


def test_yesterdays_marker_does_not_stop_today(sandbox):
    (sandbox / "data/logs").mkdir(parents=True)
    (sandbox / "data/logs/.nightly-done").write_text("2020-01-01\n")
    run(sandbox, status=0, hour=19)
    assert runs(sandbox) == 1
    assert (sandbox / "data/logs/.nightly-done").read_text().strip() == date.today().isoformat()
