"""Interpret native Hub2 playlist timing for display."""

from typing import Any


def timer_has_no_content(state: dict[str, Any]) -> bool:
    """A running timer can report a bare color throughout an entire playlist."""
    return bool(
        state.get("onState")
        and (state.get("timerId") or state.get("timer"))
        and not any(
            state.get(key)
            for key in ("pattern", "architectural", "playlist", "colorB", "impulse")
        )
    )


def playlist_step(
    playlist: dict[str, Any], now: float
) -> tuple[str | None, float | None]:
    """Return the current ordered item and seconds until the next transition."""
    start = playlist.get("startedAt")
    entries = playlist.get("playlistData")
    if (
        playlist.get("sequence") != "ordered"
        or type(start) is not int
        or start <= 0
        or start > now
        or not isinstance(entries, list)
        or not entries
    ):
        return None, None

    steps = {}
    for entry in entries:
        if not isinstance(entry, dict):
            return None, None
        index = entry.get("index")
        duration = entry.get("lengthInSeconds")
        if type(index) is not int or not 0 <= index < len(entries) or index in steps:
            return None, None
        if type(duration) is not int or duration <= 0:
            return None, None
        pattern = entry.get("pattern")
        name = pattern.get("name") if isinstance(pattern, dict) else None
        steps[index] = (name if isinstance(name, str) and name else None, duration)

    elapsed = (now - start) % sum(duration for _, duration in steps.values())
    for index in range(len(steps)):
        name, duration = steps[index]
        if elapsed < duration:
            return name, duration - elapsed
        elapsed -= duration
    return None, None
