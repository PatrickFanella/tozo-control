#!/usr/bin/python3
"""Install the local plugin while retaining the previous plugin/configuration."""
import datetime
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

source = Path(__file__).resolve().parent / "omarchy-ht3"
subprocess.run(["omarchy", "plugin", "validate", str(source)], check=True)
config = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "omarchy"
destination = config / "plugins" / "patrickfanella.ht3"
stamp = datetime.datetime.now().strftime("%Y%m%dT%H%M%S%f")
backup = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "omarchy-ht3/backups" / stamp
backup.mkdir(parents=True)
if (config / "shell.json").exists():
    shutil.copy2(config / "shell.json", backup / "shell.json")
destination.parent.mkdir(parents=True, exist_ok=True)
# Build outside the watched plugins tree, then expose complete files together.
with tempfile.TemporaryDirectory(prefix="ht3-install-", dir=config) as temporary:
    staged = Path(temporary) / "plugin"
    shutil.copytree(source, staged, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    subprocess.run(["omarchy", "plugin", "validate", str(staged)], check=True)
    if destination.exists():
        shutil.move(str(destination), str(backup / "plugin"))
    try:
        staged.rename(destination)
    except Exception:
        if (backup / "plugin").exists():
            shutil.move(str(backup / "plugin"), str(destination))
        raise
subprocess.run(["omarchy", "shell", "shell", "rescanPlugins"], check=True)
subprocess.run(["omarchy", "plugin", "enable", "patrickfanella.ht3", "right"], check=True)
# Qt caches component files and directory entries across plugin rescans.
# Restart through Omarchy so upgrades reliably load the just-installed QML.
subprocess.run(["omarchy", "restart", "shell"], check=True)
print(f"Installed: {destination}\nBackup: {backup}")
