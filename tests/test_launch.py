"""Launch the app exactly the way a user does (§11.1 step 3).

Importing ``radarscope.main`` is not proof the documented commands work; these
tests run ``python -m radarscope`` and the installed ``radarscope`` console
script as real subprocesses under the offscreen Qt platform.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from radarscope.config import Config, load_config, save_config

REPO = Path(__file__).resolve().parents[1]


def launch_env(config_dir):
    env = dict(os.environ)
    env["QT_QPA_PLATFORM"] = "offscreen"
    env["RADARSCOPE_CONFIG_DIR"] = str(config_dir)
    return env


def seeded_config(tmp_path):
    save_config(Config(postcode="GL14 2AB", home_lat=51.82, home_lon=-2.49), tmp_path / "config.toml")
    return tmp_path


def run(cmd, tmp_path, cwd=REPO, timeout=60):
    return subprocess.run(cmd, cwd=cwd, env=launch_env(tmp_path), capture_output=True, text=True,
                          timeout=timeout)


def test_python_dash_m_radarscope_launches(tmp_path):
    cfg_dir = seeded_config(tmp_path)
    proc = run([sys.executable, "-m", "radarscope", "--quit-after", "2"], cfg_dir)
    assert proc.returncode == 0, proc.stderr
    assert "cannot be directly executed" not in proc.stderr
    # The window ran and persisted its geometry on shutdown.
    assert load_config(cfg_dir / "config.toml").window.x is not None


def test_python_dash_m_first_run_prompts_then_exits_cleanly(tmp_path):
    # No config: the first-run postcode dialog appears; --quit-after closes it.
    proc = run([sys.executable, "-m", "radarscope", "--quit-after", "1.5"], tmp_path)
    assert proc.returncode == 0, proc.stderr
    assert "No postcode entered" in proc.stderr


def test_python_dash_m_version(tmp_path):
    proc = run([sys.executable, "-m", "radarscope", "--version"], tmp_path)
    assert proc.returncode == 0 and proc.stdout.startswith("radarscope ")


@pytest.mark.skipif(shutil.which("radarscope") is None,
                    reason="console script not installed (pip install . to enable)")
def test_console_script_launches(tmp_path):
    cfg_dir = seeded_config(tmp_path)
    # Run from an unrelated directory so the installed package, not the checkout, is used.
    proc = run([shutil.which("radarscope"), "--quit-after", "2"], cfg_dir, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr
