"""Non-GUI core for Light Table: recursive image discovery and the thumbnail cache."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

# Tier-1 formats decode on the hot path; everything Qt/Pillow can sniff still opens.
IMAGE_EXTS = {
    ".png", ".jpg", ".jpeg", ".tif", ".tiff", ".webp", ".bmp", ".gif",
    ".ppm", ".pgm", ".pbm", ".ico", ".tga", ".exr", ".hdr", ".jxl",
    ".avif", ".heic", ".dds", ".psd",
}

DEFAULT_LIMIT = 50_000
THUMB_SIZE = 256


def cache_dir() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "2photon" / "lighttable"


def scan_images(root: Path, limit: int = DEFAULT_LIMIT) -> list[tuple[float, str]]:
    """All images under root (recursive), newest-modified first, capped at limit."""
    found: list[tuple[float, str]] = []
    stack = [str(root)]
    while stack and len(found) < limit:
        folder = stack.pop()
        try:
            with os.scandir(folder) as entries:
                for entry in entries:
                    if entry.name.startswith("."):
                        continue
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(entry.path)
                        elif os.path.splitext(entry.name)[1].lower() in IMAGE_EXTS:
                            found.append((entry.stat(follow_symlinks=False).st_mtime, entry.path))
                            if len(found) >= limit:
                                break
                    except OSError:
                        continue
        except OSError:
            continue
    found.sort(reverse=True)
    return found


def thumb_key(path: str, mtime: float, size: int) -> str:
    """Cache key: path identity plus content freshness."""
    raw = f"{path}\x00{mtime:.6f}\x00{size}".encode("utf-8", "surrogateescape")
    return hashlib.blake2b(raw, digest_size=16).hexdigest()


def thumb_path_for(path: str) -> Path | None:
    """Where this file's thumbnail lives in the cache, or None if the file is gone."""
    try:
        stat = os.stat(path)
    except OSError:
        return None
    return cache_dir() / f"{thumb_key(path, stat.st_mtime, stat.st_size)}.png"
