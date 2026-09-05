import os
import sys
from pathlib import Path


def write_python_launcher(path: Path, source: str) -> Path:
    if os.name == "nt":
        # with_suffix replaces any existing suffix, so callers must pass a
        # stem-only path (no ".sh"/".py" of its own) or it will collide.
        script = path.with_suffix(".py")
        script.write_text(source, encoding="utf-8")
        launcher = path.with_suffix(".cmd")
        # %* forwards argv through cmd.exe, which reinterprets cmd
        # metacharacters (&, ^, >); fine for the plain package specs and
        # paths current fixtures pass, not a general argv passthrough.
        launcher.write_text(
            f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n',
            encoding="utf-8",
        )
        return launcher

    path.write_text("#!/usr/bin/env python3\n" + source, encoding="utf-8")
    path.chmod(0o755)
    return path


def venv_python_path(root: Path, name: str = ".venv") -> Path:
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return root / name / relative


def venv_python_relative(name: str = ".venv") -> str:
    return (Path(name) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")).as_posix()
