# Figure extraction

`puba figures` extracts per-figure image artifacts from MinerU's already-computed
layout output. No additional model inference is performed — figures are derived
from the `*_content_list.json` file written during `puba md`.

## Prerequisites

`puba figures` requires:

1. **`bib.yaml` resolved** — same gate as `puba md`: `bib.yaml` must exist and
   `needs_review` must be `false`. Run `puba bib <pdf>` first.
2. **`paper.md` rendered** — MinerU must have already run (`puba md <pdf>` first).
   `puba figures` will error if `paper.md` is not yet rendered; it does not
   auto-render it.

## Output layout

```
paper.puba/
├── paper.figures.json          ← figure manifest (one JSON object)
├── mineru/
│   └── images/                 ← MinerU raw crops (sha-named *.jpg)
└── figures/
    ├── page006_img1.jpg        ← renamed copy of MinerU crop
    ├── page006_img1.json       ← per-figure metadata sidecar
    ├── page010_img1.jpg
    ├── page010_img1.json
    └── ...
```

### Naming convention

`page{NNN}_img{M}.jpg` where:
- `NNN` — 1-indexed physical PDF page number, zero-padded to 3 digits.
  Matches the `<!-- page N -->` markers in `paper.md`.
- `M` — 1-indexed figure sequence within that page (resets per page).

### `paper.figures.json` schema

```json
{
  "mineru_version": "mineru-5",
  "figures_version": "figures-1",
  "figures": [
    {
      "id":         "page006_img1",
      "page":       6,
      "page_idx":   5,
      "type":       "image",
      "bbox":       [369, 118, 687, 281],
      "width_pt":   318,
      "height_pt":  163,
      "width_px":   1272,
      "height_px":  652,
      "caption":    "Fig. 1. Hybrid simulation's modeling phases.",
      "footnote":   null,
      "source_sha": "89bc7818fdc97c3bf1b6f884d9a21f577dc1ffdd755114dd1f5b57c3b0d8ad41",
      "jpg":        "/abs/path/to/paper.puba/figures/page006_img1.jpg"
    }
  ]
}
```

| Field | Description |
|---|---|
| `id` | Filename stem; unique within the paper |
| `page` | 1-indexed physical PDF page (matches `<!-- page N -->` markers) |
| `page_idx` | 0-indexed page (MinerU's native numbering) |
| `type` | `"image"`, `"chart"`, or `"table"` |
| `bbox` | `[x0, y0, x1, y1]` in PDF points (top-left origin) |
| `width_pt` / `height_pt` | Bounding-box dimensions in PDF points |
| `width_px` / `height_px` | Raster dimensions of the `.jpg` file in pixels |
| `caption` | Figure caption text from MinerU, or `null` if absent |
| `footnote` | Figure footnote text from MinerU, or `null` if absent |
| `source_sha` | SHA256 stem of the original MinerU crop in `mineru/images/` |
| `jpg` | Absolute path to the renamed JPG in `figures/` |

## CLI

### `puba figures`

```
puba figures PDF [--force] [--types LIST] [--json]
```

| Flag | Default | Description |
|---|---|---|
| `PDF` | — | Path to the paper PDF |
| `--force` | off | Re-extract even if cached |
| `--types LIST` | `image,chart,table` | Comma-separated subset of figure types |
| `--json` | off | Emit JSON status envelope on stdout |

**`--json` output:**

```json
{
  "ok": true,
  "command": "figures",
  "pdf": "/path/to/paper.pdf",
  "analysis_dir": "/path/to/paper.puba",
  "manifest": "/path/to/paper.puba/paper.figures.json",
  "figures_count": 18
}
```

### `puba show figures`

```
puba show figures PDF [--json]
```

Lists all extracted figures in a table:

```
ID               PAGE  TYPE    SIZE (px)     CAPTION
page006_img1        6  image   1272× 652     Fig. 1. Hybrid simulation's modeling phases.
page010_img1       10  chart   2200× 800     Fig. 2. Performance results.
page010_img2       10  chart   2200× 720
```

| Flag | Description |
|---|---|
| `--json` | Emit full manifest as JSON on stdout |

To embed image data, use `puba show figure ID --json --embed` (single-figure form).

### `puba show figure ID`

```
puba show figure PDF ID [--json] [--embed] [--path]
```

Shows detail for a single figure:

```
page006_img1
  Page      : 6 (page_idx 5)
  Type      : image
  Size      : 1272 × 652 px  (318 × 163 pt)
  Bbox      : [369, 118, 687, 281]
  JPG       : /path/to/paper.puba/figures/page006_img1.jpg
  Caption   : Fig. 1. Hybrid simulation's modeling phases.
  Footnote  : (none)
  Source SHA: 89bc7818...
```

| Flag | Description |
|---|---|
| `--json` | Emit single manifest entry as JSON |
| `--embed` | Add `data_url` field (requires `--json`) |
| `--path` | Print only the absolute JPG path (mutually exclusive with `--json`) |

`--path` is useful for shell composition:

```bash
open $(puba show figure paper.pdf page006_img1 --path)
cp $(puba show figure paper.pdf page006_img1 --path) ~/slides/
```

## Caching

The figures stage uses `paper.puba/.state.json` (same mechanism as `puba md`
and `puba bib`). The cache key includes the `figures_version` from config and
the active `--types` set. Changing `--types` invalidates the cache and
re-extracts. Use `--force` to unconditionally re-run.

`puba clean paper.pdf --what figures` removes `paper.figures.json` and the
`figures/` directory, clearing the figures cache without touching `bib.yaml`
or `paper.md`.

## Captions

Captions come directly from MinerU's layout analysis. Coverage varies by paper
type:

- **Tables:** nearly always captioned (100% in tested fixtures).
- **Single-panel images:** usually captioned (70-95%).
- **Charts and multi-panel figures:** unreliable — the master caption may be
  attached to only one panel, or missing entirely from all panels.

The `caption` field is `null` (not `""`) when MinerU found no caption. Treat
`caption` as a hint, not ground truth. Consumers requiring authoritative
captions should verify against the markdown body (`paper.md`) using the
`<!-- page N -->` markers, or use a vision model to generate a caption from
the image.
