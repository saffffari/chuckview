# ChuckView

**Find and collect medical images for research datasets.** A catalog you import X-rays and
CT scans into, Lightroom-style: filter by anything in the DICOM header, look at the films
and scroll the CTs at full resolution, pick, rate, tag and annotate, then export the
selection as a manifest with leakage-free train/val/test splits.

Named for Chuck, whose idea it was. The viewer started life as
[Light Table](https://github.com/saffffari/lighttable) and keeps its material: black, white,
a hairline, and orange only for what is selected.

## Running it

**Windows:** download `ChuckView-windows.zip` from the latest
[CI run](../../actions/workflows/ci.yml) (Artifacts), unzip anywhere, run
`ChuckView\ChuckView.exe`. No installer, no admin rights.

**From source** (Windows, Linux, macOS — Python 3.11+):

```bash
uv venv --python 3.13 .venv
uv pip install --python .venv -r requirements.txt   # PyQt6, NumPy, pydicom, pylibjpeg codecs
./run.sh            # Linux / macOS
run.cmd             # Windows
```

`chuckview some.chuck` opens a catalog; `chuckview /path/to/folder` opens the import
dialog on that folder; `chuckview --self-test` prints which DICOM codecs this install can
decode.

## The catalog

- **Import** (Ctrl+Shift+I): choose a folder and see *everything under it* — every
  subfolder — before anything is written. DICOM is grouped as the library will show it:
  CT slices become one series however they were exported (one folder per slice is fine;
  so are extensionless `IM000123` and UID-named files), and every X-ray stays its own
  picture even when a study shares one series UID. Files already in the catalog are shown
  and unticked. **Nothing is copied**: the catalog records where files are, relative to the
  folder you imported, so a drive that comes back as `F:` instead of `E:` is fixed with
  *Find Missing Folder…* on that folder.
- **The catalog file** (`*.chuck`, SQLite) and its **previews** (`*.chuckdata/`, a 256 px
  thumbnail and a 2048 px preview of every item) sit side by side. Browsing, filtering,
  tagging and rating work from those with the originals offline; the loupe shows the
  preview and says so.
- **One person per catalog.** Keep it on a local disk, not a network share.

## Privacy

The catalog never stores a patient's name, ID, birth date, accession number, institution
or any date beyond the study year. Age is capped at 90 (HIPAA Safe Harbor). To group a
patient's images — which a train/test split needs — PatientID is turned into an
HMAC-SHA256 keyed by a secret generated inside each catalog: the same person gets the same
key in one catalog, different keys in two catalogs, and the key cannot be reversed by
hashing every possible MRN. The tests check the catalog file byte-for-byte for identifiers.

The **DICOM** section of the Info panel reads the full header live from the file and masks
every person name, date and time, identifier, address, institution/device field, free-text
patient comment and private tag (`••••••`). *Reveal identifiers* shows them for the
session only and is never saved.

Thumbnails made during an import preview are cached in the per-user cache
(`%LOCALAPPDATA%\ChuckView` / `~/.cache/chuckview`); they are pixels, not headers.

## Searching

The search line (Ctrl+F) takes words and `field:value` terms; the facet columns under it
(`\` shows and hides them) count every value of four fields you choose, under everything
else that is selected.

```
mod:CT thick:<=1.5 body:spine          thin-slice spine CTs
mod:DX view:AP,PA -is:reject           frontal films, not rejected
age:10..18 sex:F tag:scoliosis         adolescent girls you tagged
"hardware"  OR  note:rod               a word in any text or note
frames:>1 has:notes rating:>=3
```

| field | | field | |
|---|---|---|---|
| `mod` `kind` `body` `view` `lat` | modality, xray/series/image, body part, view position, laterality | `age` `year` `sex` | capped age, study year, sex |
| `series` `study` `protocol` | descriptions | `thick` `spacing` `z` `frames` `rows` `cols` `kvp` `bits` | numbers: `<` `<=` `>` `>=` `=` `a..b` |
| `mfr` `model` `kernel` `contrast` `imagetype` `transfer` | acquisition | `rating` `label` `tag` `note` `in` (collection) `path` `patient` | your marks |
| `is:` | `pick` `reject` `unflagged` `missing` `series` `xray` `stack` `rated` | `has:` | `notes` `tags` `patient` `label` `window` |

`-` negates a term, commas mean any-of, `=value` matches exactly, `OR` joins neighbours.
Ctrl+Alt+N saves the search as a **smart collection**.

## Looking

Double-click, Enter or E opens the **loupe**; G or Esc goes back to the grid.

- **Scroll a CT**: the wheel, ↑ ↓ (PgUp PgDn by ten), right-drag sideways, or the slice
  ruler under the picture — click or drag anywhere on it. The ruler also shows how much of
  the stack is decoded.
- **Window/level**: middle-drag or Shift+left-drag (right widens, down darkens).
  **L** steps presets (CT: auto, soft tissue, bone, lung, brain; X-ray: auto, full range).
  **I** inverts.
- **V**: axial → coronal → sagittal (once the stack is in memory).
- **Z** fit / 1:1, **Shift+Z** fill, Ctrl+wheel zoom, left-drag pan.
- ← → previous / next item, in the grid and the loupe.

### Built for slow machines

Nothing decodes a whole volume to open it. The slice on screen is decoded when you get to
it; the rest of the stack is decoded in the background outward from where you are, on its
own threads, under a memory budget of a quarter of the machine's RAM (256 MB–2 GB) — so an
8 GB laptop keeps the slices around you and a workstation keeps the whole CT. Pixels are
kept as stored and windowed through a 65,536-entry lookup table, so dragging the window on
a 9-megapixel EOS film costs an index, not arithmetic; while dragging, films that large are
previewed at half resolution. A big film shows its preview at once and sharpens when it has
decoded, and the next item in the direction you are stepping is decoded before you get
there.

## Marking and exporting

| key | | key | |
|---|---|---|---|
| P / X / U | pick / reject / unflag (P again clears) | 0–5 | rating |
| 6 7 8 9 | red / yellow / green / blue label | Ctrl+K | tags |
| Ctrl+' | note | Ctrl+N | collection from the selection |
| B | add to the target collection | Ctrl+Shift+E | export manifest |

Marks apply to the selection in the grid, or to the item in the loupe. *Mark → Auto
Advance* moves to the next item after each mark.

**Export Manifest** writes a CSV, one row per item: the key file, kind, modality, series
and study UIDs, the keyed patient hash, matrix, spacing, descriptions, acquisition fields,
your rating/flag/label/tags/notes, every file of a series, and — if you give
percentages — a `split` column assigned **by patient**, deterministically, so no patient's
images land on both sides.

## Layout

| path | |
|---|---|
| `chuckview/catalog.py` | the catalog: schema, import, search, facets, marks, collections, export |
| `chuckview/query.py` | the search language → parameterised SQL |
| `chuckview/dicom.py` | sniffing, headers, series grouping, the lazy slice source, windowing |
| `chuckview/phi.py` | the header for the Info panel, identifiers masked |
| `chuckview/previews.py` | the `.chuckdata` previews |
| `chuckview/app.py` `library.py` `inspector.py` `importer.py` `viewer.py` `stack.py` | the window |
| `packaging/` | PyInstaller spec, icon, frozen entry point |

Tests: `QT_QPA_PLATFORM=offscreen python -m unittest discover -s tests -t .` — all on
synthetic DICOM generated in `tests/fixtures.py`; no patient data is in this repository.

GPLv3.
