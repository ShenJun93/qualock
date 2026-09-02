from __future__ import annotations

from .models import WatchEvent, WatchEventKind

_MESSAGES = {
    WatchEventKind.CONTROL_VERIFIED: "Signed protection lock verified.",
    WatchEventKind.CHECKING: "Checking protected behavior...",
    WatchEventKind.WATCHING: "Watching for changes...",
    WatchEventKind.CHANGED: "Changes detected...",
    WatchEventKind.SETTLING: "Waiting for edits to settle...",
    WatchEventKind.STALE: (
        "Project changed while QuaLock was checking; checking again after edits settle."
    ),
    WatchEventKind.INSTABILITY_INCOMPLETE: (
        "CHECK COULD NOT FINISH\n\n"
        "Protected checks kept changing watched project files while QuaLock was checking."
    ),
}


def render_watch_event(event: WatchEvent) -> str:
    if event.kind is WatchEventKind.RESULT:
        return ""
    return _MESSAGES[event.kind] + "\n"
