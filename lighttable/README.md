# Lt — Light Table

The 2D image viewer. Three columns, left to right — **Locations**, **Gallery**, **Viewer** — and
that order is the app: where, then which, then this one. The contact sheet shows every image in the
selected folder **and all of its subfolders**, newest modified first. Tabs (Ctrl+T) hold
independent column/folder states, can be reordered, and can be renamed by double-clicking their
label; the tab strip only appears once there is more than one tab.

## How it opens — on what you handed it

- **An image** — from the file manager, or `lighttable shot.png` — opens in **Focus**: the picture
  edge to edge, no columns, no chrome but the menu bar. You asked to look at a picture.
- **A folder**, or the app on its own, opens on the **folder and the gallery**. No viewer column
  takes up a third of the window for a picture you have not picked yet.
- **Double-click a frame** and the viewer opens beside the sheet — it does not throw the sheet away
  for a full-bleed view you did not ask for. `Ctrl+Shift+F` is the one that goes full bleed.

## The columns — sized, never rearranged

Every seam between two columns is a drag handle, so the proportions are yours. The order is not:
there is nowhere to drag a column *to*, and no arrangement to reset, because a picture browser you
can rearrange is a picture browser you have to re-learn every time you open it.

- **Resize** by dragging any seam — between the columns, and between the places and the tree inside
  the sidebar.
- **Open and close** a column from the View menu, or `Ctrl+Shift+F` for Focus. The last column
  standing cannot be closed: closing it leaves the viewer, which is Focus.
- **The rightmost open column takes the leftovers.** Widening the window widens the picture (or the
  sheet, with no viewer open), never the sidebar.
- Widths persist to `~/.config/2photon/lighttable-panels.txt` — one line per column,
  `name width open`. Edit it by hand; there is no settings dialog. **View → Reset Column Widths**
  puts them back.

## There are no buttons

There was a bar of twelve across the top of the sidebar — Quick, Flag, Copy, Paste, Rename, Path,
Reveal, ＋ Tab, three column toggles, Facts, Focus — and it was the part of the app nobody touched
twice. Every one of them was a key you already press or a line in a menu you already have, and
together they cost a strip of the one column that has real work to do.

What is left in the whole window is **one line to type a search on**. The verbs are the keys, the
menu bar, and the right-click menu on a frame; what is open is the View menu, where the four
switches are checkmarks — which is the only thing the buttons were really carrying.

## The material — black, white, and a hairline

Light Table is made of what [PC-6](../../../PC-6) is made of, for the same reason: a readout should
look etched into the panel, not like a card lying on one.

- **One ground.** Every surface in the app is the same black, the matting included. The photograph
  is the brightest thing on screen and the only thing in colour, so you can trust what it looks
  like.
- **A hairline, or nothing.** Where two things have to be told apart there is a 1px rule. No slab a
  shade lighter, no border box, no rounded corner, no shadow, no gradient.
- **Monospaced small capitals** for every letter of chrome, so keys line up in a column and a path
  does not reflow when a digit in it changes.
- **The facts are a readout**: a three-letter key hard left, the value hard right, a rule under the
  row — `DIR` `IMG` `NEW` `SEL` `DIM` `SIZ` `MRK`. A column head is the same three moves, which is
  why the Gallery's head carries the frame count on its right.
- **Selection is the one colour** and it is a hairline too: an orange ring on the thumbnail's own
  black padding, never on the picture, and orange text in the sidebar. Nothing else in the app is
  ever orange.

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

The top of the Locations column is the search line; under it are the two sets of marks, your
places and the drives, and under those the live folder tree. Drag the handle between the two lists
to trade one for the other.

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
- **← →** previous–next image, from anywhere in the window. They are the pictures' keys even when
  the keyboard is in the folder tree or the sidebar — going through a shoot cannot depend on having
  clicked the right pane first — and they step aside while you are typing in the search line. The
  tree gives up expand/collapse to them, which it can spare: a disclosure triangle is one click
  away and the next frame is not. **Space / Shift+Space** do the same thing.
- **MX Master**: forward button pans; back button holds a compare with the last-viewed image
- **RMB drag on the contact sheet** resizes thumbnails; a plain **RMB click** (no drag) opens the
  context menu
- **Shift-click** selects consecutive images, **Ctrl-click** toggles one; drag selections onto a
  folder in the tree to move the files; File → New Folder… (Ctrl+Shift+N)
- **Search** sits at the top of the sidebar (`Ctrl+F`). Type to narrow the sheet to matching images —
  every word has to match, in any order, anywhere in the path below the folder on screen, so
  `retro cockpit` finds the twelve pictures in `retro cockpits/`. It filters what is already
  scanned, so it never touches the disk; searching in Focus opens the Gallery, because results
  you cannot see are not results. `Esc` clears it and hands the keyboard back to the picture.
  Each tab keeps its own search. While you are typing in it the bare-letter keys step aside, so
  searching for `a car` filters instead of flagging half the shoot.
- **A** quick collection · **F** flag — both toggle, both work on the selection
- **W** toggles the Facts, at the foot of the sidebar (folder facts over image facts)
- **0** fit · **9** fill · **1** 1:1 · **Ctrl+Shift+F** focus mode (the viewer alone) ·
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

The chrome costs nothing to draw: it is flat fill and a stylesheet, with no per-pixel material and
nothing sampling the picture, so the viewport scrolls its own backing store and repaints only the
sliver that moved. Membership in the two sets of marks is held as a pair of sets rather than
re-read per thumbnail, so scrolling a large folder never touches them.

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
