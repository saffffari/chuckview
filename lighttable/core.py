"""Non-GUI core for Light Table: recursive image discovery, the thumbnail cache,
and the pure helpers behind import/paste and the file-manager verbs."""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
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


# --- Import / paste planning ----------------------------------------------------------
# The app drops external images into the current folder (copy, non-destructive) and
# leaves images already inside it alone (just view). These are the pure decisions.


def is_image_path(path: str | os.PathLike) -> bool:
    return Path(path).suffix.lower() in IMAGE_EXTS


def unique_destination(folder: Path, name: str, reserved: Iterable[Path] = ()) -> Path:
    """A path in `folder` for `name` that collides with neither disk nor `reserved`.
    Duplicates get a " (2)", " (3)" … suffix, keeping the extension."""
    folder = Path(folder)
    taken = {Path(r) for r in reserved}
    original = Path(name)
    candidate = folder / original.name
    index = 2
    while candidate.exists() or candidate in taken:
        candidate = folder / f"{original.stem} ({index}){original.suffix}"
        index += 1
    return candidate


@dataclass(frozen=True)
class DropPlan:
    """The classified outcome of dropping/pasting a set of paths onto a folder."""

    copies: tuple[tuple[Path, Path], ...]  # (source, destination) images to copy in
    existing: tuple[Path, ...]             # image files already inside the folder


def _within(path: Path, folder: Path) -> bool:
    try:
        path.relative_to(folder)
        return True
    except ValueError:
        return False


def plan_import(sources: Iterable[str | os.PathLike], folder: Path) -> DropPlan:
    """Decide, for each dropped/pasted path, whether to copy it into `folder`
    (external image) or just select it (already inside). Non-images, directories,
    missing paths, and duplicates are dropped."""
    folder = Path(folder).resolve()
    copies: list[tuple[Path, Path]] = []
    existing: list[Path] = []
    reserved: list[Path] = []
    seen: set[Path] = set()
    for source in sources:
        try:
            resolved = Path(source).resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        if not is_image_path(resolved) or not resolved.is_file():
            continue
        if _within(resolved, folder):
            existing.append(resolved)
            continue
        destination = unique_destination(folder, resolved.name, reserved)
        reserved.append(destination)
        copies.append((resolved, destination))
    return DropPlan(tuple(copies), tuple(existing))


def filter_paths(paths: Iterable[str], query: str, root: str | os.PathLike | None = None) -> list[str]:
    """Narrow a scanned folder to the images matching every word of the query,
    case-insensitively and in any order — so "red car" finds `car_red_04.png`.

    The sheet is recursive, so a subfolder is part of an image's identity: matching runs
    over the path *below the folder on screen*, which makes "apollo" find everything in
    `apollo_11_retro/` as well as `apollo.png`. Nothing above that folder counts, or
    every image would match the name of the folder you are already in.

    An empty query is not a filter: it returns everything, in order."""
    terms = query.lower().split()
    if not terms:
        return list(paths)
    prefix = f"{os.fspath(root).rstrip(os.sep)}{os.sep}" if root is not None else ""
    kept = []
    for path in paths:
        subject = path[len(prefix):] if prefix and path.startswith(prefix) else os.path.basename(path)
        subject = subject.lower()
        if all(term in subject for term in terms):
            kept.append(path)
    return kept


def pasted_image_name(when: datetime) -> str:
    """Filename for raw image data (clipboard/drag) that has no source file of its own."""
    return when.strftime("Pasted %Y-%m-%d %H%M%S.png")


# --- File-manager verbs (reveal / trash) ----------------------------------------------
# Return argv lists so the app can just Popen them; None means "no native command,
# fall back". Kept per-OS and pure so the mapping is unit-tested without a desktop.


def reveal_command(path: str | os.PathLike, platform: str | None = None) -> list[str] | None:
    """Argv to reveal `path` in the OS file manager with the file itself selected."""
    platform = platform if platform is not None else sys.platform
    target = Path(path)
    if platform == "darwin":
        return ["open", "-R", str(target)]
    if platform.startswith("win"):
        return ["explorer", f"/select,{target}"]
    # Linux/*nix: the freedesktop FileManager1 D-Bus call highlights the file.
    try:
        uri = target.resolve().as_uri()
    except ValueError:
        return None
    return [
        "dbus-send", "--session", "--print-reply",
        "--dest=org.freedesktop.FileManager1",
        "--type=method_call", "/org/freedesktop/FileManager1",
        "org.freedesktop.FileManager1.ShowItems",
        f"array:string:{uri}", "string:",
    ]


def open_path_command(path: str | os.PathLike, platform: str | None = None) -> list[str]:
    """Argv to open a file or folder with the OS default handler (reveal fallback)."""
    platform = platform if platform is not None else sys.platform
    target = str(path)
    if platform == "darwin":
        return ["open", target]
    if platform.startswith("win"):
        return ["explorer", target]
    return ["xdg-open", target]


def trash_command(path: str | os.PathLike, platform: str | None = None) -> list[str] | None:
    """Argv to move `path` to the trash (recoverable), or None where unsupported."""
    platform = platform if platform is not None else sys.platform
    target = str(path)
    if platform == "darwin":
        return ["osascript", "-e", f'tell application "Finder" to delete POSIX file "{target}"']
    if platform.startswith("win"):
        return None
    return ["gio", "trash", "--", target]


# --- Places: pinned favorites plus whatever is mounted right now -----------------------
# Favorites persist to one flat, human-readable file — one absolute path per line,
# `#` comments allowed (Rule 2: settings are a text file, never a database).
# Drives are never stored: they are read off the mount table every time the list is built,
# so plugging a disk in makes it appear and unplugging it makes it leave.


def config_dir() -> Path:
    """Suite config root. TWOPHOTON_CONFIG_HOME overrides it for tests."""
    base = os.environ.get("TWOPHOTON_CONFIG_HOME") or str(Path.home() / ".config" / "2photon")
    return Path(base)


def favorites_file() -> Path:
    return config_dir() / "lighttable-favorites.txt"


def default_favorites() -> list[Path]:
    """What a fresh install pins: home and the usual picture wells that exist."""
    home = Path.home()
    candidates = [home, home / "Pictures", home / "Desktop", home / "Downloads"]
    return [path for path in candidates if path.is_dir()]


def _dedupe(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    ordered: list[Path] = []
    for path in paths:
        key = str(path)
        if key not in seen:
            seen.add(key)
            ordered.append(path)
    return ordered


def load_favorites(path: Path | None = None) -> list[Path]:
    """Read the favorites file. Missing file means a fresh install → the defaults.
    Entries that do not exist right now are kept (an unplugged drive is still pinned)."""
    source = Path(path) if path is not None else favorites_file()
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return default_favorites()
    folders = [
        Path(stripped).expanduser()
        for line in lines
        if (stripped := line.strip()) and not stripped.startswith("#")
    ]
    return _dedupe(folders)


def save_favorites(folders: Iterable[str | os.PathLike], path: Path | None = None) -> Path:
    """Write the favorites file, one absolute path per line, with a header comment."""
    target = Path(path) if path is not None else favorites_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    ordered = _dedupe(Path(folder).expanduser() for folder in folders)
    body = "\n".join(str(folder) for folder in ordered)
    target.write_text(
        "# Light Table favorites — one folder per line. Edit freely.\n" + body + "\n",
        encoding="utf-8",
    )
    return target


def add_favorite(folder: str | os.PathLike, path: Path | None = None) -> list[Path]:
    """Pin a folder (no-op if already pinned). Returns the new favorites list."""
    folders = load_favorites(path)
    wanted = Path(folder).expanduser()
    if wanted not in folders:
        folders.append(wanted)
        save_favorites(folders, path)
    return folders


def remove_favorite(folder: str | os.PathLike, path: Path | None = None) -> list[Path]:
    """Unpin a folder (no-op if it was not pinned). Returns the new favorites list."""
    folders = load_favorites(path)
    wanted = Path(folder).expanduser()
    remaining = [f for f in folders if f != wanted]
    if len(remaining) != len(folders):
        save_favorites(remaining, path)
    return remaining


def _drive_search_roots(platform: str, user: str) -> list[Path]:
    if platform == "darwin":
        return [Path("/Volumes")]
    if platform.startswith("win"):
        return []
    return [Path("/run/media") / user, Path("/media") / user, Path("/media"), Path("/mnt")]


def mounted_drives(
    roots: Iterable[str | os.PathLike] | None = None,
    platform: str | None = None,
    user: str | None = None,
) -> list[tuple[str, Path]]:
    """Every mounted volume right now, as (label, path) — citadel, board_rack, sticks.
    Directories that only *contain* mounts (an empty /media/alex) contribute nothing."""
    platform = platform if platform is not None else sys.platform
    user = user if user is not None else (os.environ.get("USER") or Path.home().name)
    if platform.startswith("win") and roots is None:
        found = [
            (f"{letter}:", Path(f"{letter}:\\"))
            for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ"
            if os.path.isdir(f"{letter}:\\")
        ]
        return found
    search = (
        [Path(root) for root in roots]
        if roots is not None
        else _drive_search_roots(platform, user)
    )
    drives: list[tuple[str, Path]] = []
    seen: set[object] = set()
    for root in search:
        try:
            entries = sorted(os.scandir(root), key=lambda entry: entry.name.lower())
        except OSError:
            continue
        for entry in entries:
            if entry.name.startswith("."):
                continue
            try:
                # A drive is something actually mounted. Distributions leave empty
                # placeholder directories all over /mnt; those are not disks.
                if not entry.is_dir() or not os.path.ismount(entry.path):
                    continue
                # One disk, one row: a drive mounted at both /run/media and /mnt is the
                # same device, so identity is the device — not the path it turned up at.
                key: object = os.stat(entry.path).st_dev
            except OSError:
                continue
            if key in seen or os.path.realpath(entry.path) == str(Path.home()):
                continue
            seen.add(key)
            drives.append((entry.name, Path(entry.path)))
    return drives


def tree_anchor(folder: str | os.PathLike, mounts: Iterable[str | os.PathLike] | None = None) -> Path:
    """The root the folder tree should show `folder` under: its own drive if it lives on
    one, else home, else the filesystem root. Keeps the tree tight instead of rendering
    the whole disk to reach one directory."""
    target = Path(folder)
    candidates = [Path(mount) for mount in (mounts if mounts is not None
                                            else [path for _label, path in mounted_drives()])]
    candidates.append(Path.home())
    contained = [c for c in candidates if c == target or _within(target, c)]
    if contained:
        return max(contained, key=lambda c: len(str(c)))
    return Path(target.anchor or "/")
