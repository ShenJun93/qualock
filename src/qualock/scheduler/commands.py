from __future__ import annotations

import os
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from qualock.release_monitor.commands import monitor_preflight
from qualock.release_monitor.state import project_key

from .backends import (
    LaunchdAgentBackend,
    SchedulerBackend,
    SchedulerOperationalError,
    SchedulerUnsupportedError,
    SystemdUserBackend,
    WindowsTaskSchedulerBackend,
)
from .models import (
    NativeScheduleState,
    ScheduleIdentity,
    SchedulerBackendKind,
    ScheduleRegistration,
    backend_label,
    native_id_for,
    operationally_equal,
    parse_daily_time,
    schedule_identity,
)
from .state import (
    FileRegistrationStore,
    RegistrationLoadKind,
    RegistrationStore,
)


class ScheduleStatus(str, Enum):
    ENABLED = "ENABLED"
    DISABLED = "DISABLED"
    NEEDS_REPAIR = "NEEDS REPAIR"


@dataclass(frozen=True)
class ScheduleOutcome:
    status: ScheduleStatus
    project_root: Path
    backend: SchedulerBackendKind
    backend_label: str
    log_path: Path
    registration: ScheduleRegistration | None = None
    detail: str | None = None


BackendFactory = Callable[[], SchedulerBackend]


def _non_strict_identity(root: Path, kind: SchedulerBackendKind) -> ScheduleIdentity:
    key = project_key(root)
    return ScheduleIdentity(key, kind, native_id_for(kind, key))


def default_backend_factories() -> Mapping[SchedulerBackendKind, BackendFactory]:
    return {
        SchedulerBackendKind.WINDOWS_TASK_SCHEDULER: WindowsTaskSchedulerBackend,
        SchedulerBackendKind.SYSTEMD_USER: SystemdUserBackend,
        SchedulerBackendKind.LAUNCHD_AGENT: LaunchdAgentBackend,
    }


def select_backend(
    *,
    platform: str = sys.platform,
    factories: Mapping[SchedulerBackendKind, BackendFactory] | None = None,
) -> SchedulerBackend:
    active = factories or default_backend_factories()
    if platform == "win32":
        kind = SchedulerBackendKind.WINDOWS_TASK_SCHEDULER
    elif platform.startswith("linux"):
        kind = SchedulerBackendKind.SYSTEMD_USER
    elif platform == "darwin":
        kind = SchedulerBackendKind.LAUNCHD_AGENT
    else:
        raise SchedulerUnsupportedError(f"unsupported scheduler platform: {platform}")
    return active[kind]()


def _build_registration(
    root: Path,
    backend: SchedulerBackend,
    hour: int,
    minute: int,
    *,
    executable: Path | None,
    home: Path | None,
    environ: Mapping[str, str] | None,
    now: Callable[[], datetime] | None,
) -> ScheduleRegistration:
    canonical_root = root.expanduser().resolve(strict=True)
    if not canonical_root.is_dir():
        raise ValueError("project root must be a directory")
    python_path = Path(sys.executable) if executable is None else executable
    if not python_path.is_absolute() or not python_path.is_file():
        raise ValueError("scheduler Python executable is unavailable")
    runner_home = (Path.home() if home is None else home).expanduser().resolve(strict=True)
    if not runner_home.is_dir():
        raise ValueError("runner working directory is unavailable")
    source_env = os.environ if environ is None else environ
    captured_path = source_env.get("PATH", os.defpath)
    enabled_at = (now() if now is not None else datetime.now(UTC)).astimezone(UTC)
    identity = schedule_identity(canonical_root, backend.kind)
    return ScheduleRegistration(
        project_key=identity.project_key,
        project_root=canonical_root,
        backend=backend.kind,
        native_id=identity.native_id,
        hour=hour,
        minute=minute,
        python_executable=python_path,
        runner_working_directory=runner_home,
        path_env=captured_path,
        enabled_at=enabled_at,
    )


def _outcome(
    status: ScheduleStatus,
    root: Path,
    backend: SchedulerBackend,
    store: RegistrationStore,
    registration: ScheduleRegistration | None,
    detail: str | None = None,
) -> ScheduleOutcome:
    identity = _non_strict_identity(root, backend.kind)
    return ScheduleOutcome(
        status=status,
        project_root=root,
        backend=backend.kind,
        backend_label=backend_label(backend.kind),
        log_path=store.log_path(identity.project_key),
        registration=registration,
        detail=detail,
    )


def enable_schedule(
    root: Path,
    at: str = "09:00",
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
    executable: Path | None = None,
    home: Path | None = None,
    environ: Mapping[str, str] | None = None,
    now: Callable[[], datetime] | None = None,
) -> ScheduleOutcome:
    hour, minute = parse_daily_time(at)
    canonical_root = root.expanduser().resolve(strict=True)
    monitor_preflight(canonical_root)
    active_backend = backend or select_backend()
    active_backend.probe()
    active_store = store or FileRegistrationStore()
    candidate = _build_registration(
        canonical_root,
        active_backend,
        hour,
        minute,
        executable=executable,
        home=home,
        environ=environ,
        now=now,
    )
    loaded = active_store.load(candidate.project_key)
    same_registration = (
        loaded.kind is RegistrationLoadKind.VALID
        and loaded.registration is not None
        and operationally_equal(loaded.registration, candidate)
    )
    if same_registration:
        assert loaded.registration is not None
        candidate = candidate.model_copy(update={"enabled_at": loaded.registration.enabled_at})
        inspection = active_backend.inspect(
            schedule_identity(canonical_root, active_backend.kind),
            loaded.registration,
        )
        if inspection.state is NativeScheduleState.MATCHING:
            return _outcome(
                ScheduleStatus.ENABLED,
                canonical_root,
                active_backend,
                active_store,
                loaded.registration,
            )

    try:
        active_store.save(candidate)
    except OSError as exc:
        raise SchedulerOperationalError(
            f"scheduler registration could not be saved: {exc}"
        ) from exc

    identity = schedule_identity(canonical_root, active_backend.kind)
    try:
        active_backend.install(candidate)
        inspection = active_backend.inspect(identity, candidate)
        if inspection.state is not NativeScheduleState.MATCHING:
            raise SchedulerOperationalError(
                f"native schedule verification was {inspection.state.value}"
            )
    except (SchedulerOperationalError, OSError) as exc:
        remove_error: Exception | None = None
        delete_error: Exception | None = None
        try:
            active_backend.remove(identity)
        except (SchedulerOperationalError, OSError) as cleanup_exc:
            remove_error = cleanup_exc
        try:
            active_store.delete(identity.project_key)
        except OSError as cleanup_exc:
            delete_error = cleanup_exc

        parts = [f"schedule enable failed: {exc}"]
        if remove_error is not None:
            parts.append(
                "native schedule may still be enabled; run `qualock schedule status` "
                "and `qualock schedule disable`"
            )
        if delete_error is not None:
            parts.append(f"registration cleanup also failed: {delete_error}")
        raise SchedulerOperationalError(
            "; ".join(parts),
            rollback_uncertain=remove_error is not None,
        ) from exc

    return _outcome(
        ScheduleStatus.ENABLED,
        canonical_root,
        active_backend,
        active_store,
        candidate,
    )


def schedule_status(
    root: Path,
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
) -> ScheduleOutcome:
    canonical_root = root.expanduser().resolve(strict=False)
    active_backend = backend or select_backend()
    active_backend.probe()
    active_store = store or FileRegistrationStore()
    identity = _non_strict_identity(canonical_root, active_backend.kind)
    loaded = active_store.load(identity.project_key)
    registration = loaded.registration if loaded.kind is RegistrationLoadKind.VALID else None
    trusted_expected = (
        registration
        if registration is not None and registration.backend is active_backend.kind
        else None
    )
    inspection = active_backend.inspect(identity, trusted_expected)

    if (
        loaded.kind is RegistrationLoadKind.MISSING
        and inspection.state is NativeScheduleState.MISSING
    ):
        return _outcome(
            ScheduleStatus.DISABLED, canonical_root, active_backend, active_store, None
        )
    if registration is None:
        return _outcome(
            ScheduleStatus.NEEDS_REPAIR,
            canonical_root,
            active_backend,
            active_store,
            None,
            loaded.detail or inspection.detail,
        )
    healthy = (
        registration.backend is active_backend.kind
        and registration.project_root == canonical_root
        and registration.project_root.is_dir()
        and registration.python_executable.is_file()
        and registration.runner_working_directory.is_dir()
    )
    status = (
        ScheduleStatus.ENABLED
        if healthy and inspection.state is NativeScheduleState.MATCHING
        else ScheduleStatus.NEEDS_REPAIR
    )
    return _outcome(
        status,
        canonical_root,
        active_backend,
        active_store,
        registration,
        inspection.detail,
    )


def disable_schedule(
    root: Path,
    *,
    backend: SchedulerBackend | None = None,
    store: RegistrationStore | None = None,
) -> ScheduleOutcome:
    canonical_root = root.expanduser().resolve(strict=False)
    active_backend = backend or select_backend()
    active_backend.probe()
    active_store = store or FileRegistrationStore()
    identity = _non_strict_identity(canonical_root, active_backend.kind)
    try:
        active_backend.remove(identity)
    except SchedulerOperationalError:
        raise
    except OSError as exc:
        raise SchedulerOperationalError(
            f"native schedule could not be removed: {exc}"
        ) from exc
    try:
        active_store.delete(identity.project_key)
    except OSError as exc:
        raise SchedulerOperationalError(
            f"native schedule was removed but registration cleanup failed: {exc}"
        ) from exc
    return _outcome(
        ScheduleStatus.DISABLED,
        canonical_root,
        active_backend,
        active_store,
        None,
    )


__all__ = [
    "BackendFactory",
    "ScheduleOutcome",
    "ScheduleStatus",
    "default_backend_factories",
    "disable_schedule",
    "enable_schedule",
    "schedule_status",
    "select_backend",
]
