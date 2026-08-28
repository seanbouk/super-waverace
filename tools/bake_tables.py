#!/usr/bin/env python3
"""Bake-time generator for the Mode 7 rolling sea.

Reads wave feel parameters from tools/wave_params.json (exported from the
wave-visualizer web tool) and bakes, for each wave phase, per-scanline HDMA
tables:
  - TM      ($212C): backdrop-only for sky lines, BG1 for sea
  - M7A     ($211B): horizontal texel-per-pixel scale (8.8) ~ perspective
  - M7Y     ($2120): sampled texture row = first ray/wave crossing
            (with M7B=M7C=M7D=0 the sampled row is EXACTLY the M7Y value)
  - COLDATA ($2132): additive white per scanline - crest glow

Also produces the Mode 7 background data directly (sea.pc7 tiles, sea.mp7
map, sea.pal palette) - either from the procedural default texture or from
assets/sea_pattern.png + assets/water_params.json exported by the
water-designer web tool. Emitting these ourselves (instead of via gfx4snes)
keeps palette INDICES stable, which the runtime palette rotation relies on.

Outputs into game/: sea.pc7 sea.mp7 sea.pal sea.png (preview)
                    wavetables.asm wavedata.c wavedata.h
"""

import json
import math
import os
import struct
import zlib

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(TOOLS_DIR, ".."))
OUT_DIR = os.path.join(ROOT, "game")
ASSETS = os.path.join(ROOT, "assets")

SCANLINES = 224
SKY_TM = 0x10        # sky lines: sprites + backdrop only
SEA_TM = 0x13        # sea lines: BG1 + BG2 (EXTBG) + sprites
UI_LINES = 32        # top band: BG mode 1 HUD (blank row + titles + 2 value
                     # rows - row 0 stays empty for CRT overscan)
SKY_RGB = (16, 60, 150)  # backdrop / palette index 0 (deep azure zenith)
# per-scanline additive sky gradient (COLDATA, riding the crest-glow HDMA
# channel): 0 at the top of the sky, this much white added at the horizon
SKY_GRAD_MAX = 17
# The sky above the HIGHEST horizon of the wave cycle runs in MODE 1: real
# 4bpp tiles (palette row 2) drawing the same gradient with 2D dithering -
# far smoother than COLDATA's 5-bit steps. The mode switches back to 7 a
# safe margin above the highest horizon; the strip between the switch and
# the true (moving) horizon keeps the COLDATA backdrop gradient.
SKY_SAFE = 6         # scanlines of margin above the highest horizon
# BG1/BG3 char base moved to 0x4000 (the once-free bank) so 0x7000-0x7FFF
# could become OBJ name table 2 for the tall racers. Physical layout:
# 0x4000 UI map, 0x4400 BG3 cloud map, 0x4800 HUD font, then cloud chars;
# font stays at 0x5000 (ids 256+), sky rows stay at 0x5C00 (ids 448+).
SKY_CHAR0 = 448      # sky row tile ids from the 0x4000 base = VRAM 0x5C00
# CLOUD_CHAR0 (BG3 2bpp cloud char base) is derived below HUD_GLYPHS: the
# cloud chars sit immediately after the HUD font, which once silently
# overlapped them when it grew (assert guards both ends now)
CLOUD_ROW0 = 5       # strip's top map/screen tile row (scanline 40)
CLOUD_TROWS = 4      # strip height in tile rows
CLOUD_SHADE = (204, 222, 242)  # CGRAM 50: soft cloud underside

# fixed per-course maxima: WRAM array sizes and the OAM layout hang off
# these (NOT per-course - see wavedata.h comment); actual counts are runtime
MAX_BUOYS = 16
MAX_PATH = 24

DEFAULTS = {
    "camH": 34.0, "pitch": -10.0, "fovV": 25.0, "fovH": 60.0,
    "amp": 18.0, "wavelength": 128.0, "maxX": 2048.0,
    "phases": 32, "framesPerPhase": 2,
    "crestGlow": 12, "glowGamma": 2.0,
    # rotation defaults (overridden by assets/water_params.json if present)
    "rotStart": 1, "rotCount": 3, "rotFrames": 8,
    # jet ski: world distance ahead of the camera it floats at
    "skiDist": 200.0,
}


def load_params():
    p = dict(DEFAULTS)
    path = os.path.join(TOOLS_DIR, "wave_params.json")
    if os.path.exists(path):
        with open(path) as f:
            p.update(json.load(f))
    wp = os.path.join(ASSETS, "water_params.json")
    if os.path.exists(wp):
        with open(wp) as f:
            p.update(json.load(f))
    # wavelength must give a whole number of waves per 1024-texel map
    n = max(1, round(1024.0 / float(p["wavelength"])))
    p["wavelength"] = 1024.0 / n
    assert p["phases"] & (p["phases"] - 1) == 0, "phases must be a power of two"
    assert p["framesPerPhase"] in (1, 2, 4, 8), "framesPerPhase must be 1/2/4/8"
    return p


P = load_params()
TAN_HALF_H = math.tan(math.radians(P["fovH"]) / 2)
K_WAVE = 2 * math.pi / P["wavelength"]


# ---- raycast (same math as the web visualizer) --------------------------

def wave_y(x, phi):
    return P["amp"] * math.sin(K_WAVE * x + phi)


def cast(angle, phi):
    t = math.tan(angle)

    def above(x):
        return (P["camH"] + x * t) - wave_y(x, phi)

    if above(0.0) <= 0.0:
        return 0.0
    x_prev = 0.0
    x = 0.25
    while x <= P["maxX"]:
        if above(x) <= 0.0:
            lo, hi = x_prev, x
            for _ in range(30):
                mid = (lo + hi) / 2
                if above(mid) <= 0.0:
                    hi = mid
                else:
                    lo = mid
            return (lo + hi) / 2
        x_prev = x
        x += max(0.25, x * 0.01)
    return None


def raycast_phase(phi):
    hits = []
    for i in range(SCANLINES):
        frac = i / (SCANLINES - 1)
        ang = math.radians(P["pitch"] + P["fovV"] / 2 - frac * P["fovV"])
        hits.append(cast(ang, phi))
    n_sky = 0
    while n_sky < SCANLINES and hits[n_sky] is None:
        n_sky += 1
    sea = [h if h is not None else P["maxX"] for h in hits[n_sky:]]
    return n_sky, sea


# ---- HDMA table encoding -------------------------------------------------

def hdma_table(n_repeat, repeat_bytes, line_entries):
    out = bytearray()
    n = n_repeat
    while n > 0:
        c = min(n, 127)
        out.append(c)
        out += bytes(repeat_bytes)
        n -= c
    i = 0
    while i < len(line_entries):
        c = min(len(line_entries) - i, 127)
        out.append(0x80 | c)
        for e in line_entries[i:i + c]:
            out += bytes(e)
        i += c
    out.append(0x00)
    return bytes(out)


def repeat_blocks(n, value):
    out = bytearray()
    while n > 0:
        c = min(n, 127)
        out.append(c)
        out.append(value)
        n -= c
    return out


def sky_add_at(line, sky_ref):
    """The fixed-from-the-top sky light-field: COLDATA add units at an
    absolute scanline, normalised against the deepest horizon of the whole
    cycle (the moving horizon slices into it, it never breathes). LINEAR:
    the old pow-1.3 curve rounded the first two anchors both to zero,
    which drew a flat backdrop-blue band under the HUD; linear makes
    adjacent anchors differ by ~1 add unit from the very first row, so
    the top dithers straight out of the HUD's backdrop colour."""
    span = max(1, sky_ref - UI_LINES - 1)
    return SKY_GRAD_MAX * min(1.0, (line - UI_LINES) / span)


def phase_tables(phi, sky_ref, switch):
    """HDMA tables that stay camera-independent: sky/sea split + crest glow.
    Lines up to `switch` run in mode 1 (text band + tiled sky, no COLDATA
    add); the safe strip from `switch` to this phase's horizon keeps the
    backdrop + COLDATA gradient; then BG1 sea with crest glow.
    Returns (tm, glow, n_sky)."""
    n_sky, sea_x = raycast_phase(phi)
    assert n_sky >= switch, \
        "horizon above the mode switch (n_sky={0} < {1})".format(n_sky, switch)

    g_entries = []
    # safe strip: SOLID blue - the exact bottom colour of the mode-1 sky
    # band (backdrop + the same integer add its last anchor uses), flat to
    # the horizon
    strip = min(31, round(sky_add_at(switch, sky_ref)))
    for i in range(n_sky - switch):
        g_entries.append((0xE0 | strip,))
    for x in sea_x:
        # crest glow: sin() is 1 exactly at wave tops, fades down the flanks
        c = math.sin(K_WAVE * x + phi)
        b = round(P["crestGlow"] * max(0.0, c) ** P["glowGamma"]) if c > 0 else 0
        g_entries.append((0xE0 | min(31, b),))

    # TM: BG1 for the text band, BG1+BG3 through the tiled sky (the
    # scrolling cloud overlay - BG3, NOT BG2: EXTBG is on all frame for
    # the sea, and on real hardware it mangles BG2 outside mode 7), then
    # backdrop-only safe strip, then the sea
    tab_tm = bytes(repeat_blocks(UI_LINES, 0x11)
                   + repeat_blocks(switch - UI_LINES, 0x15)
                   + repeat_blocks(n_sky - switch, SKY_TM)
                   + bytearray((0x81, SEA_TM, 0x00)))
    tab_g = hdma_table(switch, (0xE0,), g_entries) # mode-1 region: add zero
    return tab_tm, tab_g, n_sky


def sky_palette(switch, sky_ref, sky_rgb):
    """The 16 band anchors for one zenith colour: 5-bit base plus the
    INTEGER white adds COLDATA uses, so the solid mode-7 strip below (which
    adds the last anchor's value to backdrop 0 = the same base) continues
    bit-exactly. Per course: a sunset is a warm zenith paling to peach."""
    base5 = [c >> 3 for c in sky_rgb]

    def anchor_add(k):
        line = UI_LINES + (switch - UI_LINES) * k / 15.0
        return min(31, round(sky_add_at(line, sky_ref)))

    pal = bytearray()
    for k in range(16):
        a = anchor_add(k)
        r, g, b = (min(31, c + a) for c in base5)
        pal += struct.pack("<H", (b << 10) | (g << 5) | r)
    return bytes(pal)


def build_sky_band(switch, sky_ref):
    """Mode-1 sky tiles: an azure gradient from the UI band down to the
    mode switch, 16-colour palette with per-line 4px dithering (2D once
    rows stack). Two exactness rules: (1) BG scroll is off by one (screen
    line N samples MAP line N+1), so the tiles are authored map-line
    indexed, one row taller than the visible band; (2) the 16 anchors are
    the 5-bit backdrop plus INTEGER adds - the same arithmetic COLDATA
    does - so the solid mode-7 strip below continues the last anchor
    bit-exactly. Returns (tiles, palette bytes, n_map_rows)."""
    n_rows = (switch - UI_LINES) // 8 + 1  # +1: the scroll off-by-one row
    # index 0 is TRANSPARENT in 4bpp: those pixels show the backdrop -
    # which IS the gradient's first colour (add 0, same as the HUD's
    # background), giving 16 gradient colours from a 15-entry palette.
    # The top must DITHER out of it, never sit flat: that needs anchor 1
    # to differ from anchor 0, which the linear field guarantees. The
    # tiles are colour-agnostic (index dithers); the PALETTE is per course.
    pal = sky_palette(switch, sky_ref, SKY_RGB)  # boot default
    pats = ((0, 0, 0, 0), (1, 0, 0, 0), (1, 0, 1, 0), (1, 1, 1, 0))
    grid = [[0] * 8 for _ in range(n_rows * 8)]
    for g in range(n_rows * 8):
        # map line UI_LINES+g draws on SCREEN line UI_LINES+g-1
        s = min(switch - 1, max(UI_LINES, UI_LINES + g - 1))
        p = 15.0 * (s - UI_LINES) / max(1, switch - UI_LINES - 1)
        k = min(15, int(p))
        d = pats[min(3, int((p - k) * 4))]
        for x in range(8):
            c = k + d[(x + g) & 3] # rotate the pattern per line: Bayer-ish
            grid[g][x] = min(15, c)
    return encode_4bpp(grid, 1, n_rows), bytes(pal), n_rows


def build_clouds():
    """BG3 cloud overlay: a 256x32 strip of dithered cumulus, 2bpp
    palette group 7 (0 transparent, 1 = white at CGRAM 29, 2 =
    CLOUD_SHADE at CGRAM 30 - spare entries of the UI text row). X wraps,
    so the 256px map loops seamlessly - and 256px against 256 binary
    degrees of heading means one full turn scrolls exactly one map: the
    loop matches turn for turn.
    Returns (tiles, map words as LE bytes, char count)."""
    W, H = 256, CLOUD_TROWS * 8
    grid = [[0] * W for _ in range(H)]
    # (base x, flat-bottom row, puffs as (dx, cy, rx, ry) ellipses)
    clouds = [
        (16, 22, [(-16, 17, 15, 7), (0, 12, 14, 9), (15, 16, 12, 6)]),
        (104, 18, [(-18, 13, 13, 6), (-3, 9, 12, 8), (11, 12, 14, 7),
                   (24, 15, 9, 5)]),
        (204, 21, [(-15, 16, 12, 6), (0, 11, 13, 8), (14, 15, 11, 6)]),
    ]
    for bx, base, puffs in clouds:
        for y in range(H):
            if y > base:
                continue # flat cumulus bottom
            for dx in range(-40, 41):
                f = 0.0
                for px, cy, rx, ry in puffs:
                    d = math.hypot((dx - px) / rx, (y - cy) / ry)
                    f = max(f, 1.0 - d)
                if f <= 0.02:
                    continue
                # solid body, hash-dithered fringe
                if f > 0.30 or _hash01(bx + dx + 97 * y, y * 31 + dx) < f * 2.4:
                    grid[y][(bx + dx) & 255] = 2 if y >= base - 2 else 1
    # 2bpp for BG3: clouds only need transparent/white/shade. BG2 is OFF
    # LIMITS in the mode-1 band because EXTBG (always on for the sea)
    # mangles BG2's fetches outside mode 7 ON REAL HARDWARE - emulators
    # implement EXTBG only in mode 7, so they can't show it (see the
    # CRT-jank hardware gotcha in CLAUDE.md).
    raw = encode_2bpp(grid, 32, CLOUD_TROWS)
    blank = bytes(16)
    chars, index, mapw = [blank], {blank: 0}, bytearray()
    for i in range(32 * CLOUD_TROWS):
        t = raw[i * 16:i * 16 + 16]
        if t not in index:
            index[t] = len(chars)
            chars.append(t)
        # palette group 7 (CGRAM 28-31) + priority: with the band's mode
        # byte 0x09 (BG3 priority) the clouds draw above the sky gradient
        mapw += struct.pack("<H", 0x2000 | 0x1C00
                            | (CLOUD_CHAR0 + index[t]))
    assert CLOUD_CHAR0 + len(chars) <= 512, \
        "cloud tiles run past the end of VRAM ({0} chars from id {1})" \
        .format(len(chars), CLOUD_CHAR0)
    return b"".join(chars), bytes(mapw), len(chars)


# ---- HUD font: gradient text without an HDMA channel -------------------------
# All 8 HDMA channels are spoken for, so the "text colour changes every
# scanline" effect is baked into the art instead: every glyph pixel ROW
# carries its own palette index (1..8), and three static CGRAM ramps (rows
# 4/5/6, CGRAM 64-111 - the free BG reserve) do the per-scanline colouring.
# Order must match hudIdx() in game/src/ui.c.
HUD_GLYPHS = "0123456789'\"/!ADEFGHIKLMNOPRSTW*."  # * / . = power pips
# BG3 cloud chars start right after the HUD font (0x4800 + glyphs*3 4bpp
# chars), as 2bpp ids from the 0x4000 BG3 char base
CLOUD_CHAR0 = (0x800 + len(HUD_GLYPHS) * 3 * 16) // 8
HUD_CHAR0 = 128  # VRAM 0x4800 from the 0x4000 BG1 base (map ids are 10-bit)
HUD_FONT = {  # 5x7, one int per row, bit 4 = leftmost pixel
    '0': (0x0E, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0E),
    '1': (0x04, 0x0C, 0x04, 0x04, 0x04, 0x04, 0x0E),
    '2': (0x0E, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1F),
    '3': (0x1F, 0x02, 0x04, 0x02, 0x01, 0x11, 0x0E),
    '4': (0x02, 0x06, 0x0A, 0x12, 0x1F, 0x02, 0x02),
    '5': (0x1F, 0x10, 0x1E, 0x01, 0x01, 0x11, 0x0E),
    '6': (0x06, 0x08, 0x10, 0x1E, 0x11, 0x11, 0x0E),
    '7': (0x1F, 0x01, 0x02, 0x04, 0x08, 0x08, 0x08),
    '8': (0x0E, 0x11, 0x11, 0x0E, 0x11, 0x11, 0x0E),
    '9': (0x0E, 0x11, 0x11, 0x0F, 0x01, 0x02, 0x0C),
    "'": (0x04, 0x04, 0x08, 0x00, 0x00, 0x00, 0x00),
    '"': (0x0A, 0x0A, 0x14, 0x00, 0x00, 0x00, 0x00),
    '/': (0x01, 0x02, 0x04, 0x08, 0x10, 0x00, 0x00),
    '!': (0x04, 0x04, 0x04, 0x04, 0x04, 0x00, 0x04),
    'A': (0x0E, 0x11, 0x11, 0x1F, 0x11, 0x11, 0x11),
    'D': (0x1E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1E),
    'E': (0x1F, 0x10, 0x1E, 0x10, 0x10, 0x10, 0x1F),
    'F': (0x1F, 0x10, 0x1E, 0x10, 0x10, 0x10, 0x10),
    'G': (0x0E, 0x11, 0x10, 0x17, 0x11, 0x11, 0x0F),
    'H': (0x11, 0x11, 0x1F, 0x11, 0x11, 0x11, 0x11),
    'I': (0x0E, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0E),
    'K': (0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11),
    'L': (0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1F),
    'M': (0x11, 0x1B, 0x15, 0x15, 0x11, 0x11, 0x11),
    'N': (0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11),
    'O': (0x0E, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0E),
    'P': (0x1E, 0x11, 0x11, 0x1E, 0x10, 0x10, 0x10),
    'R': (0x1E, 0x11, 0x11, 0x1E, 0x14, 0x12, 0x11),
    'S': (0x0F, 0x10, 0x10, 0x0E, 0x01, 0x01, 0x1E),
    'T': (0x1F, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04),
    'W': (0x11, 0x11, 0x11, 0x15, 0x15, 0x1B, 0x11),
    # power pips: 7-bit-wide soft rectangles - circles and diamonds both
    # read as zeros at this size
    '*': (0x3E, 0x7F, 0x7F, 0x7F, 0x7F, 0x3E, 0x00),  # filled
    '.': (0x3E, 0x41, 0x41, 0x41, 0x41, 0x3E, 0x00),  # hollow
}
HUD_RAMPS = (((252, 216, 32), (56, 200, 88)),   # row 4 titles: yellow -> green
             ((56, 200, 88), (252, 216, 32)),   # row 5 value top: green -> yel
             ((252, 216, 32), (228, 40, 32)))   # row 6 value bottom: yel -> red


def build_hud_font():
    """Three blocks of len(HUD_GLYPHS) chars each: single-height glyphs,
    then double-height TOPS, then double-height BOTTOMS (so bottom char =
    top char + count). Double height = each source row twice; the colour
    index still advances every SCANLINE, so the ramps keep per-line
    resolution through the doubled art. Returns (tiles, CGRAM 64-111)."""
    n = len(HUD_GLYPHS)
    grid = [[0] * 8 for _ in range(n * 8 * 3)]
    for gi, ch in enumerate(HUD_GLYPHS):
        rows = HUD_FONT[ch]
        for r in range(7):
            for x in range(7):  # rows are 7-bit (bit 6 leftmost); the 5-bit
                if not rows[r] & (0x40 >> x):  # letters just sit 1px right
                    continue
                grid[gi * 8 + r][x + 1] = 1 + r  # single height
                for s in (2 * r, 2 * r + 1):     # doubled: tops then bottoms
                    blk = (1 + s // 8) * n * 8
                    grid[blk + gi * 8 + (s & 7)][x + 1] = 1 + (s & 7)
    pal = bytearray()
    for ra, rb in HUD_RAMPS:
        row = [(0, 0, 0)] + [tuple(ra[i] + (rb[i] - ra[i]) * t // 7
                                   for i in range(3)) for t in range(8)]
        row += [(0, 0, 0)] * 7
        for r, g, b in row:
            pal += struct.pack("<H", ((b >> 3) << 10) | ((g >> 3) << 5)
                               | (r >> 3))
    return encode_4bpp(grid, 1, n * 3), bytes(pal)


# a = d * tan(fovH/2)/2, as the EXACT integer formula the console uses to
# synthesise a from d at course load (waveRawLoad in camera.asm): only d is
# stored in ROM. 18919 = round(tan(30 deg)/2 * 65536) = 74*256 - 25, the
# two positive-signed-byte factors the PPU 16x8 multiplier needs. The
# camera (fovH included) is global across courses BY DESIGN - the asserts
# below fail the bake if that ever drifts, because camera.asm hardcodes
# the 74/25 decomposition.
A_MUL = 18919


def a_from_d(d):
    return max(1, (d * A_MUL + 32768) >> 16)


def phase_raw(phi):
    """Raw per-scanline arrays for the runtime camera-table builder:
    d = hit distance (texels, 16-bit), a = horizontal scale (8.8),
    derived from d by the shared integer formula (a_from_d).
    Sky lines carry the far-cap values; the TM channel hides them."""
    assert round(TAN_HALF_H / 2.0 * 65536) == A_MUL == 74 * 256 - 25, \
        "fovH changed: update A_MUL here AND the 74/25 pair in camera.asm"
    n_sky, sea_x = raycast_phase(phi)
    xs = [P["maxX"]] * n_sky + sea_x
    d_words, a_words = [], []
    for x in xs:
        assert x < 4096, "maxX must stay below 4096 for the 16x8 multiplier"
        d = round(x) & 0xFFFF
        d_words.append(d)
        a_words.append(a_from_d(d))
    return d_words, a_words


# ---- jet ski sprite ---------------------------------------------------------
# 32x64 rear view, authored in Photoshop: assets/rider_stand.png and
# assets/rider_turn.png (indexed PNGs, true-alpha background), with
# assets/rider.act as the ROLE CONTRACT - the ACT's entry ORDER defines
# which colour is which role, and PNG pixels map to engine palette slots
# by exact RGB match (any unknown colour fails the bake). The resting
# waterline is row 58; rows 59-63 are the submerged hull.

RIDER_ROLES = ["black", "white", "hull light", "hull dark",
               "skin", "skin shadow", "clothing A", "clothing A shadow",
               "clothing B", "clothing B shadow", "jetski", "jetski shadow"]


def load_act(path):
    """Photoshop .act: 256 RGB triplets + optional (count, transparent)
    footer. Entry ORDER is the role contract (see RIDER_ROLES)."""
    raw = open(path, "rb").read()
    n = struct.unpack(">H", raw[768:770])[0] if len(raw) >= 772 else 256
    return [tuple(raw[i * 3:i * 3 + 3]) for i in range(n)]


def load_rider(name, act):
    """32x64 engine-slot grid from an indexed PNG: alpha-0 entries -> slot
    0, paint colours -> ACT position + 1. Unknown colours fail the bake."""
    pat, plte, trns = decode_png(os.path.join(ASSETS, name))
    assert len(pat) == 64 and len(pat[0]) == 32, name + " must be 32x64"
    remap = {}
    for i in {c for row in pat for c in row}:
        if (trns[i] if i < len(trns) else 255) == 0:
            remap[i] = 0
        else:
            assert plte[i] in act[:12],                 "{0}: colour #{1:02X}{2:02X}{3:02X} is not in rider.act"                 .format(name, *plte[i])
            remap[i] = act.index(plte[i]) + 1
    return [[remap[c] for c in row] for row in pat]


# engine OBJ palette 0 = the PLAYER (rider 1): slots follow RIDER_ROLES
# (+1 for the transparent 0). Art was authored with a green jetski accent
# for contrast; the PLAYER'S accent is warm yellow by design - the ACT
# colours are AUTHORING colours, the palettes below are what displays.
SKI_PALETTE = [
    (0, 0, 0),        # 0 transparent
    (0, 0, 0),        # 1 black / outline
    (255, 255, 255),  # 2 white
    (61, 57, 222),    # 3 hull light: #3D39DE deep blue-indigo
    (27, 16, 132),    # 4 hull dark:  #1B1084 (authored as grey)
    (241, 175, 105),  # 5 skin
    (174, 107, 35),   # 6 skin shadow
    (42, 110, 212),   # 7 clothing A (blue)
    (33, 38, 143),    # 8 clothing A shadow
    (191, 60, 60),    # 9 clothing B (red)
    (128, 23, 23),    # 10 clothing B shadow
    (244, 196, 40),   # 11 jetski accent: warm yellow
    (176, 130, 20),   # 12 jetski accent shadow
    (171, 171, 171),  # 13 spray shade: FIXED neutral (the spray must not
                      #    tint with the player's hull pair)
] + [(0, 0, 0)] * 2   # 14-15 spare

SKI_WATERLINE_ROW = 58  # master-art row at the surface when at rest (the
                        # BOTTOM sprite of the stacked pair sees row 26,
                        # so every runtime waterline constant is unchanged)

# NPC rider recolours (OBJ palettes 1-3; tiles shared with the player).
# Overrides: 3/4 hull, 5/6 skin, 7/8 A, 9/10 B, 11/12 jetski accent.
NPC_PALETTES = [
    # rider 2: pale skin, cool white A, pink B, cool lemon-yellow ski,
    # saturated pink hull (deeper than the clothing pink)
    {3: (232, 96, 176), 4: (150, 44, 110),
     5: (244, 205, 170), 6: (192, 146, 112),
     7: (235, 240, 248), 8: (156, 172, 196),
     9: (240, 138, 178), 10: (176, 80, 122),
     11: (230, 228, 92), 12: (162, 158, 42)},
    # rider 3: indigo skin (SF2 Dhalsim-alt), VIBRANT indigo A,
    # desaturated magenta B, teal ski, saturated blue hull
    {3: (56, 110, 232), 4: (26, 56, 148),
     5: (122, 106, 170), 6: (78, 64, 116),
     7: (92, 70, 235), 8: (52, 36, 160),
     9: (198, 128, 182), 10: (134, 76, 122),
     11: (56, 192, 186), 12: (26, 126, 122)},
    # rider 4: dark brown skin, chartreuse A, pine B, purple ski,
    # saturated indigo hull
    {3: (104, 76, 220), 4: (54, 36, 134),
     5: (126, 78, 44), 6: (82, 46, 22),
     7: (170, 220, 44), 8: (108, 148, 18),
     9: (36, 112, 74), 10: (18, 68, 44),
     11: (156, 76, 218), 12: (98, 38, 150)},
]

# the start-tree lamps get their OWN OBJ palette (4, CGRAM 192-207): they
# used to squat in the ski palette, whose slots are all rider roles now.
# Indices match the lamp_cell art (1 outline, 5/7 dark lamp, 8 highlight,
# 10/11 red, 12/13 green).
LAMP_PALETTE = [(0, 0, 0)] * 16
LAMP_PALETTE[1] = (16, 16, 24)
LAMP_PALETTE[5] = (36, 40, 54)
LAMP_PALETTE[7] = (122, 126, 142)
LAMP_PALETTE[8] = (240, 244, 248)
LAMP_PALETTE[10] = (220, 60, 50)
LAMP_PALETTE[11] = (160, 40, 36)
LAMP_PALETTE[12] = (64, 208, 80)
LAMP_PALETTE[13] = (34, 130, 48)


def ski_frame(lean):
    """32x64 engine-slot grid. lean 0 = rider_stand; lean 1 = the hand-
    drawn turn frame, MIRRORED so it leans right (the runtime flip
    convention: pressing LEFT sets hflip, which un-mirrors it)."""
    act = load_act(os.path.join(ASSETS, "rider.act"))
    g = load_rider("rider_turn.png" if lean else "rider_stand.png", act)
    return [row[::-1] for row in g] if lean else g


def ski_scaled(w):
    """w x 2w grid: the rear-view ski for NPC racers (the racers are twice
    as tall as wide). Cropped at the waterline (rows 0..SKI_WATERLINE_ROW)
    so, like the flat-bottomed buoys, nothing hangs below the surface row
    it gets anchored to; scaled with a majority-vote box filter (keeps the
    outline/hull blobs readable at 8px); bottom-anchored in the grid."""
    src = ski_frame(0)
    sh = SKI_WATERLINE_ROW + 1
    slot = 2 * w
    h = max(1, round(sh * slot / 64.0))
    g = [[0] * w for _ in range(slot)]
    for dy in range(h):
        y0 = dy * sh // h
        y1 = max(y0 + 1, (dy + 1) * sh // h)
        for dx in range(w):
            x0 = dx * 32 // w
            x1 = max(x0 + 1, (dx + 1) * 32 // w)
            counts = {}
            area = 0
            for sy in range(y0, y1):
                for sx in range(x0, x1):
                    area += 1
                    c = src[sy][sx]
                    if c:
                        counts[c] = counts.get(c, 0) + 1
            if counts and sum(counts.values()) * 2 >= area:
                g[slot - h + dy][dx] = max(counts.items(),
                                           key=lambda kv: kv[1])[0]
    return g


def encode_2bpp(grid, w_tiles, h_tiles):
    """SNES 2bpp planar tiles (16 bytes each), row-major over the sheet."""
    out = bytearray()
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            for py in range(8):
                b0 = b1 = 0
                for px in range(8):
                    c = grid[ty * 8 + py][tx * 8 + px]
                    bit = 0x80 >> px
                    if c & 1:
                        b0 |= bit
                    if c & 2:
                        b1 |= bit
                out += bytes((b0, b1))
    return bytes(out)


def encode_4bpp(grid, w_tiles, h_tiles):
    """SNES 4bpp planar tiles, row-major over the sheet."""
    out = bytearray()
    for ty in range(h_tiles):
        for tx in range(w_tiles):
            planes = bytearray(32)
            for py in range(8):
                b0 = b1 = b2 = b3 = 0
                for px in range(8):
                    c = grid[ty * 8 + py][tx * 8 + px]
                    bit = 0x80 >> px
                    if c & 1:
                        b0 |= bit
                    if c & 2:
                        b1 |= bit
                    if c & 4:
                        b2 |= bit
                    if c & 8:
                        b3 |= bit
                planes[py * 2] = b0
                planes[py * 2 + 1] = b1
                planes[16 + py * 2] = b2
                planes[16 + py * 2 + 1] = b3
            out += planes
    return bytes(out)


LETTER_L = ["10000","10000","10000","10000","10000","10000","11111"]
LETTER_R = ["11110","10001","10001","11110","10100","10010","10001"]


MINI_L = ["100", "100", "100", "100", "111"]
MINI_R = ["110", "101", "110", "101", "101"]


# buoys draw with their OWN OBJ palette (5, CGRAM 208-223) so rider
# recolours and the per-course ambient never argue over them; the slot
# numbers still mirror the player's red / warm-yellow pairs (the art was
# authored against palette 0 and the indices are baked into the tiles)
BUOY_PAL = 5
BUOY_PALETTE = [(0, 0, 0)] * 16
for _i in (1, 2, 9, 10, 11, 12):
    BUOY_PALETTE[_i] = SKI_PALETTE[_i]


def buoy_grid(size, right):
    """Flat-bottomed circle (stable silhouette across scales), letter always."""
    g = [[0] * size for _ in range(size)]
    # BUOY_PALETTE slots: 9/10 red, 11/12 yellow, 1 outline, 2 white
    body, shade = (9, 10) if right else (11, 12)
    letter_col = 2 if right else 1
    c = (size - 1) / 2.0
    r = c - 0.2
    cut = max(1, size // 6)
    flat = size - 1 - cut
    import math as _m
    widths = []
    for y in range(size):
        dy = y - c
        w2 = r * r - dy * dy
        widths.append(_m.sqrt(w2) if w2 > 0 else -1)
    for y in range(size):
        yy = min(y, flat)          # rows below 'flat' reuse the flat row width
        w = widths[yy]
        if w < 0:
            continue
        if y > flat + cut:
            continue
        x0, x1 = int(_m.ceil(c - w)), int(_m.floor(c + w))
        for x in range(x0, x1 + 1):
            edge = (x == x0 or x == x1 or y == flat + cut or
                    (yy == y and y > 0 and widths[y - 1] < 0))
            if edge:
                g[y][x] = 1
            else:
                g[y][x] = shade if (x - c) + (y - c) > r * 0.5 else body
    letter = (LETTER_R if right else LETTER_L) if size >= 12 else         (MINI_R if right else MINI_L)
    s = 2 if size >= 24 else 1
    lh, lw = len(letter) * s, len(letter[0]) * s
    ly0 = int(c) - lh // 2
    lx0 = int(c) - lw // 2 + 1
    for ly, rowbits in enumerate(letter):
        for lx, bit in enumerate(rowbits):
            if bit == "1":
                for dy in range(s):
                    for dx in range(s):
                        yy2, xx2 = ly0 + ly * s + dy, lx0 + lx * s + dx
                        if 0 <= yy2 < size and 0 <= xx2 < size and g[yy2][xx2]:
                            g[yy2][xx2] = letter_col
    return g


SPRAY_W, SPRAY_S = 2, 13  # white + the FIXED spray-shade grey


def _hash01(i, k):
    """Deterministic decorrelated [0,1) - a shared modulus made the droplets
    march in straight diagonal rays instead of scattering."""
    v = (i * 374761393 + k * 668265263) & 0xFFFFFFFF
    v = ((v ^ (v >> 13)) * 1274126177) & 0xFFFFFFFF
    return ((v >> 11) & 0xFFFF) / 65536.0


BAYER4 = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
SPRAY_LEVELS = 4


def spray_cell(level):
    """16x16 cell of the wake conveyor: ordered-dithered white water at one of
    SPRAY_LEVELS intensities. Each cell carries an internal vertical falloff
    (denser at the top) so the band reads as finer structure than its 16-row
    cell height, and the marginal pixels take the shade colour so the edges
    are soft rather than crawling."""
    size = 16
    g = [[0] * size for _ in range(size)]
    base = (0.20, 0.38, 0.58, 0.80)[level]
    for y in range(size):
        fall = 1.0 - 0.65 * (y / (size - 1.0))
        for x in range(size):
            d = base * fall
            # mostly hashed noise over a little Bayer: pure Bayer at a flat
            # density draws a regular screen door, which reads as a mesh
            # rather than water
            thr = 0.30 * ((BAYER4[y & 3][x & 3] + 0.5) / 16.0) \
                + 0.70 * _hash01(x + level * 61, y + level * 17)
            if thr < d:
                g[y][x] = SPRAY_W if (d - thr) > 0.20 else SPRAY_S
    return g


def lamp_cell(body, shade):
    """16x16 start-tree lamp: outlined round light, lit body + diagonal
    shade + a glass highlight."""
    g = [[0] * 16 for _ in range(16)]
    for y in range(16):
        for x in range(16):
            dx, dy = x - 7.5, y - 7.5
            d = math.hypot(dx, dy)
            if d > 7.5:
                continue
            g[y][x] = 1 if d > 6.2 else shade if dx + dy > 3.5 else body
    for y, x in ((4, 5), (4, 6), (5, 4), (5, 5)):
        g[y][x] = 8  # hull white doubles as the highlight
    return g


def build_ski_sheet():
    """128x128 sheet = OBJ name table 1: buoys at 5 sizes, the wake
    conveyor cells (row 96+) and the start-tree lamps (row 112+). The
    racers moved to the tall sheet (name table 2 at VRAM 0x7000); their
    old slots here are simply left blank.
    128px wide = 16 tiles/row, matching OAM's name-row stride exactly."""
    sheet = [[0] * 128 for _ in range(128)]

    def blitg(g, sx, sy, slot):
        size = len(g)
        oy = slot - size  # bottom row of art == bottom row of slot, always
        ox = (slot - size) // 2
        for y in range(size):
            for x in range(size):
                sheet[sy + oy + y][sx + ox + x] = g[y][x]

    def blit(size, right, sx, sy, slot):
        blitg(buoy_grid(size, right), sx, sy, slot)

    blit(32, 0, 64, 0, 32)   # name 8
    blit(32, 1, 96, 0, 32)   # name 12
    blit(24, 0, 0, 32, 32)   # name 64
    blit(24, 1, 32, 32, 32)  # name 68
    blit(16, 0, 64, 32, 16)  # name 72
    blit(16, 1, 80, 32, 16)  # name 74
    blit(12, 0, 96, 32, 16)  # name 76
    blit(12, 1, 112, 32, 16) # name 78
    blit(8, 0, 64, 48, 16)   # name 104
    blit(8, 1, 80, 48, 16)   # name 106
    # wake conveyor cells, one per intensity: names 192 / 194 / 196 / 198
    for lv in range(SPRAY_LEVELS):
        blitg(spray_cell(lv), lv * 16, 96, 16)
    # start-tree lamps: names 224 dark / 226 red / 228 green
    blitg(lamp_cell(7, 5), 0, 112, 16)
    blitg(lamp_cell(10, 11), 16, 112, 16)
    blitg(lamp_cell(12, 13), 32, 112, 16)
    tiles = encode_4bpp(sheet, 16, 16)
    return tiles, pal_bytes(SKI_PALETTE), sheet


def obj_palettes(amb, where):
    """The per-course OBJ palette block: palettes 0-3 (player + 3 NPC
    recolours, CGRAM 128-191, 128 bytes) and the buoy palette 5 (CGRAM
    208-223, 32 bytes), all under the course's ambient light. Spray rides
    in palette 0 (slots 2/13) so it dims too; lamps (4) and HUD are exempt."""
    riders = [list(SKI_PALETTE)]
    for over in NPC_PALETTES:
        riders.append([over.get(i, c) for i, c in enumerate(SKI_PALETTE)])
    block = bytearray()
    for n, cols in enumerate(riders):
        cols = [tint(c, amb) for c in cols]
        lint_pairs(cols, RIDER_PAIRS, "rider {0} palette".format(n + 1), where)
        block += pal_bytes(cols)
    buoy = [tint(c, amb) for c in BUOY_PALETTE]
    lint_pairs(buoy, ((9, 10), (11, 12)), "buoy palette", where)
    return bytes(block), pal_bytes(buoy)


def build_tall_sheet():
    """128x96 = OBJ name table 2 (VRAM 0x7000, runtime gfx = 256 + name):
    the tall racers, each drawn as TWO stacked sprites sharing one
    projection. Slots (art bottom-anchored, so the waterline sits at the
    slot's last row exactly like the buoys):
      rows 0-7 : player straight | player lean | NPC 32x64 | NPC 24x48
                 (top sprite name n, bottom n+64)
      rows 8-11: NPC 16x32 (n / n+32) | NPC 12x24 | NPC 8x16 (single)"""
    sheet = [[0] * 128 for _ in range(96)]

    def blit_tall(g, sx, sy, slot_w, slot_h):
        h, w = len(g), len(g[0])
        oy, ox = slot_h - h, (slot_w - w) // 2
        for y in range(h):
            for x in range(w):
                if g[y][x]:
                    sheet[sy + oy + y][sx + ox + x] = g[y][x]

    blit_tall(ski_frame(0), 0, 0, 32, 64)      # top 0, bottom 64
    blit_tall(ski_frame(1), 32, 0, 32, 64)     # top 4, bottom 68
    blit_tall(ski_scaled(32), 64, 0, 32, 64)   # top 8, bottom 72
    blit_tall(ski_scaled(24), 96, 0, 32, 64)   # top 12, bottom 76
    blit_tall(ski_scaled(16), 0, 64, 16, 32)   # top 128, bottom 160
    blit_tall(ski_scaled(12), 16, 64, 16, 32)  # top 130, bottom 162
    blit_tall(ski_scaled(8), 32, 64, 16, 16)   # single 132
    return encode_4bpp(sheet, 16, 12), sheet


# ---- sea texture ----------------------------------------------------------

def procedural_pattern():
    """Default 64x64 periodic pattern. Indices:
    0 sky (unused in art), 1-3 deep blues (rotation stripes),
    4 mid, 5 light, 6 crest, 7 foam."""
    size = 64
    tau = 2 * math.pi
    pat = [[0] * size for _ in range(size)]
    for y in range(size):
        for x in range(size):
            v = (math.sin(tau * x / 64 + 1.8 * math.sin(tau * y / 32))
                 + 0.7 * math.sin(tau * (x + y) / 32)
                 + 0.5 * math.sin(tau * y / 64 + 1.2 * math.sin(tau * x / 16)))
            if v < -0.9:
                # deep water: directional stripes across 3 rotating indices
                s = math.sin(tau * (x + 2 * y) / 32)
                c = 1 if s < -0.33 else (2 if s < 0.33 else 3)
            elif v < 0.3:
                c = 4
            elif v < 1.3:
                c = 5
            else:
                c = 6
            if v > 1.9 and (x * 7 + y * 13) % 5 == 0:
                c = 7
            pat[y][x] = c

    palette = [(0, 0, 0)] * 256
    palette[0] = SKY_RGB
    palette[1] = (14, 44, 96)     # deep stripe A
    palette[2] = (8, 32, 80)      # deep stripe B
    palette[3] = (20, 56, 112)    # deep stripe C
    palette[4] = (28, 84, 156)    # mid
    palette[5] = (56, 124, 196)   # light
    palette[6] = (112, 172, 224)  # crest
    palette[7] = (236, 250, 255)  # foam
    return pat, palette


def decode_png(path):
    """Minimal indexed-PNG reader (bit depth 8, colour type 3, filters 0-4)."""
    with open(path, "rb") as f:
        data = f.read()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "not a PNG"
    pos, w, h, plte, idat, trns = 8, 0, 0, [], b"", []
    while pos < len(data):
        ln, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype == 3, "need 8-bit indexed PNG"
        elif tag == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
        elif tag == b"tRNS":
            trns = list(body)
        elif tag == b"IDAT":
            idat += body
    raw = zlib.decompress(idat)
    rows, prev = [], bytearray(w)
    for y in range(h):
        flt = raw[y * (w + 1)]
        line = bytearray(raw[y * (w + 1) + 1:(y + 1) * (w + 1)])
        for x in range(w):
            a = line[x - 1] if x else 0
            b = prev[x]
            cc = prev[x - 1] if x else 0
            if flt == 1:
                line[x] = (line[x] + a) & 255
            elif flt == 2:
                line[x] = (line[x] + b) & 255
            elif flt == 3:
                line[x] = (line[x] + ((a + b) >> 1)) & 255
            elif flt == 4:
                pp = a + b - cc
                pa, pb, pc = abs(pp - a), abs(pp - b), abs(pp - cc)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else cc)
                line[x] = (line[x] + pr) & 255
        rows.append(bytes(line))
        prev = line
    pal = [(0, 0, 0)] * 256
    for i, c in enumerate(plte[:256]):
        pal[i] = c
    return [list(r) for r in rows], pal, trns


def load_pattern(cdir=None):
    # per-course texture wins; then the shared assets/sea_pattern.png,
    # then the procedural fallback
    src = None
    if cdir and os.path.exists(os.path.join(cdir, "sea_pattern.png")):
        src = os.path.join(cdir, "sea_pattern.png")
    elif os.path.exists(os.path.join(ASSETS, "sea_pattern.png")):
        src = os.path.join(ASSETS, "sea_pattern.png")
    if src:
        pat, palette, _ = decode_png(src)
        size = len(pat)
        assert size == len(pat[0]), "pattern must be square"
        assert 1024 % size == 0 and size % 8 == 0, \
            "pattern size must divide 1024 and be a multiple of 8"
        print("pattern {0} ({1}x{1})".format(src, size))
        return pat, palette
    return procedural_pattern()


# ---- course: sand islands, shorelines, rope float-lines ----------------------
# palette indices 8-13 (the course block in the colour map)
SAND, SAND_SH, FOAM, WET_SAND, FLOAT_A, SHAL_BLUE, CALM, TEAL =     8, 9, 10, 11, 12, 13, 14, 15
TEAL_SAND = 50    # band-1 teal, over the wet sand (CGRAM 50: free since
                  # the cloud shade moved to the BG3 palette group)
CHECK_DARK = 48   # start/finish checker black - NOT 32-47, which is the
CHECK_WHITE = 49  # mode-1 sky palette row loaded over CGRAM at boot
COURSE_COLORS = {
    SAND: (232, 214, 164), SAND_SH: (212, 190, 142),
    FOAM: (172, 214, 246), WET_SAND: (186, 164, 118),  # foam = lattice blue
    FLOAT_A: (216, 44, 214),  # magenta: not confusable with R buoys
    CALM: (22, 62, 122),   # flat wake band under ropes (non-rotating)
    TEAL: (60, 182, 214),       # clear shallows, water side: toward blue
    TEAL_SAND: (128, 196, 168), # clear shallows over sand: toward wet sand
    # SHAL_BLUE is set at bake time to the lightest deep-water rotation
    # colour (fixed copy, so the shallows don't flow)
}
CHECK_COLORS = {CHECK_DARK: (16, 16, 20), CHECK_WHITE: (250, 250, 250)}
# the sand distance fade's end colours (not CGRAM entries: the HDMA table
# sweeps entry 8 from `sand_far` at the horizon through SAND to `sand_deep`
# at the ski)
SAND_FAR_DEFAULT = (248, 244, 228)   # pale horizon, still warm-tinted
SAND_DEEP_DEFAULT = (212, 184, 120)  # darker + more saturated at the ski

# ---- per-course palette + ambient light (course.json "palette"/"ambient") -
# Palette ROLES are fixed (the bake paints by index; EXTBG bits, glow
# exemption and the sand-fade HDMA all key on indices) - a course only
# changes the RGB behind a role. Names are what the course painter shows.
PALETTE_ROLES = {
    "sand": SAND, "sand_shade": SAND_SH, "foam": FOAM, "wet_sand": WET_SAND,
    "float": FLOAT_A, "calm": CALM, "teal": TEAL, "teal_sand": TEAL_SAND,
    "check_dark": CHECK_DARK, "check_white": CHECK_WHITE,
}
FADE_ROLES = ("sand_far", "sand_deep")
SKY_ROLES = ("sky",)  # the zenith colour: backdrop 0 + the band's 16 anchors
# shade pairs that must survive the ambient multiply + 5-bit quantisation
# (a dark ambient can fold light and dark into one CGRAM value and flatten
# the art); indices are CGRAM entries
COURSE_PAIRS = ((SAND, WET_SAND), (SAND, SAND_SH), (TEAL, TEAL_SAND),
                (FOAM, SHAL_BLUE), (CHECK_DARK, CHECK_WHITE))
RIDER_PAIRS = ((3, 4), (5, 6), (7, 8), (9, 10), (11, 12))


def parse_rgb(v, where):
    """'#rrggbb' or [r, g, b] -> (r, g, b)."""
    if isinstance(v, str):
        s = v.lstrip("#")
        assert len(s) == 6, where + ": colour must be #rrggbb"
        return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))
    assert len(v) == 3, where + ": colour must be #rrggbb or [r,g,b]"
    return tuple(int(x) for x in v)


def load_style(cj, where):
    """course.json -> (index -> rgb overrides, fade endpoints, ambient).
    Ambient is an RGB multiplier, 255 = neutral; it is applied AT BAKE to
    every in-world palette (course + water, sand fade, all rider OBJ
    palettes, buoys, spray) - zero runtime cost. HUD text and the start
    lamps are exempt (readability / self-lit), and so is the sky band."""
    over = {}
    fade = {"sand_far": SAND_FAR_DEFAULT, "sand_deep": SAND_DEEP_DEFAULT}
    sky = SKY_RGB
    for name, v in (cj.get("palette") or {}).items():
        if name in PALETTE_ROLES:
            over[PALETTE_ROLES[name]] = parse_rgb(v, where + " palette." + name)
        elif name in FADE_ROLES:
            fade[name] = parse_rgb(v, where + " palette." + name)
        elif name in SKY_ROLES:
            sky = parse_rgb(v, where + " palette." + name)
        else:
            raise AssertionError(where + ": unknown palette role '" + name
                                 + "' (roles: " + ", ".join(
                                     sorted(list(PALETTE_ROLES) + list(FADE_ROLES)
                                            + list(SKY_ROLES)))
                                 + ")")
    amb = parse_rgb(cj.get("ambient", (255, 255, 255)), where + " ambient")
    return over, fade, amb, sky


def tint(rgb, amb):
    """Ambient multiply, 255 = identity (exact: the default course bakes
    byte-identical to the pre-ambient build)."""
    return tuple(min(255, (c * a + 127) // 255) for c, a in zip(rgb, amb))


def rgb15(rgb):
    r, g, b = rgb
    return ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)


def pal_bytes(cols):
    out = bytearray()
    for c in cols:
        out += struct.pack("<H", rgb15(c))
    return bytes(out)


def lint_pairs(cols, pairs, what, where):
    """Warn when a shade pair quantises to one CGRAM value - the art loses
    its shading silently otherwise."""
    for i, j in pairs:
        if rgb15(cols[i]) == rgb15(cols[j]):
            print("WARNING: {0}: {1} slots {2}/{3} collapse to one 15-bit "
                  "colour {4} under this ambient".format(where, what, i, j,
                                                          cols[i]))


def load_course(path):
    if not os.path.exists(path):
        return None
    with open(path) as f:
        c = json.load(f)
    zones = c["zones"]
    assert len(zones) == 128 and all(len(r) == 128 for r in zones), \
        "course zones must be 128x128"
    # MIRROR FIX: the Mode 7 view transform is left-handed relative to the
    # painter's map (facing +Y, screen-right samples texture +X - so the
    # rendered world is the painter's mirror image). Flip ALL course data
    # in X here, once, so the in-game world matches the painter exactly.
    zones = [r[::-1] for r in zones]
    ropes = [[[1023 - x, y] for x, y in rope] for rope in c.get("ropes", [])]
    buoys = [[1023 - x, y, side] for x, y, side in c.get("buoys", [])]
    rpath = [[1023 - x, y] for x, y in c.get("path", [])]
    return zones, ropes, buoys, rpath


def order_gates(course):
    """Buoys sorted into racing-line order, each with the line direction at
    its nearest point — the runtime power-gate data. A gate is judged when
    the player crosses the buoy's perpendicular: sign(cross(dir, player -
    buoy)) picks the side, and the racing line itself passes every buoy at
    cross > 0 for L, < 0 for R (verified against the authored course), so
    that IS the correct-side convention. Also lints: if the racing line
    passes a buoy on the wrong side of its own label, the course is asking
    the player to leave the line — almost certainly a painting mistake."""
    buoys, rpath = course[2], course[3]
    if not buoys or len(rpath) < 2:
        return []
    n = len(rpath)
    gates = []
    for i, (bx, by, side) in enumerate(buoys):
        best = None
        for k in range(n):
            ax, ay = rpath[k]
            dx, dy = rpath[(k + 1) % n][0] - ax, rpath[(k + 1) % n][1] - ay
            L2 = dx * dx + dy * dy
            t = 0.0 if L2 == 0 else min(1.0, max(0.0, (
                (bx - ax) * dx + (by - ay) * dy) / L2))
            px, py = ax + t * dx, ay + t * dy
            d2 = (bx - px) ** 2 + (by - py) ** 2
            if best is None or d2 < best[0]:
                best = (d2, k, t, dx, dy, px, py)
        d2, k, t, dx, dy, px, py = best
        L = math.hypot(dx, dy) or 1.0
        nx, ny = round(dx / L * 64), round(dy / L * 64)
        line_side = dx * (py - by) - dy * (px - bx)  # cross(dir, line - buoy)
        want_left = side != "R"
        if (line_side > 0) != want_left:
            print("WARNING: buoy {0} is labelled {1} but the racing line "
                  "passes it on the other side".format(i, side))
        gates.append((k + t, bx, by, 1 if want_left else 0, nx, ny, k))
    gates.sort()
    return [g[1:] for g in gates]


def compose_canvas(pat, course):
    """Full 1024x1024 index canvas: tiled water pattern, then sand zones,
    auto-shore foam (on the sand side), and rope float-lines.
    Also returns the 128x128 collision byte-map (0 water, 1 sand, 2 rope)."""
    size = len(pat)
    coll = bytearray(128 * 128)
    canvas = [bytearray(1024) for _ in range(1024)]
    for y in range(1024):
        row = canvas[y]
        prow = pat[y % size]
        for x in range(1024):
            row[x] = prow[x % size]
    if not course:
        return canvas, coll

    zones, ropes, buoys = course[:3]

    def water(cy, cx):
        return zones[cy % 128][cx % 128] == "w"

    # surf wings: two water-cell bands out from every shore, painted with
    # strictly 8-periodic patterns so each band costs ~one unique tile
    # (band 2 has one variant per shore direction)
    dist = [[9] * 128 for _ in range(128)]
    frontier = []
    for cy in range(128):
        for cx in range(128):
            if zones[cy][cx] == "s":
                dist[cy][cx] = 0
                frontier.append((cy, cx))
    for step in (1, 2):
        nxt = []
        for cy, cx in frontier:
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = (cy + dy) & 127, (cx + dx) & 127
                    if dist[ny][nx] == 9 and zones[ny][nx] == "w":
                        dist[ny][nx] = step
                        nxt.append((ny, nx))
        frontier = nxt
    # band 1: dense foam over plain wet sand.
    # band 2: a shore-to-sea gradient PER CELL, oriented by where the
    # shore is: sandy seafoam teal on the land edge dithering into the
    # bluer water teal, then out through SHAL_BLUE into noise drawn from
    # the open sea's own colour balance
    # (pattern indices 1-5 - the rotating stripes carry a little of the
    # sea's motion right up to the shallows). It can't tile perfectly
    # with the open sea, but it shares its palette and texture. All
    # patterns stay 8-periodic per direction variant: ~5 unique tiles.
    pool = [c for prow in pat for c in prow if 1 <= c <= 5]
    for cy in range(128):
        for cx in range(128):
            d = dist[cy][cx]
            if d < 1 or d > 2:
                continue
            dn = dist[(cy - 1) & 127][cx] <= 1
            ds = dist[(cy + 1) & 127][cx] <= 1
            dw = dist[cy][(cx - 1) & 127] <= 1
            de = dist[cy][(cx + 1) & 127] <= 1
            for py in range(8):
                y = cy * 8 + py
                for px in range(8):
                    x = cx * 8 + px
                    n = ((x & 7) * 13 + (y & 7) * 29 + ((x & 7) * (y & 7))) % 17
                    h = _hash01((x & 7) * 7 + 3, (y & 7) * 11 + 5)
                    if d == 1:
                        if n < 12:
                            c = FOAM
                        else:
                            c = WET_SAND
                    else:
                        # s: pixel rows from the shore side (0) to sea (7)
                        if dn:
                            s = py
                        elif ds:
                            s = 7 - py
                        elif dw:
                            s = px
                        elif de:
                            s = 7 - px
                        else:
                            s = 4  # diagonal-only corner: mid-blend
                        if s <= 2:
                            # land edge: the sandy seafoam teal, dithering
                            # into the bluer water teal across three rows
                            if s <= 1 and h < 0.25:
                                c = FOAM
                            elif h < (5 - 2 * s) * 0.33:  # 1.0 / .99 / .33
                                c = TEAL_SAND
                            else:
                                c = TEAL
                        elif s <= 4:
                            c = TEAL if h < (5 - s) * 0.35 else SHAL_BLUE
                        elif h < (7 - s) * 0.1:
                            c = TEAL
                        else:
                            c = pool[int(_hash01((x & 7) + 51,
                                                 (y & 7) + 77) * len(pool))]
                    canvas[y][x] = c

    # sand: flat plain dry sand everywhere; coastline cells (4-adjacent to
    # water) add the foam fringe on their water edges. Wet sand appears
    # ONLY in the water-side surf band (foam over wet sand)
    for cy in range(128):
        zrow = zones[cy]
        for cx in range(128):
            if zrow[cx] != "s":
                continue
            coll[cy * 128 + cx] = 1
            n, s_, w, e = water(cy - 1, cx), water(cy + 1, cx), \
                water(cy, cx - 1), water(cy, cx + 1)
            if n or s_ or w or e:
                for py in range(8):
                    y = cy * 8 + py
                    for px in range(8):
                        d = 9
                        if n:
                            d = min(d, py)
                        if s_:
                            d = min(d, 7 - py)
                        if w:
                            d = min(d, px)
                        if e:
                            d = min(d, 7 - px)
                        canvas[y][cx * 8 + px] = FOAM if d <= 1 else SAND
            else:
                for py in range(8):
                    y = cy * 8 + py
                    for px in range(8):
                        canvas[y][cx * 8 + px] = SAND

    for bx, by, _side in buoys:
        cy, cx = (by >> 3) & 127, (bx >> 3) & 127
        coll[cy * 128 + cx] = 3
        for py in range(8):
            for px in range(8):
                d2 = (px * 2 - 7) ** 2 + (py * 2 - 7) ** 2
                canvas[cy * 8 + py][cx * 8 + px] =                     FOAM if 20 <= d2 <= 40 else CALM

    # Ropes are CELL-CANONICAL to keep the tile budget flat: each crossed
    # 8x8 cell gets a flat calm-water background (the rope's wake) and one
    # of 8 canonical centre-lines matching the local direction, so a rope
    # costs ~24 unique tiles no matter how long it is.
    octants = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]
    for rope in ropes:
        cells = []  # (cy, cx, octant)
        for i in range(len(rope) - 1):
            x0, y0 = rope[i]
            x1, y1 = rope[i + 1]
            steps = max(abs(x1 - x0), abs(y1 - y0), 1)
            dx, dy = x1 - x0, y1 - y0
            ang = math.atan2(dy, dx)
            oct_i = round(ang / (math.pi / 4)) % 8
            for t in range(steps + 1):
                cx = (round(x0 + dx * t / steps) >> 3) & 127
                cy = (round(y0 + dy * t / steps) >> 3) & 127
                if not cells or cells[-1][:2] != (cy, cx):
                    cells.append((cy, cx, oct_i))
        for i, (cy, cx, oct_i) in enumerate(cells):
            coll[cy * 128 + cx] = 2
            ox, oy = octants[oct_i]
            for py in range(8):
                for px in range(8):
                    # calm wake with a breath of open-sea noise (the same
                    # pool as the shallows' sea side, rotating stripes
                    # included) so the band isn't a dead flat ribbon.
                    # Cell-local coords keep the tiles canonical.
                    canvas[cy * 8 + py][cx * 8 + px] = \
                        pool[int(_hash01(px + 13, py + 44) * len(pool))] \
                        if _hash01(px * 5 + 21, py * 3 + 66) < 0.30 else CALM
            # canonical rope line through the cell centre
            for s in range(-4, 5):
                x = (cx * 8 + 3 + ox * s) & 1023
                y = (cy * 8 + 3 + oy * s) & 1023
                if (x >> 3) == cx and (y >> 3) == cy:
                    canvas[y][x] = WET_SAND
            # a red float on every cell: reads as a bead chain, and the
            # blob covering the centre makes corner cells identical too
            for fy in range(-2, 3):
                for fx in range(-2, 3):
                    if fx * fx + fy * fy <= 4:
                        canvas[cy * 8 + 3 + fy][cx * 8 + 3 + fx] = FLOAT_A

    # Start/finish line: a checkered strip across the course at path[0],
    # perpendicular to the opening racing-line segment (axis-snapped). It is
    # texture art, so it floats on the swell like everything painted; no
    # collision cells - skis drive straight over it.
    path = course[3] if len(course) > 3 else []
    if len(path) >= 2:
        sx, sy = path[0]
        dx = (path[1][0] - sx + 512) % 1024 - 512
        dy = (path[1][1] - sy + 512) % 1024 - 512
        HALF, THICK, CELL = 40, 6, 3  # texels; 2 rows of 3-texel checkers
        for t in range(-HALF, HALF):
            for s in range(THICK):
                if abs(dy) >= abs(dx):  # racing N-S: strip runs E-W
                    x, y = (sx + t) & 1023, (sy - THICK // 2 + s) & 1023
                else:
                    x, y = (sx - THICK // 2 + s) & 1023, (sy + t) & 1023
                canvas[y][x] = CHECK_WHITE if ((t // CELL) + (s // CELL)) & 1 \
                    else CHECK_DARK
    return canvas, coll


def quantize_tiles(tiles, counts, palette, classes, limit=256):
    """Greedy-merge the most similar tiles (RGB distance on 2x2-quadrant
    signatures) until the count fits. Keeps the more-used tile's pixels.
    Tiles only merge within their class, so rare course features (shoreline
    foam, rope floats) can never be absorbed into water or plain sand."""
    def signature(t):
        sig = []
        for qy in range(4):
            for qx in range(4):
                r = g = b = 0
                for py in range(2):
                    for px in range(2):
                        cr, cg, cb = palette[t[(qy * 2 + py) * 8 + qx * 2 + px] & 0x7F]
                        r += cr
                        g += cg
                        b += cb
                sig += [r >> 2, g >> 2, b >> 2]
        return sig

    sigs = [signature(t) for t in tiles]
    alive = list(range(len(tiles)))
    remap = {}
    merges = 0
    while len(alive) > limit:
        best = None
        for ii in range(len(alive)):
            a = alive[ii]
            sa = sigs[a]
            for jj in range(ii + 1, len(alive)):
                b = alive[jj]
                if classes[a] != classes[b]:
                    continue
                sb = sigs[b]
                d = 0
                for k in range(48):
                    d += abs(sa[k] - sb[k])
                    if best and d >= best[0]:
                        break
                # course tiles (shore, ropes, floats) are precious: make
                # merging them 8x more expensive than softening water
                if classes[a] == 1:
                    d *= 8
                if best is None or d < best[0]:
                    best = (d, a, b)
        _, a, b = best
        # keep the more-used tile
        keep, drop = (a, b) if counts[a] >= counts[b] else (b, a)
        counts[keep] += counts[drop]
        remap[drop] = keep
        alive.remove(drop)
        merges += 1
    # resolve chains
    def resolve(i):
        while i in remap:
            i = remap[i]
        return i
    return alive, resolve, merges


def build_mode7_data(canvas, palette):
    """Dedupe the full 1024x1024 canvas into <=256 tiles (quantising the most
    similar together if needed). Returns (pc7, mp7, pal)."""
    tile_index = {}
    tiles = []
    counts = []
    cell_ids = [[0] * 128 for _ in range(128)]
    for ty in range(128):
        for tx in range(128):
            t = bytes(canvas[ty * 8 + py][tx * 8 + px]
                      for py in range(8) for px in range(8))
            i = tile_index.get(t)
            if i is None:
                i = tile_index[t] = len(tiles)
                tiles.append(t)
                counts.append(0)
            counts[i] += 1
            cell_ids[ty][tx] = i

    raw_unique = len(tiles)
    if raw_unique > 256:
        # class 1 = contains course colours (shore foam, sand, ropes, floats,
        # the start-line checker) - never merged into water
        classes = [1 if any(8 <= (px & 0x7F) <= 15
                            or (px & 0x7F) in (CHECK_DARK, CHECK_WHITE)
                            for px in t) else 0 for t in tiles]
        alive, resolve, merges = quantize_tiles(tiles, counts, palette, classes)
        final = {old: new for new, old in enumerate(alive)}
        mp7 = bytearray(128 * 128)
        for my in range(128):
            for mx in range(128):
                mp7[my * 128 + mx] = final[resolve(cell_ids[my][mx])]
        pc7 = b"".join(tiles[i] for i in alive)
        print("texture: {0} unique tiles, quantised to 256 ({1} merges)"
              .format(raw_unique, merges))
    else:
        mp7 = bytearray(128 * 128)
        for my in range(128):
            for mx in range(128):
                mp7[my * 128 + mx] = cell_ids[my][mx]
        pc7 = b"".join(tiles)
        print("texture: {0} unique tiles".format(raw_unique))

    pal = bytearray()
    for r, g, b in palette:
        bgr = ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)
        pal += struct.pack("<H", bgr)
    return bytes(pc7), bytes(mp7), bytes(pal)


def write_png(path, rows, palette):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    w, h = len(rows[0]), len(rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0)
    plte = b"".join(bytes(c) for c in palette)
    raw = b"".join(b"\x00" + bytes(r) for r in rows)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"PLTE", plte)
           + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


# ---- emit ------------------------------------------------------------------

def db_lines(data):
    lines = []
    for i in range(0, len(data), 16):
        lines.append(".db " + ",".join("${:02X}".format(b) for b in data[i:i + 16]))
    return "\n".join(lines)


def dw_lines(words):
    lines = []
    for i in range(0, len(words), 8):
        lines.append(".dw " + ",".join("${:04X}".format(w) for w in words[i:i + 8]))
    return "\n".join(lines)


# ---- multi-course machinery ------------------------------------------------
# Courses live in assets/courses/<nn>_<name>/course.json (sorted by folder
# name; the menu name is the folder name past the underscore, uppercased).
# A course may carry its own sea_pattern.png / water_params.json, and names
# a wave profile ("wave_profile": "calm" -> assets/waves/calm.json, a wave
# lab export). Identical profiles dedupe: courses share the baked tables.
# The CAMERA keys are global by design (the a-from-d synthesis, sprite
# scale bands and ski row all assume it): a profile that disagrees with
# the default profile's camera fails the bake.

CAMERA_KEYS = ("camH", "pitch", "fovV", "fovH", "skiDist")
WAVE_KEYS = ("amp", "wavelength", "maxX", "phases", "framesPerPhase",
             "crestGlow", "glowGamma")
MAX_PHASES = 32
MAX_COURSES = 8


def find_courses():
    legacy = os.path.join(ASSETS, "course.json")
    assert not os.path.exists(legacy), \
        "assets/course.json has moved: put it at assets/courses/<nn>_<name>/course.json"
    root = os.path.join(ASSETS, "courses")
    out = []
    if os.path.isdir(root):
        for d in sorted(os.listdir(root)):
            cj = os.path.join(root, d, "course.json")
            if os.path.isfile(cj):
                out.append((d, os.path.join(root, d)))
    return out


def course_menu_name(folder):
    name = folder.split("_", 1)[1] if "_" in folder else folder
    name = name.replace("_", " ").upper()[:12]
    for ch in name:  # the menu uses the console font: printable ASCII
        assert 32 <= ord(ch) < 127, "course name has non-ASCII: " + name
    return name


def set_profile(pp):
    """Point the module-level raycast at this profile's params."""
    global K_WAVE
    for k in CAMERA_KEYS:
        assert k not in pp or pp[k] == P[k], \
            "profile changes CAMERA key {0} - the camera is global".format(k)
    for k in WAVE_KEYS:
        P[k] = pp[k]
    n = max(1, round(1024.0 / float(P["wavelength"])))
    P["wavelength"] = 1024.0 / n
    assert P["phases"] & (P["phases"] - 1) == 0, "phases must be a power of two"
    assert P["phases"] <= MAX_PHASES, "profile exceeds WAVE_MAX_PHASES"
    assert P["framesPerPhase"] in (1, 2, 4, 8), "framesPerPhase must be 1/2/4/8"
    K_WAVE = 2 * math.pi / P["wavelength"]


def load_wave_profile(name):
    path = os.path.join(ASSETS, "waves", name + ".json")
    assert os.path.exists(path), "wave profile missing: " + path
    with open(path) as f:
        d = json.load(f)
    return d


def encode_delta(words):
    """First word raw, then one signed byte per word ($80 = escape + raw
    word). The decoder is waveRawLoad pass 1 in camera.asm."""
    enc = bytearray()
    prev = None
    for w in words:
        if prev is None:
            enc += struct.pack("<H", w)
        else:
            dlt = w - prev
            if -127 <= dlt <= 127:
                enc.append(dlt & 0xFF)
            else:
                enc.append(0x80)
                enc += struct.pack("<H", w)
        prev = w
    return bytes(enc)


def encode_map(m):
    """Mode-7 map codec: the water texture tiles the map with a 16-entry
    period, so plain RLE FAILS (adjacent entries differ) but copy-from-16-
    back crushes it. Token: bit7 set = copy (n & $7F) entries from dst-16;
    else n literal bytes follow. Counts 1-127; decoder (mapTo7F,
    camera.asm) stops at the known 16384. Entries 0-15 are always
    literals (nothing to copy from)."""
    out = bytearray()
    i, n = 0, len(m)
    while i < n:
        if i >= 16 and m[i] == m[i - 16]:
            run = 0
            while i + run < n and run < 127 and m[i + run] == m[i + run - 16]:
                run += 1
            out.append(0x80 | run)
            i += run
        else:
            lit = 0
            while (i + lit < n and lit < 127
                   and (i + lit < 16 or m[i + lit] != m[i + lit - 16])):
                lit += 1
            out.append(lit)
            out += m[i:i + lit]
            i += lit
    return bytes(out)


def bake_profile(pp, sky_switch, sky_ref):
    """All per-profile tables (raycasts under set_profile)."""
    set_profile(pp)
    phases = P["phases"]
    out = {"phases": phases,
           "steps": round(256.0 * phases / P["wavelength"]),
           "mask": phases * 256 - 1,
           "tm": [], "g": [], "sky": [], "ski_row": [], "surf_h": []}
    raw_d = []
    for p in range(phases):
        phi = 2 * math.pi * p / phases
        tm_tab, g_tab, n_sky = phase_tables(phi, sky_ref, sky_switch)
        out["tm"].append(tm_tab)
        out["g"].append(g_tab)
        out["sky"].append(n_sky)
        d_words, _ = phase_raw(phi)
        raw_d += d_words
        row = SCANLINES - 1
        while row > 0 and d_words[row] < P["skiDist"]:
            row -= 1
        out["ski_row"].append(row)
        out["surf_h"].append(round(P["amp"] * math.sin(K_WAVE * P["skiDist"] + phi)))
    out["rawd"] = encode_delta(raw_d)
    out["raw_words"] = len(raw_d)
    return out


def bake_course(cdir, sky_switch, sky_ref):
    """Everything derived from one course folder: canvas -> tiles/map/pal,
    packed collision, gates, start grid, rotation colours, the course's
    OBJ palettes, sand fade and sky palette (the last two need the GLOBAL
    sky_switch / sky_ref)."""
    c = {}
    where = os.path.basename(cdir)
    with open(os.path.join(cdir, "course.json")) as f:
        cj = json.load(f)
    over, fade, amb, sky = load_style(cj, where)
    pat, palette = load_pattern(cdir)
    for idx, rgb in COURSE_COLORS.items():
        palette[idx] = rgb
    for idx, rgb in CHECK_COLORS.items():
        palette[idx] = rgb
    for idx, rgb in over.items():
        palette[idx] = rgb
    wp = dict(rotStart=P["rotStart"], rotCount=P["rotCount"],
              rotFrames=P["rotFrames"])
    wpath = os.path.join(cdir, "water_params.json")
    if not os.path.exists(wpath):
        wpath = os.path.join(ASSETS, "water_params.json")
    if os.path.exists(wpath):
        with open(wpath) as f:
            wj = json.load(f)
        for k in ("rotStart", "rotCount", "rotFrames"):
            if k in wj:
                wp[k] = int(wj[k])
    rs, rc = wp["rotStart"], wp["rotCount"]
    palette[SHAL_BLUE] = max((palette[rs + i] for i in range(rc)), key=sum)
    # ambient light: every CGRAM entry the course ships (1-15 water +
    # course block, 48-51 checker + teals) is tinted here, once, so the
    # tiles, the preview PNG, the rotation colours and the ROM palette all
    # agree. Entry 0 (sky backdrop) and everything above 51 are not ours.
    for idx in list(range(1, 16)) + [CHECK_DARK, CHECK_WHITE, TEAL_SAND]:
        palette[idx] = tint(palette[idx], amb)
    lint_pairs(palette, COURSE_PAIRS, "course palette", where)
    c["obj_pal"], c["buoy_pal"] = obj_palettes(amb, where)
    c["fade"] = sand_fade_table(sky_switch, palette[SAND],
                                tint(fade["sand_far"], amb),
                                tint(fade["sand_deep"], amb))
    # sky: the zenith colour is authored directly (NOT ambient-multiplied -
    # the sky is the light source); the clouds ARE lit by the ambient
    c["sky_pal"] = sky_palette(sky_switch, sky_ref, sky)
    c["sky0"] = rgb15(sky)
    c["cloud_pal"] = pal_bytes([tint((255, 255, 255), amb),
                                tint(CLOUD_SHADE, amb)])
    course = load_course(os.path.join(cdir, "course.json"))
    assert course, "unreadable course.json in " + cdir

    # start grid from the racing line (player at the back, facing along
    # the opening segment; world units - the C side derives the camera)
    assert len(course[3]) >= 2, cdir + ": course needs a racing line"
    p = course[3]
    gx, gy = p[0]
    gdx = (p[1][0] - gx + 512) % 1024 - 512
    gdy = (p[1][1] - gy + 512) % 1024 - 512
    gl = math.hypot(gdx, gdy) or 1.0
    gux, guy = gdx / gl, gdy / gl
    gqx, gqy = -guy, gux  # lateral, to the line's left

    def grid_slot(back, lat):
        return (round((gx - gux * back + gqx * lat) * 4) & 4095,
                round((gy - guy * back + gqy * lat) * 4) & 4095)

    c["start_slot"] = grid_slot(44, 0)
    c["npc_slots"] = [grid_slot(8, -28), grid_slot(18, 28), grid_slot(30, -8)]
    c["start_theta"] = round(math.atan2(gdx, gdy) * 128 / math.pi) & 255

    canvas, coll = compose_canvas(pat, course)
    # EXTBG: bit 7 on course pixels -> BG2-high, above BG1 and outside its
    # colour math (crest glow never touches sand); colour = low 7 bits
    exempt = (SAND, SAND_SH, WET_SAND, FLOAT_A, TEAL, TEAL_SAND,
              CHECK_DARK, CHECK_WHITE)
    for row in canvas:
        for x in range(1024):
            if row[x] in exempt:
                row[x] |= 0x80
    pc7, mp7, pal = build_mode7_data(canvas, palette)
    # preview reflects the QUANTISED data the SNES will actually show
    for ty in range(128):
        for tx in range(128):
            base = mp7[ty * 128 + tx] * 64
            for py in range(8):
                row = canvas[ty * 8 + py]
                for px in range(8):
                    row[tx * 8 + px] = pc7[base + py * 8 + px]
    c["preview"] = ([bytes(px & 0x7F for px in row) for row in canvas], palette)
    c["pc7"] = pc7
    c["mp7_rle"] = encode_map(mp7)
    c["pal"] = pal
    packed = bytearray(len(coll) // 4)
    for ci, cv in enumerate(coll):
        assert cv < 4, "collision cell value overflows 2 bits"
        packed[ci >> 2] |= cv << ((ci & 3) * 2)
    c["coll"] = bytes(packed)
    c["coll_sum"] = sum(coll) & 0xFFFF
    c["course"] = course
    c["gates"] = order_gates(course)
    c["rot"] = wp
    rot_colors = []
    for i in range(wp["rotCount"]):
        r, g, b = palette[wp["rotStart"] + i]
        rot_colors.append(((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3))
    c["rot_colors"] = rot_colors
    return c


def sand_fade_table(sky_switch, sand, sand_far, sand_deep):
    """HDMA ch0 hold-run table: CGRAM entry 8 (dry sand) repainted down the
    frame - paler/washed far, richer/darker near; wave-phase independent BY
    DESIGN (divorces the land's light from the breathing sea). Per course:
    the three colours arrive already under the course's ambient."""
    fade = []
    sand_mid = (sky_switch + 224) // 2
    for y in range(224):
        if y <= sand_mid:
            t = 0.0 if y <= sky_switch else \
                (y - sky_switch) / float(sand_mid - sky_switch)
            a, b = sand_far, sand
        else:
            t = (y - sand_mid) / float(223 - sand_mid)
            a, b = sand, sand_deep
        c5 = [round(f + (n - f) * t) >> 3 for f, n in zip(a, b)]
        fade.append((c5[2] << 10) | (c5[1] << 5) | c5[0])
    tab = bytearray()
    y = 0
    while y < 224:
        run = 1
        while y + run < 224 and fade[y + run] == fade[y] and run < 127:
            run += 1
        tab += bytes((run, SAND, SAND, fade[y] & 0xFF, fade[y] >> 8))
        y += run
    tab.append(0)
    return bytes(tab)


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    # ---- discover courses + resolve wave profiles (deduped) ----
    found = find_courses()
    assert found, "no courses under assets/courses/<nn>_<name>/course.json"
    assert len(found) <= MAX_COURSES, "too many courses"
    default_prof = {k: P[k] for k in WAVE_KEYS}
    profiles, prof_names, prof_keys = [], [], []
    course_meta = []  # (folder, dir, menu name, profile index)
    for folder, cdir in found:
        with open(os.path.join(cdir, "course.json")) as f:
            cj = json.load(f)
        pname = cj.get("wave_profile")
        pp = dict(default_prof)
        if pname:
            pj = load_wave_profile(pname)
            for k in WAVE_KEYS:
                if k in pj:
                    pp[k] = pj[k]
            for k in CAMERA_KEYS:
                assert k not in pj or pj[k] == P[k], \
                    "{0}: profile {1} changes camera key {2}".format(
                        folder, pname, k)
        key = tuple(pp[k] for k in WAVE_KEYS)
        if key in prof_keys:
            pi = prof_keys.index(key)
        else:
            pi = len(profiles)
            prof_keys.append(key)
            profiles.append(pp)
            prof_names.append(pname or "default")
        course_meta.append((folder, cdir, course_menu_name(folder), pi))

    # ---- global sky: the switch is the min across ALL profiles' horizons
    # (the roughest course sets the band), the gradient ref the max ----
    all_h = []
    for pp in profiles:
        set_profile(pp)
        all_h += [raycast_phase(2 * math.pi * p / P["phases"])[0]
                  for p in range(P["phases"])]
    sky_ref = max(all_h)
    sky_switch = ((min(all_h) - SKY_SAFE) // 8) * 8
    assert UI_LINES + 8 <= sky_switch <= 127, \
        "mode-1 sky band needs UI_LINES+8 <= switch <= 127 (HDMA count)"
    sky_gfx, sky_pal2, sky_rows = build_sky_band(sky_switch, sky_ref)
    assert SKY_CHAR0 + sky_rows <= 512, "sky tiles overflow into the OBJ sheet"

    # ---- bake every profile and course ----
    baked_profs = [bake_profile(pp, sky_switch, sky_ref) for pp in profiles]
    baked_courses = [bake_course(cdir, sky_switch, sky_ref)
                     for _, cdir, _, _ in course_meta]
    for i, ((folder, cdir, mname, pi), bc) in enumerate(
            zip(course_meta, baked_courses)):
        rows, pal = bc["preview"]
        write_png(os.path.join(OUT_DIR, "sea{0}.png".format(i)), rows, pal)
        if i == 0:  # compat name for README/web tooling
            write_png(os.path.join(OUT_DIR, "sea.png"), rows, pal)
        print("course {0} '{1}': {2} buoys, {3} waypoints, profile {4}, "
              "map {5}B rle, coll sum {6}".format(
                  i, mname, len(bc["course"][2]), len(bc["course"][3]),
                  prof_names[pi], len(bc["mp7_rle"]), bc["coll_sum"]))

    # ---- wavetables.asm ----
    asm = ['.include "hdr.asm"', ""]
    externs = []
    for pf, bp in enumerate(baked_profs):
        for p in range(bp["phases"]):
            asm.append('.section ".wavf{0}p{1}" superfree'.format(pf, p))
            for name in ("tm", "g"):
                label = "wave_{0}_f{1}p{2}".format(name, pf, p)
                asm.append(label + ":")
                asm.append(db_lines(bp[name][p]))
                externs.append("extern char {0};".format(label))
            asm.append(".ends")
            asm.append("")
        asm.append('.section ".wraw{0}" superfree'.format(pf))
        asm.append("wave_rawd_f{0}:".format(pf))
        asm.append(db_lines(bp["rawd"]))
        asm.append(".ends")
        asm.append("")
        externs.append("extern char wave_rawd_f{0};".format(pf))

    for ci, bc in enumerate(baked_courses):
        asm.append('.section ".crs{0}t" superfree'.format(ci))
        asm.append("crs{0}_tiles:".format(ci))
        asm.append(db_lines(bc["pc7"]))
        asm.append(".ends")
        asm.append('.section ".crs{0}d" superfree'.format(ci))
        asm.append("crs{0}_map:".format(ci))
        asm.append(db_lines(bc["mp7_rle"]))
        # palette: ONLY the entries the course owns - 1-15 (water pattern +
        # course block; 0 is the backdrop, owned by the sky) and 48-51
        # (checker + shallows teal). A full 512-byte load would wipe the
        # UI/sky/HUD/OBJ palettes, which load once at boot.
        asm.append("crs{0}_pal1:".format(ci))
        asm.append(db_lines(bc["pal"][2:32]))
        asm.append("crs{0}_pal48:".format(ci))
        asm.append(db_lines(bc["pal"][96:104]))
        # OBJ palettes 0-3 (128-191) + buoy palette 5 (208-223) and the
        # sand-fade HDMA table, all under the course's ambient light
        asm.append("crs{0}_obj:".format(ci))
        asm.append(db_lines(bc["obj_pal"]))
        asm.append("crs{0}_buoy:".format(ci))
        asm.append(db_lines(bc["buoy_pal"]))
        asm.append("crs{0}_fade:".format(ci))
        asm.append(db_lines(bc["fade"]))
        # sky band anchors (CGRAM 32-47) + the ambient-lit cloud pair (29-30)
        asm.append("crs{0}_sky:".format(ci))
        asm.append(db_lines(bc["sky_pal"]))
        asm.append("crs{0}_cloud:".format(ci))
        asm.append(db_lines(bc["cloud_pal"]))
        asm.append("crs{0}_coll:".format(ci))
        asm.append(db_lines(bc["coll"]))
        asm.append(".ends")
        asm.append("")
        for lbl in ("tiles", "map", "pal1", "pal48", "obj", "buoy", "fade",
                    "sky", "cloud", "coll"):
            externs.append("extern char crs{0}_{1};".format(ci, lbl))

    # s0.7 sine table for the camera heading (256 binary degrees)
    sin_bytes = [(round(127 * math.sin(2 * math.pi * i / 256))) & 0xFF
                 for i in range(256)]
    asm.append('.section ".camsin" superfree')
    asm.append("camSinTab:")
    asm.append(db_lines(sin_bytes))
    asm.append(".ends")
    asm.append("")

    # jet ski sprite sheet (4bpp OBJ tiles) + palette
    ski_tiles, ski_pal, ski_sheet = build_ski_sheet()
    tall_tiles, tall_sheet = build_tall_sheet()
    write_png(os.path.join(OUT_DIR, "ski.png"),
              [bytes(r) for r in ski_sheet + tall_sheet],
              SKI_PALETTE + [(0, 0, 0)] * 240)
    asm.append('.section ".skigfx" superfree')
    asm.append("ski_tiles:")
    asm.append(db_lines(ski_tiles))
    asm.append("tall_tiles:")
    asm.append(db_lines(tall_tiles))
    # boot-time palette 0 for oamInitGfxSet; courseLoad replaces palettes
    # 0-3 + 5 with the course's ambient-lit set before the first frame
    asm.append("ski_pal:")
    asm.append(db_lines(ski_pal))
    asm.append("lamp_pal:")
    asm.append(db_lines(pal_bytes(LAMP_PALETTE)))
    asm.append("sky_gfx:")
    asm.append(db_lines(sky_gfx))
    asm.append("sky_pal2:")
    asm.append(db_lines(sky_pal2))
    cloud_gfx, cloud_map, cloud_chars = build_clouds()
    assert (CLOUD_ROW0 + CLOUD_TROWS) * 8 <= sky_switch, \
        "cloud strip reaches below the mode switch"
    asm.append("cloud_gfx:")
    asm.append(db_lines(cloud_gfx))
    asm.append("cloud_map:")
    asm.append(db_lines(cloud_map))
    hud_gfx, hud_pal = build_hud_font()
    asm.append("hud_gfx:")
    asm.append(db_lines(hud_gfx))
    asm.append("hud_pal:")
    asm.append(db_lines(hud_pal))
    asm.append(".ends")
    asm.append("")

    with open(os.path.join(OUT_DIR, "wavetables.asm"), "w", newline="\n") as f:
        f.write("\n".join(asm))

    # ---- wavedata.h ----
    with open(os.path.join(OUT_DIR, "wavedata.h"), "w", newline="\n") as f:
        f.write("""#ifndef WAVEDATA_H
#define WAVEDATA_H

#include <snes.h>

#define WAVE_MAX_PHASES {0}
#define WAVE_COURSES {1}
#define WAVE_RAW_STRIDE 448
/* fixed maxima: array sizes and the OAM layout (NPC_SPR) hang off these,
   so they stay constant across courses; the ACTUAL counts are runtime
   (buoyCount/pathCount, set by courseGeom) */
#define WAVE_MAX_BUOYS {{MXB}}
#define WAVE_MAX_PATH {{MXP}}
/* OBJ sheet: byte size, and the wake conveyor cells (16x16, names +2 each,
   one per intensity level) */
#define WAVE_SKI_SHEET {{SHB}}
#define WAVE_TALL_SHEET {{TLB}} /* name table 2: the stacked tall racers */
#define WAVE_SPRAY_CELL 192
#define WAVE_LIGHT_CELL 224 /* start-tree lamps: dark / +2 red / +4 green */
#define WAVE_SPRAY_LEVELS {{SPLV}}
/* mode-1 sky band: tile rows under the text, mode 7 resumes at the switch */
#define WAVE_SKY_SWITCH {{SKSW}}
#define WAVE_SKY_ROWS {{SKRW}}
#define WAVE_SKY_CHAR0 {{SKC0}}
/* HUD gradient font: SH glyphs, then DH tops, then DH bottoms */
#define WAVE_HUD_CHAR0 {{HDC0}}
#define WAVE_HUD_GLYPHS {{HDGL}}
/* BG2 cloud overlay: char/map strip geometry + the CGRAM 50 shade */
#define WAVE_CLOUD_CHAR0 {{CLC0}}
#define WAVE_CLOUD_ROW0 {{CLR0}}
#define WAVE_CLOUD_TROWS {{CLTR}}
#define WAVE_CLOUD_CHARS {{CLCH}}
#define WAVE_CLOUD_SHADE 0x{{CLSH}}
#define WAVE_UI_LINES {2}
#define WAVE_BASE_ROLL 64
#define WAVE_SKI_PPT_Q4 {3}
#define WAVE_SKI_REST_ROW {4}
#define WAVE_SKI_DIST {5}

/* per-profile tables + phase-advance params, filled by waveProfLoad */
extern u8 *waveTM[WAVE_MAX_PHASES];
extern u8 *waveG[WAVE_MAX_PHASES];
extern u8 waveSky[WAVE_MAX_PHASES];
extern u8 waveSkiRow[WAVE_MAX_PHASES];
extern s8 waveSurfH[WAVE_MAX_PHASES];
extern u16 wvSteps; /* phase steps per (vAlong>>4) texel, from wavelength */
extern u16 wvMask;  /* phases*256 - 1 */
extern dmaMemory wrSrc; /* delta-d stream for waveRawLoad (camera.asm) */
extern u16 wrWords;     /* decoded d words (= phases * 224) */

/* per-course geometry: counts, start grid (world units, ski positions;
   heading in binary degrees), buoys, racing line - filled by courseGeom */
extern u8 courseProf; /* the course's wave profile: pass to waveProfLoad */
extern u8 buoyCount, pathCount;
extern u16 startX, startY;
extern u8 startTheta;
extern u16 npcGridX[3], npcGridY[3];
extern u16 buoyX[WAVE_MAX_BUOYS + 1];
extern u16 buoyY[WAVE_MAX_BUOYS + 1];
extern u8 buoyType[WAVE_MAX_BUOYS + 1];
extern u16 pathX[WAVE_MAX_PATH + 1];
extern u16 pathY[WAVE_MAX_PATH + 1];
/* power gates: the buoys again, sorted into racing-line order, with the
   line direction at each (s8, unit * 64) and the segment they belong to */
extern u16 gateX[WAVE_MAX_BUOYS + 1];
extern u16 gateY[WAVE_MAX_BUOYS + 1];
extern u8 gateLeft[WAVE_MAX_BUOYS + 1];
extern s8 gateNx[WAVE_MAX_BUOYS + 1];
extern s8 gateNy[WAVE_MAX_BUOYS + 1];
extern u8 gateWp[WAVE_MAX_BUOYS + 1];
/* per-course blobs (ROM addresses for the loaders) + texture rotation.
   csObj = OBJ palettes 0-3 (CGRAM 128, 128 bytes), csBuoy = palette 5
   (CGRAM 208, 32 bytes), csFade = the sand-fade HDMA table - all baked
   under the course's ambient light */
extern dmaMemory csTiles, csMap, csPal1, csPal48, csObj, csBuoy, csColl, csFade;
/* csSky = the mode-1 sky band's 16 anchors (CGRAM 32, 32 bytes), csSky0 =
   backdrop entry 0 (the same zenith colour), csCloud = BG3 cloud white +
   shade under the ambient (CGRAM 29, 4 bytes) */
extern dmaMemory csSky, csCloud;
extern u16 csSky0;
#define WAVE_BUOY_PAL {6}
extern u16 csTilLen;
extern u8 wvRotStart, wvRotCount, wvRotFrames;
extern u16 rotCols[8];

void waveProfLoad(u8 pf); /* tables/pointers; then call waveRawLoad */
void courseGeom(u8 c);    /* geometry + blob pointers + courseProf */
/* menu label (console-font ASCII) copied into out, NUL included - a
   returned char* loses its bank byte to the tcc 16-bit pointer-copy bug */
void courseNameTo(u8 c, char *out);

#endif
""".replace("{{MXB}}", str(MAX_BUOYS)).replace("{{MXP}}", str(MAX_PATH))
           .replace("{{SHB}}", str(len(ski_tiles)))
           .replace("{{TLB}}", str(len(tall_tiles)))
           .replace("{{SPLV}}", str(SPRAY_LEVELS))
           .replace("{{SKSW}}", str(sky_switch))
           .replace("{{SKRW}}", str(sky_rows))
           .replace("{{SKC0}}", str(SKY_CHAR0))
           .replace("{{HDC0}}", str(HUD_CHAR0))
           .replace("{{HDGL}}", str(len(HUD_GLYPHS)))
           .replace("{{CLC0}}", str(CLOUD_CHAR0))
           .replace("{{CLR0}}", str(CLOUD_ROW0))
           .replace("{{CLTR}}", str(CLOUD_TROWS))
           .replace("{{CLCH}}", str(cloud_chars))
           .replace("{{CLSH}}", "{0:04X}".format(
               ((CLOUD_SHADE[2] >> 3) << 10) | ((CLOUD_SHADE[1] >> 3) << 5)
               | (CLOUD_SHADE[0] >> 3)))
           .format(MAX_PHASES, len(baked_courses), UI_LINES,
                   round((P["camH"] / (P["skiDist"] ** 2 + P["camH"] ** 2))
                         * ((SCANLINES - 1) / math.radians(P["fovV"])) * 2 * 16),
                   SKI_WATERLINE_ROW - 32, round(P["skiDist"]), BUOY_PAL))

    # ---- wavedata.c ----
    with open(os.path.join(OUT_DIR, "wavedata.c"), "w", newline="\n") as f:
        f.write("/* generated by tools/bake_tables.py - do not edit */\n")
        f.write('#include "wavedata.h"\n\n')
        f.write("\n".join(externs) + "\n\n")
        f.write("u8 *waveTM[WAVE_MAX_PHASES];\nu8 *waveG[WAVE_MAX_PHASES];\n")
        f.write("u8 waveSky[WAVE_MAX_PHASES];\n")
        f.write("u8 waveSkiRow[WAVE_MAX_PHASES];\ns8 waveSurfH[WAVE_MAX_PHASES];\n")
        f.write("u16 wvSteps;\nu16 wvMask;\ndmaMemory wrSrc;\nu16 wrWords;\n\n")
        f.write("u8 courseProf;\nu8 buoyCount, pathCount;\n")
        f.write("u16 startX, startY;\nu8 startTheta;\n")
        f.write("u16 npcGridX[3], npcGridY[3];\n")
        f.write("u16 buoyX[WAVE_MAX_BUOYS + 1];\nu16 buoyY[WAVE_MAX_BUOYS + 1];\n")
        f.write("u8 buoyType[WAVE_MAX_BUOYS + 1];\n")
        f.write("u16 pathX[WAVE_MAX_PATH + 1];\nu16 pathY[WAVE_MAX_PATH + 1];\n")
        f.write("u16 gateX[WAVE_MAX_BUOYS + 1];\nu16 gateY[WAVE_MAX_BUOYS + 1];\n")
        f.write("u8 gateLeft[WAVE_MAX_BUOYS + 1];\n")
        f.write("s8 gateNx[WAVE_MAX_BUOYS + 1];\ns8 gateNy[WAVE_MAX_BUOYS + 1];\n")
        f.write("u8 gateWp[WAVE_MAX_BUOYS + 1];\n")
        f.write("dmaMemory csTiles, csMap, csPal1, csPal48, csObj, csBuoy, "
                "csColl, csFade, csSky, csCloud;\nu16 csSky0;\n")
        f.write("u16 csTilLen;\n")
        f.write("u8 wvRotStart, wvRotCount, wvRotFrames;\nu16 rotCols[8];\n\n")

        f.write("void waveProfLoad(u8 pf)\n{\n    switch (pf)\n    {\n")
        for pf, bp in enumerate(baked_profs):
            f.write("    case {0}:\n".format(pf))
            for p in range(bp["phases"]):
                f.write("        waveTM[{0}] = (u8 *)&wave_tm_f{1}p{0};\n"
                        .format(p, pf))
            for p in range(bp["phases"]):
                f.write("        waveG[{0}] = (u8 *)&wave_g_f{1}p{0};\n"
                        .format(p, pf))
            for p, v in enumerate(bp["sky"]):
                f.write("        waveSky[{0}] = {1};\n".format(p, v))
            for p, v in enumerate(bp["ski_row"]):
                f.write("        waveSkiRow[{0}] = {1};\n".format(p, v))
            for p, v in enumerate(bp["surf_h"]):
                f.write("        waveSurfH[{0}] = {1};\n".format(p, v))
            f.write("        wvSteps = {0};\n".format(bp["steps"]))
            f.write("        wvMask = {0};\n".format(bp["mask"]))
            f.write("        wrSrc.mem.p = (u8 *)&wave_rawd_f{0};\n".format(pf))
            f.write("        wrWords = {0};\n".format(bp["raw_words"]))
            f.write("        break;\n")
        f.write("    }\n}\n\n")

        f.write("void courseGeom(u8 c)\n{\n    switch (c)\n    {\n")
        for ci, ((folder, cdir, mname, pi), bc) in enumerate(
                zip(course_meta, baked_courses)):
            course = bc["course"]
            f.write("    case {0}:\n".format(ci))
            f.write("        courseProf = {0};\n".format(pi))
            f.write("        buoyCount = {0};\n        pathCount = {1};\n"
                    .format(len(course[2]), len(course[3])))
            assert len(course[2]) <= MAX_BUOYS, folder + ": too many buoys"
            assert len(course[3]) <= MAX_PATH, folder + ": too many waypoints"
            f.write("        startX = {0};\n        startY = {1};\n"
                    "        startTheta = {2};\n".format(
                        bc["start_slot"][0], bc["start_slot"][1],
                        bc["start_theta"]))
            for i, (nx, ny) in enumerate(bc["npc_slots"]):
                f.write("        npcGridX[{0}] = {1};\n"
                        "        npcGridY[{0}] = {2};\n".format(i, nx, ny))
            for i, (bx, by, side) in enumerate(course[2]):
                f.write("        buoyX[{0}] = {1};\n".format(i, (bx * 4) & 4095))
                f.write("        buoyY[{0}] = {1};\n".format(i, (by * 4) & 4095))
                f.write("        buoyType[{0}] = {1};\n"
                        .format(i, 1 if side == "R" else 0))
            for i, (px, py) in enumerate(course[3]):
                f.write("        pathX[{0}] = {1};\n".format(i, (px * 4) & 4095))
                f.write("        pathY[{0}] = {1};\n".format(i, (py * 4) & 4095))
            for i, (gx2, gy2, left, nx, ny, wp2) in enumerate(bc["gates"]):
                f.write("        gateX[{0}] = {1};\n".format(i, (gx2 * 4) & 4095))
                f.write("        gateY[{0}] = {1};\n".format(i, (gy2 * 4) & 4095))
                f.write("        gateLeft[{0}] = {1};\n".format(i, left))
                f.write("        gateNx[{0}] = {1};\n".format(i, nx))
                f.write("        gateNy[{0}] = {1};\n".format(i, ny))
                f.write("        gateWp[{0}] = {1};\n".format(i, wp2))
            f.write("        csTiles.mem.p = (u8 *)&crs{0}_tiles;\n".format(ci))
            f.write("        csTilLen = {0};\n".format(len(bc["pc7"])))
            f.write("        csMap.mem.p = (u8 *)&crs{0}_map;\n".format(ci))
            f.write("        csPal1.mem.p = (u8 *)&crs{0}_pal1;\n".format(ci))
            f.write("        csPal48.mem.p = (u8 *)&crs{0}_pal48;\n".format(ci))
            f.write("        csObj.mem.p = (u8 *)&crs{0}_obj;\n".format(ci))
            f.write("        csBuoy.mem.p = (u8 *)&crs{0}_buoy;\n".format(ci))
            f.write("        csColl.mem.p = (u8 *)&crs{0}_coll;\n".format(ci))
            f.write("        csFade.mem.p = (u8 *)&crs{0}_fade;\n".format(ci))
            f.write("        csSky.mem.p = (u8 *)&crs{0}_sky;\n".format(ci))
            f.write("        csCloud.mem.p = (u8 *)&crs{0}_cloud;\n".format(ci))
            f.write("        csSky0 = 0x{0:04X};\n".format(bc["sky0"]))
            f.write("        wvRotStart = {0};\n".format(bc["rot"]["rotStart"]))
            f.write("        wvRotCount = {0};\n".format(bc["rot"]["rotCount"]))
            f.write("        wvRotFrames = {0};\n".format(bc["rot"]["rotFrames"]))
            for i, col in enumerate(bc["rot_colors"]):
                f.write("        rotCols[{0}] = 0x{1:04X};\n".format(i, col))
            f.write("        break;\n")
        f.write("    }\n}\n\n")

        f.write("void courseNameTo(u8 c, char *out)\n{\n")
        f.write("    char *nm = \"\";\n    switch (c)\n    {\n")
        for ci, (folder, cdir, mname, pi) in enumerate(course_meta):
            f.write("    case {0}:\n        nm = \"{1}\";\n"
                    "        break;\n".format(ci, mname))
        f.write("    }\n    while (*nm)\n        *out++ = *nm++;\n"
                "    *out = 0;\n}\n")

    # ---- byte-budget report ----
    fixed = len(ski_tiles) + len(tall_tiles) + len(sky_gfx) + len(cloud_gfx) \
        + len(cloud_map) + len(hud_gfx) + len(hud_pal) + 256
    total_prof = 0
    for pf, (bp, nm) in enumerate(zip(baked_profs, prof_names)):
        sz = len(bp["rawd"]) + sum(len(t) for t in bp["tm"]) \
            + sum(len(g) for g in bp["g"])
        total_prof += sz
        print("profile {0} '{1}': {2} phases, {3} bytes".format(
            pf, nm, bp["phases"], sz))
    total_crs = 0
    for ci, bc in enumerate(baked_courses):
        pals = 38 + len(bc["obj_pal"]) + len(bc["buoy_pal"]) + len(bc["fade"]) \
            + len(bc["sky_pal"]) + len(bc["cloud_pal"]) + 2
        sz = len(bc["pc7"]) + len(bc["mp7_rle"]) + pals + len(bc["coll"])
        total_crs += sz
        print("course {0}: {1} bytes ({2} tiles raw, {3} map rle, {4} coll, "
              "{5} palettes+fade)".format(ci, sz, len(bc["pc7"]),
                                         len(bc["mp7_rle"]), len(bc["coll"]),
                                         pals))
    print("BUDGET: baked shared gfx ~{0}K, profiles {1}K, courses {2}K "
          "(+ code/sfx; 512K cap)".format(
              fixed // 1024, total_prof // 1024, total_crs // 1024))


if __name__ == "__main__":
    main()
