"""The glass material — one slab of frosted, refracting glass, computed in NumPy.

Light Table's panels float over the picture instead of sitting beside it, so their
material has to do what real glass does: blur what is behind it, bend it at the edges,
and catch a highlight along the rim. That is the whole illusion of depth — a flat tint
reads as paint, a bent edge reads as a pane you could pick up.

The numbers are not invented. KWin's glass effect (kwin-effects-glass, the KDE Store
"Better Blur / liquid" fork) already has the user's taste in it — corner radius, how wide
the refracting edge is, how hard it bends, how much the rim glows — so we read that group
out of ~/.config/kwinrc and use it. The panels then match the desktop they are floating
on, and turning the refraction up out there turns it up in here too. Off KDE, or with the
effect absent, the defaults below stand in and nothing else changes.

Everything here is NumPy and arithmetic: no Qt, no GL, no I/O beyond reading one text
file, so it is testable headless and cheap enough to run on every repaint.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np

# The KWin effect writes its settings into this group of kwinrc.
KWIN_GROUP = "Effect-blurplus"


@dataclass(frozen=True)
class GlassSettings:
    """How the glass behaves. Field names are ours; the defaults are the effect's.

    Distances are pixels. The strengths are the effect's own sliders, turned into pixels
    in `Maps` so there is exactly one place in the app that does that conversion.
    """

    top_radius: float = 12.0        # TopCornerRadius
    bottom_radius: float = 12.0     # BottomCornerRadius
    edge: float = 14.0              # RefractionEdgeSize — width of the bending band
    strength: float = 12.0          # RefractionStrength — how far it bends
    offset: float = 8.0             # RefractionOffsetStrength — outward push
    fringing: float = 8.0           # RefractionRGBFringing — per-channel dispersion
    normal_pow: float = 6.0         # RefractionNormalPow — how sharply it falls off
    bevel: float = 10.0             # RefractionBevelIntensity — lit and shaded edge
    rim_width: float = 19.0         # RimWidth
    rim_specular: float = 10.0      # RimSpecularScale
    rim_glow: bool = True           # RimGlow
    edge_lighting: bool = True      # EdgeLighting
    noise: float = 4.0              # NoiseStrength
    blur: float = 15.0              # BlurStrength
    physical: bool = True           # PhysicallyBasedRefraction

    @property
    def radius(self) -> float:
        """One radius for a panel. The effect keeps top and bottom apart for windows with
        a titlebar; a floating panel is one object, so take the larger of the two."""
        return max(self.top_radius, self.bottom_radius)


DEFAULTS = GlassSettings()

_BOOL_KEYS = {"RimGlow", "EdgeLighting", "PhysicallyBasedRefraction"}
_FIELDS = {
    "TopCornerRadius": "top_radius",
    "BottomCornerRadius": "bottom_radius",
    "RefractionEdgeSize": "edge",
    "RefractionStrength": "strength",
    "RefractionOffsetStrength": "offset",
    "RefractionRGBFringing": "fringing",
    "RefractionNormalPow": "normal_pow",
    "RefractionBevelIntensity": "bevel",
    "RimWidth": "rim_width",
    "RimSpecularScale": "rim_specular",
    "RimGlow": "rim_glow",
    "EdgeLighting": "edge_lighting",
    "NoiseStrength": "noise",
    "BlurStrength": "blur",
    "PhysicallyBasedRefraction": "physical",
}


def kwin_config_file() -> Path:
    """Where KWin keeps its settings. XDG_CONFIG_HOME wins, as everywhere else."""
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "kwinrc"


def read_group(text: str, group: str) -> dict[str, str]:
    """The `key=value` lines of one `[group]` in an ini-shaped file.

    Hand-rolled rather than configparser: kwinrc is full of groups with `[nested][keys]`
    and repeated names that make a strict parser throw, and we only ever want one group.
    """
    wanted, found = f"[{group}]", {}
    inside = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("["):
            inside = stripped == wanted
            continue
        if not inside or not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        found[key.strip()] = value.strip()
    return found


def settings_from_group(values: dict[str, str]) -> GlassSettings:
    """Turn the effect's group into our settings, keeping the default for anything
    missing or unparseable — a half-written config must never take the glass down."""
    kwargs: dict[str, object] = {}
    for key, field in _FIELDS.items():
        if key not in values:
            continue
        raw = values[key]
        if key in _BOOL_KEYS:
            kwargs[field] = raw.strip().lower() in ("true", "1", "yes", "on")
            continue
        try:
            kwargs[field] = float(raw)
        except ValueError:
            continue
    return GlassSettings(**kwargs)  # type: ignore[arg-type]


def read_glass_settings(path: Path | None = None) -> GlassSettings:
    """The desktop's own glass, or ours if the desktop has no opinion."""
    source = Path(path) if path is not None else kwin_config_file()
    try:
        text = source.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return DEFAULTS
    return settings_from_group(read_group(text, KWIN_GROUP))


# --- The material ---------------------------------------------------------------------
# What follows is per-panel-size geometry: a rounded-rectangle distance field, the surface
# normal that falls out of it, and the maps that ride on top — how far each colour channel
# is displaced, where the rim catches light, how the corners fade out. All of it depends
# only on (width, height, settings), so it is built once per size and reused for every
# repaint: a panel dragged across a photograph recomputes nothing.

LIGHT = (-0.6, -0.8)  # the key light sits up and to the left, as it does in the house UI


def rounded_box_sdf(width: int, height: int, radius: float) -> np.ndarray:
    """Signed distance to a rounded rectangle: negative inside, zero on the edge.

    The whole material hangs off this one field. Distance gives the refraction falloff,
    its gradient gives the surface normal, and a half-pixel band around zero gives
    antialiased corners for free."""
    half_w, half_h = width / 2.0, height / 2.0
    radius = float(max(0.0, min(radius, half_w, half_h)))
    ys = np.arange(height, dtype=np.float32)[:, None] + 0.5 - half_h
    xs = np.arange(width, dtype=np.float32)[None, :] + 0.5 - half_w
    qx = np.abs(xs) - (half_w - radius)
    qy = np.abs(ys) - (half_h - radius)
    outside = np.hypot(np.maximum(qx, 0.0), np.maximum(qy, 0.0))
    inside = np.minimum(np.maximum(qx, qy), 0.0)
    return (inside + outside - radius).astype(np.float32)


def surface_normal(sdf: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Outward normal of the glass surface, from the gradient of its distance field."""
    gy, gx = np.gradient(sdf)
    length = np.sqrt(gx * gx + gy * gy)
    length[length < 1e-6] = 1.0
    return (gx / length).astype(np.float32), (gy / length).astype(np.float32)


class Maps:
    """The per-size material: sample offsets, rim light, and the alpha mask.

    Built once per panel size, and deliberately built only for the *edge*. Glass bends
    light where it curves, which on a rounded rectangle is a band a dozen pixels wide
    around the border; the flat middle of a pane is just the blurred backdrop under a
    tint, and Qt fills that in C++ far faster than NumPy can. So the maps here cover the
    band only — a few per cent of the pixels, which is the difference between a panel that
    drags at sixty frames a second and one that does not."""

    def __init__(self, width: int, height: int, settings: GlassSettings = DEFAULTS):
        self.width, self.height, self.settings = width, height, settings
        sdf = rounded_box_sdf(width, height, settings.radius)
        nx, ny = surface_normal(sdf)

        # How deep into the refracting band each pixel is: 0 in the flat middle of the
        # pane, 1 right at the edge. Raised to the effect's normal power, this is the
        # curve of the glass — a wide flat centre and a fast roll-off at the rim.
        edge = max(1.0, settings.edge)
        depth = np.clip(1.0 + sdf / edge, 0.0, 1.0)
        curve = depth ** max(1.0, settings.normal_pow / 2.0)

        # Pixels, from the effect's sliders. Strength bends the picture along the normal;
        # offset pushes the sample outward so the edge shows a sliver of what the panel is
        # covering, which is what makes it read as thick rather than painted on.
        bend = edge * (settings.strength / 20.0) + settings.offset / 4.0
        fringe = settings.fringing / 100.0 if settings.physical else 0.0

        # Rim: a narrow band right at the edge, lit where the surface faces the light and
        # shaded where it turns away. Specular on the lit side, a dark seat on the other.
        rim_band = max(1.0, settings.rim_width / 6.0)
        rim = np.clip(1.0 + sdf / rim_band, 0.0, 1.0) ** 2
        facing = nx * LIGHT[0] + ny * LIGHT[1]
        lit = np.clip(facing, 0.0, 1.0)
        shaded = np.clip(-facing, 0.0, 1.0)
        glow = settings.rim_specular / 10.0 if settings.rim_glow else 0.0
        highlight = rim * (lit ** 3) * glow * 90.0
        if settings.edge_lighting:
            # One bright hairline exactly on the boundary — the wet look.
            highlight = highlight + np.clip(1.0 - np.abs(sdf) / 1.2, 0.0, 1.0) * lit * 55.0
        # The bevel is the wider, softer version of the same light: it gives the pane a
        # thickness you can see across the whole band, not just a line on the border.
        bevel = curve * (lit - shaded) * (settings.bevel / 100.0)
        seat = rim * shaded * 26.0
        # Antialiased corners: one pixel of fade across the zero crossing.
        alpha = np.clip(0.5 - sdf, 0.0, 1.0)

        # The band is every pixel the glass actually changes — anything that bends light,
        # catches the rim, or sits in the fade at a rounded corner. Everything else is
        # flat pane and belongs to Qt.
        touched = (curve > 0.002) | (rim > 0.002) | (alpha < 0.999)
        self.band = np.flatnonzero(touched.reshape(-1)).astype(np.int32)
        rows, columns = np.divmod(self.band, width)

        # Where each band pixel reads the backdrop from, per colour channel. Physically
        # based refraction disperses: red bends furthest, blue least, which is the faint
        # colour seam you see along the edge of real glass.
        shift = (curve.reshape(-1)[self.band] * bend).astype(np.float32)
        normal_x = nx.reshape(-1)[self.band]
        normal_y = ny.reshape(-1)[self.band]
        self.samples = []
        for channel_scale in (1.0 + fringe, 1.0, 1.0 - fringe):
            sample_x = np.clip(columns + normal_x * shift * channel_scale, 0, width - 1)
            sample_y = np.clip(rows + normal_y * shift * channel_scale, 0, height - 1)
            flat = sample_y.astype(np.int32) * width + sample_x.astype(np.int32)
            self.samples.append(flat)

        rng = np.random.default_rng(7)  # fixed: the grain must not crawl between frames
        grain = (rng.random(self.band.size, dtype=np.float32) - 0.5) * settings.noise

        self.light = (highlight.reshape(-1)[self.band] - seat.reshape(-1)[self.band]
                      + grain).astype(np.float32)
        self.gain = (1.0 + bevel.reshape(-1)[self.band]).astype(np.float32)
        self.alpha = (alpha.reshape(-1)[self.band] * 255.0).astype(np.uint8)

    def rim_image(self, backdrop: np.ndarray | None, tint: tuple[int, int, int],
                  tint_alpha: float) -> np.ndarray:
        """The refracting edge as an RGBA uint8 array the size of the panel.

        The flat middle comes back fully transparent on purpose: the caller has already
        drawn the blurred backdrop and the tint there. `backdrop` is what is behind the
        panel, already blurred, already at panel size; None means nothing has been
        captured yet, and the glass falls back to its tint."""
        height, width = self.height, self.width
        tint_rgb = np.array(tint, dtype=np.float32)
        count = self.band.size
        if backdrop is None:
            colour = np.broadcast_to(tint_rgb, (count, 3)).astype(np.float32).copy()
        else:
            flat = backdrop.reshape(-1, 3)
            bent = np.empty((count, 3), dtype=np.uint8)
            for channel, samples in enumerate(self.samples):
                bent[:, channel] = flat[samples, channel]
            colour = bent.astype(np.float32)
            # Frost: the tint is a veil over the blurred picture, never a wash that hides
            # it. Alpha stays high enough that white ink is legible over a white photo.
            colour += (tint_rgb - colour) * tint_alpha

        colour *= self.gain[:, None]
        colour += self.light[:, None]
        np.clip(colour, 0.0, 255.0, out=colour)

        out = np.zeros((height * width, 4), dtype=np.uint8)
        out[self.band, :3] = colour.astype(np.uint8)
        out[self.band, 3] = self.alpha
        return out.reshape(height, width, 4)


class MaterialCache:
    """Maps are expensive to build and cheap to keep. Panels come in a handful of sizes
    and change size only while someone is dragging an edge, so a small cache is the
    difference between glass that is free and glass that stutters."""

    def __init__(self, settings: GlassSettings | None = None, limit: int = 8):
        self.settings = settings if settings is not None else read_glass_settings()
        self.limit = limit
        self._cache: dict[tuple[int, int], Maps] = {}

    def maps(self, width: int, height: int) -> Maps:
        width, height = max(1, int(width)), max(1, int(height))
        key = (width, height)
        found = self._cache.get(key)
        if found is None:
            if len(self._cache) >= self.limit:
                self._cache.pop(next(iter(self._cache)))
            found = self._cache[key] = Maps(width, height, self.settings)
        return found

    def rim_image(self, backdrop: np.ndarray | None, width: int, height: int,
                  tint: tuple[int, int, int], tint_alpha: float) -> np.ndarray:
        return self.maps(width, height).rim_image(backdrop, tint, tint_alpha)


def blur_passes(settings: GlassSettings, capture_scale: int) -> int:
    """How many halve-and-restore passes the backdrop needs to look frosted.

    The blur itself is Qt's, not ours — smooth-scaling an image down and back up is a
    box filter written in C++, and chaining a few of them is a Gaussian for all the eye
    can tell. All this has to decide is how many, given that the capture is already
    reduced by `capture_scale` and so already carries some of the blur."""
    effective = max(1.0, settings.blur / max(1, capture_scale))
    return int(max(1, min(4, round(effective / 2.0))))

