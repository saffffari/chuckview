# Lt — Light Table

The 2D image viewer. Three full-height columns: folder tree · contact sheet · viewport. The
contact sheet shows every image in the selected folder **and all of its subfolders**, newest
modified first. No visible buttons — menus and gestures only. Tabs (Ctrl+T) hold independent
three-column states.

## Controls

- **LMB drag** pan · **RMB drag** zoom (on z-stacks, horizontal RMB drag scrubs slices)
- **Scroll** zoom at cursor on flat images; steps slices on z-stacks
- **Horizontal wheel / ← → / Space / Shift+Space** previous–next image
- **MX Master**: forward button pans; back button holds a compare with the last-viewed image
- **RMB drag on the contact sheet** resizes thumbnails
- **Shift-click** selects consecutive images, **Ctrl-click** toggles one; drag selections onto a
  folder in the tree to move the files; File → New Folder… (Ctrl+Shift+N)
- **W** toggles the two detail panels — folder facts under the tree, image facts under the sheet
- **F** fit · **Shift+F** fill · **1** 1:1 · **Ctrl+Shift+F** focus mode (hides columns) ·
  **Alt+F** fullscreen · **Esc** backs out
- `lighttable image.png` or `lighttable folder/` opens directly (files are files)

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
