"""Run the existing Open recorder's offline ownership/cleanup checks."""

from pathlib import Path
import subprocess
import sys


def test_open_recorder_offline_self_test(tmp_path):
    script = Path(__file__).resolve().parents[1] / "run_notification_actions_smoke.py"
    subprocess.run(
        [sys.executable, str(script), "--self-test", "--artifacts", str(tmp_path)],
        check=True, capture_output=True, text=True, timeout=10,
    )
