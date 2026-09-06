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
    help_stream: str = "stdout",
    environment_log: Path | None = None,
) -> Path:
    source = (
        "import os\n"
        "import sys\n"
        "args = sys.argv[1:]\n"
    )
    if environment_log is not None:
        source += (
            f"with open({str(environment_log)!r}, 'a', encoding='utf-8') as handle:\n"
            "    handle.write("
            "f\"{args[0]}={os.environ.get('AGY_CLI_DISABLE_AUTO_UPDATE')}\\n\")\n"
        )
    source += (
        "if args == ['--version']:\n"
        f"    print({version!r})\n"
        f"    raise SystemExit({version_exit_code})\n"
        "if args == ['--help']:\n"
        f"    print({help_text!r}, file=sys.{help_stream})\n"
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


def test_resolve_rejects_uppercase_windows_executable_suffix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    with pytest.raises(AntigravityResolveError, match="native Linux"):
        AntigravityResolver(tmp_path / "agy.EXE").resolve("1.1.27")


def test_resolve_rejects_symlink_to_windows_executable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    # A literal .exe file, not write_fake_agy(name="agy.exe"): that helper
    # delegates to write_python_launcher, which on Windows rewrites the
    # path through with_suffix(".cmd") and returns agy.cmd instead of an
    # .exe, so the symlink would no longer point at a Windows executable.
    windows_binary = tmp_path / "agy.exe"
    windows_binary.write_text("not executed; suffix check rejects first", encoding="utf-8")
    symlink = tmp_path / "agy"
    symlink.symlink_to(windows_binary)

    with pytest.raises(AntigravityResolveError, match="native Linux"):
        AntigravityResolver(symlink).resolve("1.1.27")


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


def test_resolve_accepts_help_contract_printed_to_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Real agy 1.1.27 writes "Usage of agy:" and its whole flag table to stderr.
    agy = write_fake_agy(tmp_path, version="1.1.27", help_stream="stderr")
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    binary = AntigravityResolver(agy).resolve("1.1.27")

    assert binary.version == "1.1.27"


def test_resolve_rejects_missing_help_flag_on_stderr(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    agy = write_fake_agy(
        tmp_path,
        version="1.1.27",
        help_text="--output-format --model --effort --new-project --disable-slash-commands",
        help_stream="stderr",
    )
    monkeypatch.setattr(platform, "system", lambda: "Linux")

    with pytest.raises(AntigravityResolveError, match="missing required CLI flag --sandbox"):
        AntigravityResolver(agy).resolve("1.1.27")


def test_resolve_disables_auto_update_while_probing_binary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "env.log"
    agy = write_fake_agy(tmp_path, version="1.1.27", environment_log=log)
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    monkeypatch.delenv("AGY_CLI_DISABLE_AUTO_UPDATE", raising=False)

    AntigravityResolver(agy).resolve("1.1.27")

    assert log.read_text(encoding="utf-8").splitlines() == [
        "--version=true",
        "--help=true",
    ]
