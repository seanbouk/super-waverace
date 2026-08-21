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
SKY_CHAR0 = 192      # first tile id for sky rows (VRAM 0x5C00, above font)
CLOUD_CHAR0 = 128    # BG2 cloud chars: VRAM 0x5800, between font and sky
CLOUD_ROW0 = 5       # strip's top map/screen tile row (scanline 40)
CLOUD_TROWS = 4      # strip height in tile rows
CLOUD_SHADE = (204, 222, 242)  # CGRAM 50: soft cloud underside

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
    cycle (the moving horizon slices into it, it never breathes). Linear,
    with a small head start (+6): the old pow-1.3 curve rounded the first
    two anchors both to zero, which drew a flat backdrop-blue band under
    the HUD instead of a gradient from the very first line."""
    span = max(1, sky_ref - UI_LINES - 1)
    return SKY_GRAD_MAX * min(1.0, (line - UI_LINES + 6.0) / (span + 6.0))


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

    # TM: BG1 for the text band (BG2 clouds must never ride under the HUD),
    # BG1+BG2 through the tiled sky (the scrolling cloud overlay), then
    # backdrop-only safe strip, then the sea
    tab_tm = bytes(repeat_blocks(UI_LINES, 0x11)
                   + repeat_blocks(switch - UI_LINES, 0x13)
                   + repeat_blocks(n_sky - switch, SKY_TM)
                   + bytearray((0x81, SEA_TM, 0x00)))
    tab_g = hdma_table(switch, (0xE0,), g_entries) # mode-1 region: add zero
    return tab_tm, tab_g, n_sky


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
    base5 = [c >> 3 for c in SKY_RGB]

    # anchors use indices 1..15 ONLY: index 0 is TRANSPARENT in 4bpp, so
    # any index-0 pixel shows the flat backdrop through the tile - which
    # is exactly the "dead blue band under the HUD" bug
    def anchor_add(k):
        line = UI_LINES + (switch - UI_LINES) * (k - 1) / 14.0
        return min(31, round(sky_add_at(line, sky_ref)))

    pal = bytearray()
    for k in range(16):
        a = anchor_add(k)
        r, g, b = (min(31, c + a) for c in base5)
        pal += struct.pack("<H", (b << 10) | (g << 5) | r)
    pats = ((0, 0, 0, 0), (1, 0, 0, 0), (1, 0, 1, 0), (1, 1, 1, 0))
    grid = [[0] * 8 for _ in range(n_rows * 8)]
    for g in range(n_rows * 8):
        # map line UI_LINES+g draws on SCREEN line UI_LINES+g-1
        s = min(switch - 1, max(UI_LINES, UI_LINES + g - 1))
        p = 1.0 + 14.0 * (s - UI_LINES) / max(1, switch - UI_LINES - 1)
        k = min(15, int(p))
        d = pats[min(3, int((p - k) * 4))]
        for x in range(8):
            c = k + d[(x + g) & 3] # rotate the pattern per line: Bayer-ish
            grid[g][x] = min(15, c)
    return encode_4bpp(grid, 1, n_rows), bytes(pal), n_rows


def build_clouds():
    """BG2 cloud overlay: a 256x32 strip of dithered cumulus on palette
    row 3 (0 transparent, 1 = the start line's true white at CGRAM 49,
    2 = CLOUD_SHADE at CGRAM 50 - both in the 50-127 BG reserve, nothing
    else lives there). X wraps, so the 256px map loops seamlessly - and
    256px against 256 binary degrees of heading means one full turn
    scrolls exactly one map: the loop matches turn for turn.
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
    raw = encode_4bpp(grid, 32, CLOUD_TROWS)
    blank = bytes(32)
    chars, index, mapw = [], {}, bytearray()
    for i in range(32 * CLOUD_TROWS):
        t = raw[i * 32:i * 32 + 32]
        if t == blank:
            mapw += struct.pack("<H", 0) # font space char: transparent
            continue
        if t not in index:
            index[t] = len(chars)
            chars.append(t)
        # priority bit set: mode 1 draws BG2-high above BG1-low (the sky)
        mapw += struct.pack("<H", 0x2000 | 0x0C00
                            | (CLOUD_CHAR0 + index[t]))
    assert len(chars) <= SKY_CHAR0 - CLOUD_CHAR0, \
        "cloud tiles overflow into the sky chars ({0})".format(len(chars))
    return b"".join(chars), bytes(mapw), len(chars)


# ---- HUD font: gradient text without an HDMA channel -------------------------
# All 8 HDMA channels are spoken for, so the "text colour changes every
# scanline" effect is baked into the art instead: every glyph pixel ROW
# carries its own palette index (1..8), and three static CGRAM ramps (rows
# 4/5/6, CGRAM 64-111 - the free BG reserve) do the per-scanline colouring.
# Order must match hudIdx() in game/src/ui.c.
HUD_GLYPHS = "0123456789'\"/!ADEFGHIKLMNOPRSTW*."  # * / . = power pips
HUD_CHAR0 = 640  # VRAM 0x7800 from the 0x5000 BG1 base (map ids are 10-bit)
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


def phase_raw(phi):
    """Raw per-scanline arrays for the runtime camera-table builder:
    d = hit distance (texels, 16-bit), a = horizontal scale (8.8).
    Sky lines carry the far-cap values; the TM channel hides them."""
    n_sky, sea_x = raycast_phase(phi)
    xs = [P["maxX"]] * n_sky + sea_x
    d_words, a_words = [], []
    for x in xs:
        assert x < 4096, "maxX must stay below 4096 for the 16x8 multiplier"
        d_words.append(round(x) & 0xFFFF)
        # /4: one texture texel spans four world units (the world-scale)
        a_words.append(max(1, min(0x7FFF, round(x * TAN_HALF_H / 128.0 * 64.0))))
    return d_words, a_words


# ---- jet ski sprite ---------------------------------------------------------
# 32x32 rear view, hand-authored. Chars map to OBJ palette indices below.
# Lean frames are generated by shearing (top shifts toward the turn).

SKI_ART = [
    "..............KKKK..............",
    "............KKYYYYKK............",
    "...........KYYYYYYYYK...........",
    "...........KYYYYYYYyK...........",
    "...........KYyyyyyyyK...........",
    "............KyyyyyyK............",
    "............KKKKKKKK............",
    "..........KKBBBBBBBBKK..........",
    ".........KBBBBBBBBBBBBK.........",
    "........KBBBBBBBBBBBBBBK........",
    ".......KBBBBBBBBBBBBBBBBK.......",
    ".......KBBbBBBBBBBBBBbBBK.......",
    "......KBBb.bBBBBBBBBb.bBBK......",
    "......KFFb..bBBBBBBb..bFFK......",
    "......KFFb..bBBBBBBb..bFFK......",
    ".....KGGGK..bBBBBBBb..KGGGK.....",
    ".....KGGGK.KbBBBBBBbK.KGGGK.....",
    "....KKGGGKKKbbBBBBbbKKKGGGKK....",
    "...KWWKKKKWWKbbbbbbKWWKKKKWWK...",
    "..KWWWWWWWWWWKKKKKKWWWWWWWWWWK..",
    "..KWWWWWWWWWWWWWWWWWWWWWWWWWWK..",
    ".KWWwWWWWWWWWWWWWWWWWWWWWWwWWWK.",
    ".KRRRRRRRRRRRRRRRRRRRRRRRRRRRRK.",
    "KRRRRRRRRRRRRRRRRRRRRRRRRRRRRRRK",
    "KRRrRRRRRRRRRRRRRRRRRRRRRRRrRRRK",
    "KRRrRRRRRRRRRRRRRRRRRRRRRRRrRRRK",
    "KRrrrRRRRRRRRRRRRRRRRRRRRrrrRRRK",
    "KWWWWWWWWWWWWWWWWWWWWWWWWWWWWWWK",
    ".KWWWWWWWWWWWWWWWWWWWWWWWWWWWWK.",
    "..KKWWWWWWWWWWWWWWWWWWWWWWWWKK..",
    "....KKKKKKKKKKKKKKKKKKKKKKKK....",
    "................................",
]

SKI_CHARS = {".": 0, "K": 1, "Y": 2, "y": 3, "B": 4, "b": 5,
             "F": 6, "G": 7, "W": 8, "w": 9, "R": 10, "r": 11}

SKI_PALETTE = [
    (0, 0, 0),        # 0 transparent
    (16, 16, 24),     # 1 outline
    (240, 200, 40),   # 2 helmet
    (190, 150, 30),   # 3 helmet shade
    (40, 80, 180),    # 4 suit
    (24, 50, 120),    # 5 suit shade
    (240, 190, 150),  # 6 skin
    (150, 150, 160),  # 7 grip grey
    (240, 244, 248),  # 8 hull white
    (190, 200, 210),  # 9 hull white shade
    (220, 60, 50),    # 10 hull red
    (160, 40, 36),    # 11 hull red shade
    (64, 208, 80),    # 12 start-light green
    (34, 130, 48),    # 13 start-light green shade
] + [(0, 0, 0)] * 2

SKI_WATERLINE_ROW = 26  # sprite row that sits at the surface when at rest

# NPC rider/hull recolours (OBJ palettes 1-3; the tiles are shared).
# Overrides by palette index: 2/3 helmet, 4/5 suit, 10/11 hull accent.
NPC_PALETTES = [
    {2: (200, 240, 120), 3: (148, 184, 84),   # green racer
     4: (24, 96, 48), 5: (14, 60, 30),
     10: (72, 190, 84), 11: (46, 128, 54)},
    {2: (238, 238, 244), 3: (184, 184, 196),  # purple racer
     4: (72, 36, 122), 5: (46, 22, 78),
     10: (172, 92, 224), 11: (116, 58, 154)},
    {2: (40, 40, 52), 3: (26, 26, 34),        # orange racer
     4: (122, 50, 20), 5: (80, 32, 12),
     10: (240, 150, 40), 11: (176, 106, 26)},
]


def ski_frame(shear):
    """32x32 index grid; shear shifts rows above the hull toward +x."""
    grid = [[0] * 32 for _ in range(32)]
    for y, row in enumerate(SKI_ART):
        off = round((SKI_WATERLINE_ROW - y) * shear) if y < SKI_WATERLINE_ROW else 0
        for x, ch in enumerate(row):
            c = SKI_CHARS[ch]
            if c:
                nx = x + off
                if 0 <= nx < 32:
                    grid[y][nx] = c
    return grid


def ski_scaled(size):
    """size x size grid: the rear-view ski for NPC racers. Cropped at the
    waterline (rows 0..SKI_WATERLINE_ROW) so, like the flat-bottomed buoys,
    nothing hangs below the surface row it gets anchored to; scaled with a
    majority-vote box filter (keeps the outline/hull blobs readable at 8px);
    bottom-anchored in the grid."""
    src = ski_frame(0.0)
    sh = SKI_WATERLINE_ROW + 1
    h = max(1, round(sh * size / 32.0))
    g = [[0] * size for _ in range(size)]
    for dy in range(h):
        y0 = dy * sh // h
        y1 = max(y0 + 1, (dy + 1) * sh // h)
        for dx in range(size):
            x0 = dx * 32 // size
            x1 = max(x0 + 1, (dx + 1) * 32 // size)
            counts = {}
            area = 0
            for sy in range(y0, y1):
                for sx in range(x0, x1):
                    area += 1
                    c = src[sy][sx]
                    if c:
                        counts[c] = counts.get(c, 0) + 1
            if counts and sum(counts.values()) * 2 >= area:
                g[size - h + dy][dx] = max(counts.items(),
                                           key=lambda kv: kv[1])[0]
    return g


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


def buoy_grid(size, right):
    """Flat-bottomed circle (stable silhouette across scales), letter always."""
    g = [[0] * size for _ in range(size)]
    body, shade = (10, 11) if right else (2, 3)
    letter_col = 8 if right else 1
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


SPRAY_W, SPRAY_S = 8, 9  # ski palette: hull white + its shade


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
    """128x128 sheet: player ski frames (straight + lean), buoys at 5 sizes,
    the NPC rear-view ski at 5 sizes (rows 64+, recoloured by palette), the
    wake conveyor cells (row 96+) and the start-tree lamps (row 112+).
    128px wide = 16 tiles/row, matching OAM's name-row stride exactly."""
    sheet = [[0] * 128 for _ in range(128)]
    for f, shear in enumerate((0.0, 0.14)):
        grid = ski_frame(shear)
        for y in range(32):
            for x in range(32):
                sheet[y][f * 32 + x] = grid[y][x]

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
    # NPC ski scale ladder (same distance bands as the buoys)
    blitg(ski_scaled(32), 0, 64, 32)   # name 128
    blitg(ski_scaled(24), 32, 64, 32)  # name 132
    blitg(ski_scaled(16), 64, 64, 16)  # name 136
    blitg(ski_scaled(12), 80, 64, 16)  # name 138
    blitg(ski_scaled(8), 96, 64, 16)   # name 140
    # wake conveyor cells, one per intensity: names 192 / 194 / 196 / 198
    for lv in range(SPRAY_LEVELS):
        blitg(spray_cell(lv), lv * 16, 96, 16)
    # start-tree lamps: names 224 dark / 226 red / 228 green
    blitg(lamp_cell(7, 5), 0, 112, 16)
    blitg(lamp_cell(10, 11), 16, 112, 16)
    blitg(lamp_cell(12, 13), 32, 112, 16)
    tiles = encode_4bpp(sheet, 16, 16)

    def pal_bytes(cols):
        out = bytearray()
        for r, g, b in cols:
            out += struct.pack("<H", ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3))
        return out

    pal = pal_bytes(SKI_PALETTE)
    npc = bytearray()
    for over in NPC_PALETTES:
        npc += pal_bytes([over.get(i, c) for i, c in enumerate(SKI_PALETTE)])
    return tiles, bytes(pal), bytes(npc), sheet


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
    pos, w, h, plte, idat = 8, 0, 0, [], b""
    while pos < len(data):
        ln, tag = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + ln]
        pos += 12 + ln
        if tag == b"IHDR":
            w, h, depth, ctype = struct.unpack(">IIBB", body[:10])
            assert depth == 8 and ctype == 3, "need 8-bit indexed PNG"
        elif tag == b"PLTE":
            plte = [tuple(body[i:i + 3]) for i in range(0, len(body), 3)]
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
    return [list(r) for r in rows], pal


def load_pattern():
    src = os.path.join(ASSETS, "sea_pattern.png")
    if os.path.exists(src):
        pat, palette = decode_png(src)
        size = len(pat)
        assert size == len(pat[0]), "pattern must be square"
        assert 1024 % size == 0 and size % 8 == 0, \
            "pattern size must divide 1024 and be a multiple of 8"
        print("using designed pattern: assets/sea_pattern.png ({0}x{0})".format(size))
        return pat, palette
    return procedural_pattern()


# ---- course: sand islands, shorelines, rope float-lines ----------------------
# palette indices 8-13 (the course block in the colour map)
SAND, SAND_SH, FOAM, WET_SAND, FLOAT_A, SHAL_BLUE, CALM, SHAL_SAND =     8, 9, 10, 11, 12, 13, 14, 15
CHECK_DARK = 48   # start/finish checker black - NOT 32-47, which is the
CHECK_WHITE = 49  # mode-1 sky palette row loaded over CGRAM at boot
COURSE_COLORS = {
    SAND: (232, 214, 164), SAND_SH: (212, 190, 142),
    FOAM: (172, 214, 246), WET_SAND: (186, 164, 118),  # foam = lattice blue
    FLOAT_A: (216, 44, 214),  # magenta: not confusable with R buoys
    CALM: (22, 62, 122),   # flat wake band under ropes (non-rotating)
    # SHAL_BLUE is set at bake time to the lightest deep-water rotation
    # colour (fixed copy, so the shallows don't flow); SHAL_SAND to sand
}


def load_course():
    path = os.path.join(ASSETS, "course.json")
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

    # surf wings: white water up to 3 cells out from every shore, painted
    # with strictly 8-periodic patterns over flat calm blue so each band
    # costs ~one unique tile
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
    SURF_CUT = {1: 12, 2: 6}  # foam density per band
    for cy in range(128):
        for cx in range(128):
            d = dist[cy][cx]
            if d < 1 or d > 2:
                continue
            cut = SURF_CUT[d]
            # super-shallow water: foam over WET sand (the dry-sand-coloured
            # SHAL_SAND read as a light beach strip inside the foam line)
            gap = WET_SAND if d == 1 else SHAL_BLUE
            for py in range(8):
                y = cy * 8 + py
                for px in range(8):
                    x = cx * 8 + px
                    n = ((x & 7) * 13 + (y & 7) * 29 + ((x & 7) * (y & 7))) % 17
                    canvas[y][x] = FOAM if n < cut else gap

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
                    canvas[cy * 8 + py][cx * 8 + px] = CALM
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


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    phases = P["phases"]

    # -- texture + course --
    pat, palette = load_pattern()
    for idx, rgb in COURSE_COLORS.items():
        palette[idx] = rgb
    palette[SHAL_SAND] = palette[SAND]
    palette[CHECK_DARK] = (16, 16, 20)    # start-line checker black
    palette[CHECK_WHITE] = (250, 250, 250) # start-line checker white
    rs, rc = int(P["rotStart"]), int(P["rotCount"])
    palette[SHAL_BLUE] = max((palette[rs + i] for i in range(rc)), key=sum)
    course = load_course()
    if course:
        print("course: assets/course.json ({0} ropes, {1} buoys, {2} waypoints)"
              .format(len(course[1]), len(course[2]), len(course[3])))

    # Start grid derived from the racing line: the four skis line up just
    # behind waypoint 0 facing along the opening segment, player at the
    # back. Exported in world units (the C side derives the camera from the
    # ski slot). Falls back to the historical spawn when there is no path.
    if course and len(course[3]) >= 2:
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

        start_slot = grid_slot(44, 0)
        npc_slots = [grid_slot(8, -28), grid_slot(18, 28), grid_slot(30, -8)]
        start_theta = round(math.atan2(gdx, gdy) * 128 / math.pi) & 255
    else:
        start_slot = (2048, 968)
        npc_slots = [(1950, 1020), (2148, 1084), (2050, 1148)]
        start_theta = 0
    canvas, coll = compose_canvas(pat, course)
    # EXTBG: set bit 7 on course pixels -> they render via BG2-high (above
    # BG1, colour-math-free); colour comes from the low 7 bits so the
    # palette layout is untouched
    # per-PIXEL exemption: anything sand-coloured (beach, wet-sand line,
    # sandy shallows) plus the rope cord and floats escape the glow; foam,
    # pale shallows, calm wake and open water keep the crest highlights
    exempt = (SAND, SAND_SH, WET_SAND, FLOAT_A, SHAL_SAND,
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
    with open(os.path.join(OUT_DIR, "sea.pc7"), "wb") as f:
        f.write(pc7)
    with open(os.path.join(OUT_DIR, "sea.mp7"), "wb") as f:
        f.write(mp7)
    with open(os.path.join(OUT_DIR, "sea.pal"), "wb") as f:
        f.write(pal)
    write_png(os.path.join(OUT_DIR, "sea.png"),
              [bytes(px & 0x7F for px in row) for row in canvas],
              palette)  # preview only (priority bit masked)

    # -- HDMA tables (camera-independent) + raw arrays for the runtime builder --
    asm = ['.include "hdr.asm"', ""]
    externs, inits = [], {"tm": [], "g": []}
    arr_name = {"tm": "waveTM", "g": "waveG"}
    total = 0
    raw_d, raw_a, sky_counts, ski_rows, surf_hs = [], [], [], [], []
    # horizon extremes across the cycle: deepest normalises the gradient,
    # highest (minus a margin, floored to a tile row) sets the mode switch
    horizons = [raycast_phase(2 * math.pi * p / phases)[0]
                for p in range(phases)]
    sky_ref = max(horizons)
    sky_switch = ((min(horizons) - SKY_SAFE) // 8) * 8
    assert UI_LINES + 8 <= sky_switch <= 127, \
        "mode-1 sky band needs UI_LINES+8 <= switch <= 127 (HDMA count)"
    sky_gfx, sky_pal2, sky_rows = build_sky_band(sky_switch, sky_ref)
    assert SKY_CHAR0 + sky_rows <= 256, "sky tiles overflow the char space"
    for p in range(phases):
        phi = 2 * math.pi * p / phases
        tm_tab, g_tab, n_sky = phase_tables(phi, sky_ref, sky_switch)
        tabs = {"tm": tm_tab, "g": g_tab}
        sky_counts.append(n_sky)
        d_words, a_words = phase_raw(phi)
        raw_d += d_words
        raw_a += a_words
        # jet ski: screen row of the rendered surface at skiDist (scan from
        # the bottom; occlusion means the first row at/beyond the distance
        # is the crest the ski visually rides), and the wave height there
        row = SCANLINES - 1
        while row > 0 and d_words[row] < P["skiDist"]:
            row -= 1
        ski_rows.append(row)
        surf_hs.append(round(P["amp"] * math.sin(K_WAVE * P["skiDist"] + phi)))
        asm.append('.section ".wave{0}" superfree'.format(p))
        for name in ("tm", "g"):
            label = "wave_{0}_p{1}".format(name, p)
            total += len(tabs[name])
            asm.append(label + ":")
            asm.append(db_lines(tabs[name]))
            externs.append("extern char {0};".format(label))
            inits[name].append("    {0}[{1}] = (u8 *)&{2};".format(arr_name[name], p, label))
        asm.append(".ends")
        asm.append("")

    # raw distance/scale arrays, all phases contiguous (stride 448 bytes).
    # Both in ONE section: camera.asm reads them with a single data bank.
    asm.append('.section ".wave_raw" superfree')
    asm.append("wave_rawd:")
    asm.append(dw_lines(raw_d))
    asm.append("wave_rawa:")
    asm.append(dw_lines(raw_a))
    asm.append(".ends")
    asm.append("")

    # s0.7 sine table for the camera heading (256 binary degrees)
    sin_bytes = [(round(127 * math.sin(2 * math.pi * i / 256))) & 0xFF
                 for i in range(256)]
    asm.append('.section ".camsin" superfree')
    asm.append("camSinTab:")
    asm.append(db_lines(sin_bytes))
    asm.append(".ends")
    asm.append("")

    # collision byte-map (128x128 cells, 0 water / 1 sand / 2 rope)
    asm.append('.section ".wave_coll" superfree')
    asm.append("wave_coll:")
    asm.append(db_lines(coll))
    asm.append(".ends")
    asm.append("")

    # jet ski sprite sheet (4bpp OBJ tiles) + palette
    ski_tiles, ski_pal, npc_pals, ski_sheet = build_ski_sheet()
    write_png(os.path.join(OUT_DIR, "ski.png"),
              [bytes(r) for r in ski_sheet], SKI_PALETTE + [(0, 0, 0)] * 240)
    asm.append('.section ".skigfx" superfree')
    asm.append("ski_tiles:")
    asm.append(db_lines(ski_tiles))
    asm.append("ski_pal:")
    asm.append(db_lines(ski_pal))
    asm.append("npc_pals:")
    asm.append(db_lines(npc_pals))
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

    # -- rotation: unrolled palette writes with literal colours (no far reads) --
    rot_start, rot_count, rot_frames = P["rotStart"], P["rotCount"], P["rotFrames"]
    rot_colors = []
    for i in range(rot_count):
        r, g, b = palette[rot_start + i]
        rot_colors.append(((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3))

    tick_shift = {1: 0, 2: 1, 4: 2, 8: 3}[P["framesPerPhase"]]

    with open(os.path.join(OUT_DIR, "wavedata.h"), "w", newline="\n") as f:
        f.write("""#ifndef WAVEDATA_H
#define WAVEDATA_H

#include <snes.h>

#define WAVE_PHASES {0}
#define WAVE_TICK_SHIFT {1}
#define WAVE_ROT_COUNT {2}
#define WAVE_ROT_FRAMES {3}
#define WAVE_RAW_STRIDE 448
#define WAVE_PC7_SIZE {{PC7SIZE}}
#define WAVE_BUOY_COUNT {{NBUOYS}}
#define WAVE_PATH_COUNT {{NPATH}}
/* OBJ sheet: byte size, and the wake conveyor cells (16x16, names +2 each,
   one per intensity level) */
#define WAVE_SKI_SHEET {{SHB}}
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
/* start grid (world units, ski positions; heading in binary degrees) */
#define WAVE_START_X {{STX}}
#define WAVE_START_Y {{STY}}
#define WAVE_START_THETA {{STTH}}
#define WAVE_NPC_X0 {{NX0}}
#define WAVE_NPC_Y0 {{NY0}}
#define WAVE_NPC_X1 {{NX1}}
#define WAVE_NPC_Y1 {{NY1}}
#define WAVE_NPC_X2 {{NX2}}
#define WAVE_NPC_Y2 {{NY2}}
#define WAVE_UI_LINES {4}
#define WAVE_BASE_ROLL 64
#define WAVE_STEPS_PER_TEXEL {5}
#define WAVE_PHASE_MASK {6}
#define WAVE_SKI_PPT_Q4 {7}
#define WAVE_SKI_REST_ROW {8}
#define WAVE_SKI_DIST {9}

extern u8 *waveTM[WAVE_PHASES];
extern u8 *waveG[WAVE_PHASES];
extern u8 waveSky[WAVE_PHASES];
extern u8 waveSkiRow[WAVE_PHASES];
extern s8 waveSurfH[WAVE_PHASES];
extern u16 buoyX[WAVE_BUOY_COUNT + 1];
extern u16 buoyY[WAVE_BUOY_COUNT + 1];
extern u8 buoyType[WAVE_BUOY_COUNT + 1];
extern u16 pathX[WAVE_PATH_COUNT + 1];
extern u16 pathY[WAVE_PATH_COUNT + 1];
/* power gates: the buoys again, sorted into racing-line order, with the
   line direction at each (s8, unit * 64) and the segment they belong to */
extern u16 gateX[WAVE_BUOY_COUNT + 1];
extern u16 gateY[WAVE_BUOY_COUNT + 1];
extern u8 gateLeft[WAVE_BUOY_COUNT + 1];
extern s8 gateNx[WAVE_BUOY_COUNT + 1];
extern s8 gateNy[WAVE_BUOY_COUNT + 1];
extern u8 gateWp[WAVE_BUOY_COUNT + 1];

void waveTablesInit(void);
void waveRotateStep(u8 offset);

#endif
""".replace("{{PC7SIZE}}", str(len(pc7))).replace("{{NBUOYS}}",
             str(len(course[2]) if course else 0)).replace("{{NPATH}}",
             str(len(course[3]) if course else 0))
           .replace("{{SHB}}", str(len(ski_tiles)))
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
           .replace("{{STX}}", str(start_slot[0]))
           .replace("{{STY}}", str(start_slot[1]))
           .replace("{{STTH}}", str(start_theta))
           .replace("{{NX0}}", str(npc_slots[0][0]))
           .replace("{{NY0}}", str(npc_slots[0][1]))
           .replace("{{NX1}}", str(npc_slots[1][0]))
           .replace("{{NY1}}", str(npc_slots[1][1]))
           .replace("{{NX2}}", str(npc_slots[2][0]))
           .replace("{{NY2}}", str(npc_slots[2][1])).format(phases, tick_shift, rot_count, rot_frames, UI_LINES,
           round(256.0 * phases / P["wavelength"]), phases * 256 - 1,
           # screen px per world texel at the ski's distance, x2 for drama,
           # in 4.4 fixed point
           round((P["camH"] / (P["skiDist"] ** 2 + P["camH"] ** 2))
                 * ((SCANLINES - 1) / math.radians(P["fovV"])) * 2 * 16),
           SKI_WATERLINE_ROW, round(P["skiDist"])))

    with open(os.path.join(OUT_DIR, "wavedata.c"), "w", newline="\n") as f:
        f.write("/* generated by tools/bake_tables.py - do not edit */\n")
        f.write('#include "wavedata.h"\n\n')
        f.write("\n".join(externs) + "\n\n")
        f.write("u8 *waveTM[WAVE_PHASES];\nu8 *waveG[WAVE_PHASES];\n")
        f.write("u8 waveSky[WAVE_PHASES];\n")
        f.write("u8 waveSkiRow[WAVE_PHASES];\ns8 waveSurfH[WAVE_PHASES];\n\n")
        f.write("u16 buoyX[WAVE_BUOY_COUNT + 1];\nu16 buoyY[WAVE_BUOY_COUNT + 1];\n")
        f.write("u8 buoyType[WAVE_BUOY_COUNT + 1];\n")
        f.write("u16 pathX[WAVE_PATH_COUNT + 1];\nu16 pathY[WAVE_PATH_COUNT + 1];\n")
        f.write("u16 gateX[WAVE_BUOY_COUNT + 1];\nu16 gateY[WAVE_BUOY_COUNT + 1];\n")
        f.write("u8 gateLeft[WAVE_BUOY_COUNT + 1];\n")
        f.write("s8 gateNx[WAVE_BUOY_COUNT + 1];\ns8 gateNy[WAVE_BUOY_COUNT + 1];\n")
        f.write("u8 gateWp[WAVE_BUOY_COUNT + 1];\n\n")
        f.write("void waveTablesInit(void)\n{\n")
        for name in ("tm", "g"):
            f.write("\n".join(inits[name]) + "\n")
        for p, n in enumerate(sky_counts):
            f.write("    waveSky[{0}] = {1};\n".format(p, n))
        for p, r in enumerate(ski_rows):
            f.write("    waveSkiRow[{0}] = {1};\n".format(p, r))
        for p, h in enumerate(surf_hs):
            f.write("    waveSurfH[{0}] = {1};\n".format(p, h))
        if course:
            for i, (bx, by, side) in enumerate(course[2]):
                f.write("    buoyX[{0}] = {1};\n".format(i, (bx * 4) & 4095))
                f.write("    buoyY[{0}] = {1};\n".format(i, (by * 4) & 4095))
                f.write("    buoyType[{0}] = {1};\n".format(i, 1 if side == "R" else 0))
            for i, (px, py) in enumerate(course[3]):
                f.write("    pathX[{0}] = {1};\n".format(i, (px * 4) & 4095))
                f.write("    pathY[{0}] = {1};\n".format(i, (py * 4) & 4095))
            for i, (gx, gy, left, nx, ny, wp) in enumerate(order_gates(course)):
                f.write("    gateX[{0}] = {1};\n".format(i, (gx * 4) & 4095))
                f.write("    gateY[{0}] = {1};\n".format(i, (gy * 4) & 4095))
                f.write("    gateLeft[{0}] = {1};\n".format(i, left))
                f.write("    gateNx[{0}] = {1};\n".format(i, nx))
                f.write("    gateNy[{0}] = {1};\n".format(i, ny))
                f.write("    gateWp[{0}] = {1};\n".format(i, wp))
        f.write("}\n\n")
        f.write("void waveRotateStep(u8 offset)\n{\n    switch (offset)\n    {\n")
        for o in range(rot_count):
            f.write("    case {0}:\n".format(o))
            for i in range(rot_count):
                col = rot_colors[(i + o) % rot_count]
                f.write("        setPaletteColor({0}, 0x{1:04X});\n"
                        .format(rot_start + i, col))
            f.write("        break;\n")
        f.write("    }\n}\n")

    n_sky0 = raycast_phase(0.0)[0]
    cycle = phases * P["framesPerPhase"] / 60.0
    print("baked {0} phases ({1} bytes), wavelength {2:.1f}, cycle {3:.2f}s, "
          "sky lines at phase 0: {4}".format(phases, total, P["wavelength"],
                                             cycle, n_sky0))


if __name__ == "__main__":
    main()
