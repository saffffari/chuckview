# Lt — Light Table

The 2D image viewer. Three independently collapsible panes: places + folder tree · contact
sheet · viewport. Light Table opens in **Focus** — one full-frame picture, no columns — and the
only chrome left down the side is a hairline gutter with two pull-tabs, **Locations** and
**Gallery**. Click one and its column slides in; the toolbar toggles the same three panes by
name. The contact sheet shows every image in the selected folder **and all of its subfolders**,
newest modified first. Tabs (Ctrl+T or the `+` button) hold independent pane/folder states, can
be reordered, and can be renamed by double-clicking their label.

## The material

The window is one translucent slab of the house grey — chrome and matting let a hint of the
desktop through, and compositors that blur behind a translucent window (KWin, DWM's Mica) make it
frosted for free. It is a tint, never something you read through: the alphas stay high enough
that chrome is legible over a white window, and the picture itself is the only fully opaque thing
on screen, because you have to be able to trust what an image looks like.

## Places — favorites and drives

The top half of the locations column is places, the bottom half is the live folder tree; drag the
handle between them to trade one for the other.

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
- **Horizontal wheel / ← → / Space / Shift+Space** previous–next image
- **MX Master**: forward button pans; back button holds a compare with the last-viewed image
- **RMB drag on the contact sheet** resizes thumbnails; a plain **RMB click** (no drag) opens the
  context menu
- **Shift-click** selects consecutive images, **Ctrl-click** toggles one; drag selections onto a
  folder in the tree to move the files; File → New Folder… (Ctrl+Shift+N)
- **Top toolbar** exposes Copy, Paste, Rename, Copy Path, Reveal, pane toggles, and Focus without
  hiding the matching keyboard shortcuts.
- **W** toggles the two detail panels — folder facts under the tree, image facts under the sheet
- **F** fit · **Shift+F** fill · **1** 1:1 · **Ctrl+Shift+F** focus mode (hides columns) ·
  **Alt+F** fullscreen · **Esc** backs out
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

## Default app

`./install-default.sh` registers Light Table as the system default for common 2D image types
(PNG, JPEG, TIFF, WebP, BMP, GIF) via a desktop entry and `xdg-mime`. Re-run it from the main
checkout after merging so the desktop entry points at a permanent path.
