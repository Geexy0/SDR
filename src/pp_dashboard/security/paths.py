"""Canonical path containment for captures, voice, and /dev/shm artifacts."""

from __future__ import annotations

from pathlib import Path


def resolve_safe_path(base_dir: Path, user_input: str | Path) -> Path:
    """Resolve ``user_input`` under ``base_dir``; reject ``..``, absolutes, and symlink escapes."""
    base = base_dir.expanduser().resolve()
    base.mkdir(parents=True, exist_ok=True)
    candidate = Path(user_input)
    if candidate.is_absolute():
        resolved = candidate.expanduser().resolve()
    else:
        resolved = (base / candidate).expanduser().resolve()
    try:
        resolved.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"path must be under {base}") from exc
    return resolved


def resolve_under_any(roots: list[Path], user_input: str | Path) -> Path:
    """Resolve ``user_input`` if it stays under any of ``roots`` after ``Path.resolve()``."""
    resolved = Path(user_input).expanduser().resolve()
    for root in roots:
        base = root.expanduser().resolve()
        try:
            resolved.relative_to(base)
            return resolved
        except ValueError:
            continue
    roots_s = ", ".join(str(r.resolve()) for r in roots)
    raise ValueError(f"path must be under one of: {roots_s}")
