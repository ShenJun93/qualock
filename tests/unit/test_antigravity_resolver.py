import platform
import shutil
from pathlib import Path

import pytest

from qualock.agents.antigravity_resolver import AntigravityResolveError, AntigravityResolver

from ._platform_helpers import write_python_launcher

DEFAULT_HELP_TEXT = "--sandbox --output-format --model --effort --new-project --disable-slash-commands"


def write_fake_agy(
    directory: Path,
    *,
    version: str = "1.1.27",
    help_text: str = DEFAULT_HELP_TEXT,
    version_exit_code: int = 0,
    name: str = "agy",
) -> Path:
    source = (
        "import sys\n"
        "args = sys.argv[1:]\n"
        "if args == ['--version']:\n"
        f"    print({version!r})\n"
        f"    raise SystemExit({version_exit_code})\n"
        "if args == ['--help']:\n"
        f"    print({help_text!r})\n"
        "    raise SystemExit(0)\n"
        "raise SystemExit(2)\n"
    )
    return write_python_launcher(directory / name, source)


def test_resolve_pins_exact_native_linux_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agy = write_fake_agy(
        tmp_path,
        version="1.1.27",
        help_text="--sandbox --output-format --model --effort --new-project --disable-slash-commands",
    )
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    binary = AntigravityResolver(agy).resolve("1.1.27")

    assert binary.name == "antigravity"
    assert binary.version == "1.1.27"
    assert binary.path == agy.resolve()
    assert len(binary.sha256) == 64
    assert binary.support_binaries == ()


def test_resolve_rejects_version_mismatch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agy = write_fake_agy(tmp_path, version="1.1.27")
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    with pytest.raises(AntigravityResolveError, match="requested 1.1.26"):
        AntigravityResolver(agy).resolve("1.1.26")


def test_resolve_rejects_windows_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    with pytest.raises(AntigravityResolveError, match="native Linux"):
        AntigravityResolver(tmp_path / "agy.exe").resolve("1.1.27")


def test_resolve_rejects_non_linux_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agy = write_fake_agy(tmp_path, version="1.1.27")
    monkeypatch.setattr(platform, "system", lambda: "Darwin")

    with pytest.raises(AntigravityResolveError, match="native Linux"):
        AntigravityResolver(agy).resolve("1.1.27")


def test_resolve_rejects_missing_executable(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    missing = tmp_path / "agy"

    with pytest.raises(AntigravityResolveError, match="not found"):
        AntigravityResolver(missing).resolve("1.1.27")


def test_resolve_rejects_when_no_binary_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda name: None)

    with pytest.raises(AntigravityResolveError, match="no Antigravity binary found"):
        AntigravityResolver().resolve("1.1.27")


def test_resolve_uses_path_lookup_when_binary_path_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agy = write_fake_agy(tmp_path, version="1.1.27")
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.setattr(shutil, "which", lambda name: str(agy) if name == "agy" else None)

    binary = AntigravityResolver().resolve("1.1.27")

    assert binary.path == agy.resolve()


def test_resolve_rejects_nonzero_version_exit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    agy = write_fake_agy(tmp_path, version="1.1.27", version_exit_code=1)
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    with pytest.raises(AntigravityResolveError, match="failed to inspect Antigravity version"):
        AntigravityResolver(agy).resolve("1.1.27")


def test_resolve_rejects_missing_required_help_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agy = write_fake_agy(
        tmp_path,
        version="1.1.27",
        help_text="--output-format --model --effort --new-project --disable-slash-commands",
    )
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    with pytest.raises(AntigravityResolveError, match="missing required CLI flag --sandbox"):
        AntigravityResolver(agy).resolve("1.1.27")
