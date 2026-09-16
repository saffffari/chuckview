# Lt — Light Table

The 2D image viewer. The picture is the window, edge to edge; everything else — **Locations**
(places + folder tree), **Gallery** (the contact sheet) and **Facts** — floats over it on panes of
glass. Light Table opens in **Focus**, one full-frame picture with nothing on it but a small
floating rail of pull-tabs; pull one and that panel comes back where you left it. The contact
sheet shows every image in the selected folder **and all of its subfolders**, newest modified
first. Tabs (Ctrl+T or the `+` button) hold independent panel/folder states, can be reordered, and
can be renamed by double-clicking their label; the tab strip only appears once there is more than
one tab.

## The panels — modular, resizable, rearrangeable

Nothing is nailed down, and nothing permanently costs the picture any room.

- **Move** a panel by dragging its header. Drop it anywhere over the picture.
- **Dock** it by shoving it against a side of the window: it takes that whole edge. A tall panel
  docks to a side, a wide one to the top or bottom, so a column carried to the right edge does
  not end up glued to the top.
- **Share an edge.** Two panels docked to the same side become a column of two, splitting it in
  the order they were dropped. That is what makes the layout rearrangeable rather than just
  movable.
- **Resize** from any edge or corner, docked or floating. Docked panels keep their edge and only
  change thickness.
- **Roll up** a panel by double-clicking its header; **close** it with the × (or its pull-tab,
  or its toolbar toggle). Closing the last one is the same thing as Focus.
- The arrangement persists to `~/.config/2photon/lighttable-panels.txt` — one line per panel,
  `name x y width height open snap collapsed`. Edit it by hand; there is no settings dialog.
  **View → Reset Panel Layout** puts everything back.

## The material — glass, from your own desktop

The panels are real glass, not a dark box parked over a photograph: each one blurs what it is
covering, bends it at the edge, and catches a lit rim so you can tell where the pane stops and the
picture starts. Depth is the point — shadow under the pane, the pane, then the controls raised
again on top of it.

The optics are not invented. KWin's glass effect (`kwin-effects-glass`, the KDE Store
"Better Blur / liquid" fork) already holds your taste in it, so Light Table reads
`[Effect-blurplus]` out of `~/.config/kwinrc` and uses the same numbers: corner radius, refraction
edge size and strength, RGB fringing, rim width and specular, noise, blur. Turn the refraction up
on the desktop and it goes up in here. Off KDE, or with the effect absent, sane defaults stand in
and nothing else changes.

It stays cheap by only doing the expensive part once: the viewport is captured small and blurred
once per change (not once per panel), the refraction is computed per pixel only along the edge
band — about a tenth of a panel — and the maps behind it are cached per size, so a panel dragged
across a photograph recomputes nothing. A repaint of a large panel is well under a millisecond.

The window itself is still one translucent slab of the house grey, so a compositor that blurs
behind a translucent window frosts the chrome for free. The picture is the only fully opaque thing
on screen, because you have to be able to trust what an image looks like.

## Marks — the quick collection and the flags

Two sets of pictures that live outside any folder: the quick collection is the shelf you sweep
things onto while you look, flags are the keepers you mark on the way past.

- **A** puts the selection in the **Quick Collection**, or takes it out if it is already in.
- **F** **flags** the selection, or unflags it.
- One key, both directions. A mixed selection goes *in* — with some frames marked and some not,
  what you meant by pressing the key was "all of these", not "undo half of them".
- Marks show as a badge on the thumbnail (a ring for the quick collection, a pennant for a flag)
  and on the Facts panel. In Focus, where there is no sheet to look at, the keypress leaves a
  brief receipt over the picture.
- Both sets have a row in the sidebar, under **COLLECTIONS**, with what is in them. Click one and
  it opens in the contact sheet like a folder — in the order you marked them, which is the only
  order in the app that is yours rather than the disk's. The folder underneath does not change:
  it is still where a paste or a drop lands. Right-click a set to empty it.
- Each set is one flat text file — `~/.config/2photon/lighttable-quick.txt` and
  `lighttable-flags.txt` — one absolute path per line, `#` comments allowed. Marking an image
  never moves, copies, or writes to it. A file on an unplugged drive stays marked and simply does
  not show until it is back.

## Places — favorites and drives

The top half of the Locations panel is the two sets of marks, your places and the drives; the
bottom half is the live folder tree. Drag the handle between them to trade one for the other.

- **Favorites** are yours: right-click any folder — in the tree or in places — and *Add to
  Favorites*. Right-click a pinned one to remove it. Pinning in one tab shows in all of them.
- **Drives** are read off the mount table every time the list is built, so citadel, board_rack,
  and a USB stick appear when they are mounted and leave when they are not. Nothing about them is
  stored, one disk is one row however many places it is mounted, and an empty `/mnt` placeholder
  directory is not a drive.
- Picking a drive re-roots the tree onto that drive, so anything mounted is browsable without
  ever rendering the whole disk to get there.
- Favorites persist to `~/.config/2photon/lighttable-favorites.txt` — one absolute path per line,
  `#` comments allowed. Edit it by hand; there is no settings dialog. An unplugged drive stays
  pinned in the file and simply does not show until it is back.

## Controls

- **LMB drag** pan · **Ctrl+LMB drag** carries the picture out to another app · **RMB drag** zoom
  (on z-stacks, horizontal RMB drag scrubs slices)
- **Scroll** zoom at cursor on flat images; steps slices on z-stacks
- **Horizontal wheel** scrolls the picture sideways — it does not change image
- **← → / Space / Shift+Space** previous–next image
- **MX Master**: forward button pans; back button holds a compare with the last-viewed image
- **RMB drag on the contact sheet** resizes thumbnails; a plain **RMB click** (no drag) opens the
  context menu
- **Shift-click** selects consecutive images, **Ctrl-click** toggles one; drag selections onto a
  folder in the tree to move the files; File → New Folder… (Ctrl+Shift+N)
- **Search** sits at the top left (`Ctrl+F`). Type to narrow the sheet to matching images —
  every word has to match, in any order, anywhere in the path below the folder on screen, so
  `retro cockpit` finds the twelve pictures in `retro cockpits/`. It filters what is already
  scanned, so it never touches the disk; searching in Focus opens the Gallery, because results
  you cannot see are not results. `Esc` clears it and hands the keyboard back to the picture.
  Each tab keeps its own search. While you are typing in it the bare-letter keys step aside, so
  searching for `a car` filters instead of flagging half the shoot.
- **Command bar** floats on the picture with Quick, Flag, Copy, Paste, Rename, Copy Path, Reveal,
  the panel toggles and Focus, without hiding the matching keyboard shortcuts.
- **A** quick collection · **F** flag — both toggle, both work on the selection
- **W** toggles the Facts panel (folder facts over image facts)
- **0** fit · **9** fill · **1** 1:1 · **Ctrl+Shift+F** focus mode (closes every panel) ·
  **Alt+F** fullscreen · **Esc** backs out. Zoom moved to the number row because **F** is worth
  more as the flag key, and 9-0-1 in a row is easier to find in the dark than a letter.
- `lighttable image.png` or `lighttable folder/` opens directly (files are files)

## Files are files — drag, drop, copy, paste

Light Table treats the folder on screen as the real thing on disk, so it moves images the way the
rest of your desktop does (Rule 8 — no project containers, no import step):

- **Drag out.** Drag one or more images from the contact sheet into any other app, or **Ctrl+drag
  the picture straight out of the viewport** — including in Focus, where the viewport is the whole
  window. (Ctrl, because a bare left drag is the pan.) The drag carries both the file URLs (for
  file managers, browsers, chat) and, for a single image, the decoded bitmap (for image editors).
- **Drag in.** Drop images onto the contact sheet or the viewport to import them into the current
  folder (a copy — the source is untouched). Dropping onto a folder in the tree still **moves** them.
- **Copy / paste.** `Ctrl+C` copies the selection (URLs + bitmap); `Ctrl+V` pastes — clipboard
  files import into the folder, raw bitmap data lands as a timestamped `Pasted ….png`. `Ctrl+Shift+C`
  copies the file paths as text.
- **Right-click menu.** On an image: Open, Copy, Copy Path, Paste, **Reveal in File Manager**,
  Rename (`F2`), and **Move to Trash** (`Del`, recoverable), plus New Folder and Refresh. Reveal
  opens your file manager with the image selected (freedesktop `FileManager1` on Linux, Finder on
  macOS, Explorer on Windows). On a folder: Open, **Add to / Remove from Favorites**, Reveal, and
  New Folder.

## Performance

Thumbnails decode on a global thread pool across all cores into a persistent disk cache at
`~/.cache/2photon/lighttable` keyed on path + mtime + size — a folder is slow once, instant
forever. Z-stacks (multi-frame TIFF etc.) decode fully into RAM on load so slice scrubbing is
instant. Format support is Qt's image plugins today; the OpenImageIO tier (EXR, DPX, RAW, PSD…)
is the planned lazy-loaded extension.

The glass costs a capture of the viewport at a quarter scale per change — coalesced, so panning a
picture asks for it once a frame at most, not once a panel — plus about half a millisecond of
NumPy per panel repaint for the refracting edge. Membership in the two sets of marks is held as a
pair of sets rather than re-read per thumbnail, so scrolling a large folder never touches them.

## Default app

The launcher prefers a dedicated runtime at
`${XDG_DATA_HOME:-$HOME/.local/share}/2photon/lighttable-venv` when present, falling
back to `python3` otherwise. To create it from the repository root with `uv`:

```bash
runtime="${XDG_DATA_HOME:-$HOME/.local/share}/2photon/lighttable-venv"
uv venv --python 3.13 "$runtime"
uv pip install --python "$runtime/bin/python" -r requirements.txt
```

This keeps Light Table's PyQt6 and NumPy dependencies separate from system package
cleanup. Both menu launches and opening an image use this same runtime.

`./install-default.sh` registers Light Table as the system default for common 2D image types
(PNG, JPEG, TIFF, WebP, BMP, GIF) via a desktop entry and `xdg-mime`. Re-run it from the main
checkout after merging so the desktop entry points at a permanent path.
