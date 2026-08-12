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
UI_LINES = 24        # top band: BG mode 1 text UI (3 tile rows)
SKY_RGB = (248, 168, 96)  # backdrop / palette index 0 (dusk orange)

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


def phase_tables(phi):
    """HDMA tables that stay camera-independent: sky/sea split + crest glow.
    Returns (tm, glow, n_sky)."""
    n_sky, sea_x = raycast_phase(phi)
    assert n_sky >= UI_LINES, \
        "sea reaches into the UI band (n_sky={0} < {1}) - raise camH/pitch".format(
            n_sky, UI_LINES)

    g_entries = []
    for x in sea_x:
        # crest glow: sin() is 1 exactly at wave tops, fades down the flanks
        c = math.sin(K_WAVE * x + phi)
        b = round(P["crestGlow"] * max(0.0, c) ** P["glowGamma"]) if c > 0 else 0
        g_entries.append((0xE0 | min(31, b),))

    # TM: UI band shows BG1 (mode-1 text), then backdrop sky, then BG1 sea
    tab_tm = bytes(repeat_blocks(UI_LINES, 0x11)
                   + repeat_blocks(n_sky - UI_LINES, SKY_TM)
                   + bytearray((0x81, SEA_TM, 0x00)))
    tab_g = hdma_table(n_sky, (0xE0,), g_entries)   # UI band + sky: add zero
    return tab_tm, tab_g, n_sky


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
] + [(0, 0, 0)] * 4

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


def build_ski_sheet():
    """128x96 sheet: player ski frames (straight + lean), buoys at 5 sizes,
    and the NPC rear-view ski at 5 sizes (rows 64+, recoloured by palette).
    128px wide = 16 tiles/row, matching OAM's name-row stride exactly."""
    sheet = [[0] * 128 for _ in range(96)]
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
    tiles = encode_4bpp(sheet, 16, 12)

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
COURSE_COLORS = {
    SAND: (232, 214, 164), SAND_SH: (212, 190, 142),
    FOAM: (250, 250, 244), WET_SAND: (186, 164, 118),
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
    return zones, c.get("ropes", []), c.get("buoys", []), c.get("path", [])


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
            gap = SHAL_SAND if d == 1 else SHAL_BLUE  # super-shallow water
            for py in range(8):
                y = cy * 8 + py
                for px in range(8):
                    x = cx * 8 + px
                    n = ((x & 7) * 13 + (y & 7) * 29 + ((x & 7) * (y & 7))) % 17
                    canvas[y][x] = FOAM if n < cut else gap

    for cy in range(128):
        zrow = zones[cy]
        for cx in range(128):
            if zrow[cx] != "s":
                continue
            coll[cy * 128 + cx] = 1
            n, s_, w, e = water(cy - 1, cx), water(cy + 1, cx), \
                water(cy, cx - 1), water(cy, cx + 1)
            for py in range(8):
                y = cy * 8 + py
                for px in range(8):
                    x = cx * 8 + px
                    # flat pale sand with a sparse speckle
                    c = SAND_SH if ((x & 31) * 7 + (y & 31) * 13) % 29 == 0 else SAND
                    # foam fringe on edges that face water
                    d = 9
                    if n:
                        d = min(d, py)
                    if s_:
                        d = min(d, 7 - py)
                    if w:
                        d = min(d, px)
                    if e:
                        d = min(d, 7 - px)
                    if d <= 1:
                        c = FOAM
                    elif d == 2:
                        c = WET_SAND
                    canvas[y][x] = c

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
        # class 1 = contains course colours (shore foam, sand, ropes, floats)
        classes = [1 if any(8 <= (px & 0x7F) <= 15 for px in t) else 0 for t in tiles]
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
    rs, rc = int(P["rotStart"]), int(P["rotCount"])
    palette[SHAL_BLUE] = max((palette[rs + i] for i in range(rc)), key=sum)
    course = load_course()
    if course:
        print("course: assets/course.json ({0} ropes, {1} buoys, {2} waypoints)"
              .format(len(course[1]), len(course[2]), len(course[3])))
    canvas, coll = compose_canvas(pat, course)
    # EXTBG: set bit 7 on course pixels -> they render via BG2-high (above
    # BG1, colour-math-free); colour comes from the low 7 bits so the
    # palette layout is untouched
    # per-PIXEL exemption: anything sand-coloured (beach, wet-sand line,
    # sandy shallows) plus the rope cord and floats escape the glow; foam,
    # pale shallows, calm wake and open water keep the crest highlights
    exempt = (SAND, SAND_SH, WET_SAND, FLOAT_A, SHAL_SAND)
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
    for p in range(phases):
        phi = 2 * math.pi * p / phases
        tm_tab, g_tab, n_sky = phase_tables(phi)
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

void waveTablesInit(void);
void waveRotateStep(u8 offset);

#endif
""".replace("{{PC7SIZE}}", str(len(pc7))).replace("{{NBUOYS}}",
             str(len(course[2]) if course else 0)).replace("{{NPATH}}",
             str(len(course[3]) if course else 0)).format(phases, tick_shift, rot_count, rot_frames, UI_LINES,
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
        f.write("u16 pathX[WAVE_PATH_COUNT + 1];\nu16 pathY[WAVE_PATH_COUNT + 1];\n\n")
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
