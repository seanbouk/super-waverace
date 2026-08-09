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
SEA_TM = 0x11        # sea lines: BG1 + sprites
UI_LINES = 24        # top band: BG mode 1 text UI (3 tile rows)
SKY_RGB = (248, 168, 96)  # backdrop / palette index 0 (dusk orange)

DEFAULTS = {
    "camH": 34.0, "pitch": -10.0, "fovV": 25.0, "fovH": 60.0,
    "amp": 18.0, "wavelength": 128.0, "maxX": 2048.0,
    "phases": 32, "framesPerPhase": 2,
    "crestGlow": 12, "glowGamma": 2.0,
    # rotation defaults (overridden by assets/water_params.json if present)
    "rotStart": 1, "rotCount": 3, "rotFrames": 8,
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
    tab_tm = bytes(repeat_blocks(UI_LINES, SEA_TM)
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
        a_words.append(max(1, min(0x7FFF, round(x * TAN_HALF_H / 128.0 * 256.0))))
    return d_words, a_words


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


def build_mode7_data(pat, palette):
    """Tile the pattern across the 128x128 map, dedupe 8x8 tiles.
    Returns (pc7 tile bytes, mp7 map bytes, pal bytes)."""
    size = len(pat)
    tiles_across = size // 8
    tile_index = {}
    tiles = bytearray()
    small_map = []  # tile number for each pattern tile
    for ty in range(tiles_across):
        row = []
        for tx in range(tiles_across):
            t = bytes(pat[ty * 8 + py][tx * 8 + px]
                      for py in range(8) for px in range(8))
            if t not in tile_index:
                assert len(tile_index) < 256, "more than 256 unique 8x8 tiles!"
                tile_index[t] = len(tile_index)
                tiles += t
            row.append(tile_index[t])
        small_map.append(row)

    mp7 = bytearray(128 * 128)
    for my in range(128):
        for mx in range(128):
            mp7[my * 128 + mx] = small_map[my % tiles_across][mx % tiles_across]

    pal = bytearray()
    for r, g, b in palette:
        bgr = ((b >> 3) << 10) | ((g >> 3) << 5) | (r >> 3)
        pal += struct.pack("<H", bgr)

    print("texture: {0} unique tiles, {1} bytes".format(len(tile_index), len(tiles)))
    return bytes(tiles), bytes(mp7), bytes(pal)


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

    # -- texture --
    pat, palette = load_pattern()
    pc7, mp7, pal = build_mode7_data(pat, palette)
    with open(os.path.join(OUT_DIR, "sea.pc7"), "wb") as f:
        f.write(pc7)
    with open(os.path.join(OUT_DIR, "sea.mp7"), "wb") as f:
        f.write(mp7)
    with open(os.path.join(OUT_DIR, "sea.pal"), "wb") as f:
        f.write(pal)
    write_png(os.path.join(OUT_DIR, "sea.png"), pat, palette)  # preview only

    # -- HDMA tables (camera-independent) + raw arrays for the runtime builder --
    asm = ['.include "hdr.asm"', ""]
    externs, inits = [], {"tm": [], "g": []}
    arr_name = {"tm": "waveTM", "g": "waveG"}
    total = 0
    raw_d, raw_a, sky_counts = [], [], []
    for p in range(phases):
        phi = 2 * math.pi * p / phases
        tm_tab, g_tab, n_sky = phase_tables(phi)
        tabs = {"tm": tm_tab, "g": g_tab}
        sky_counts.append(n_sky)
        d_words, a_words = phase_raw(phi)
        raw_d += d_words
        raw_a += a_words
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
#define WAVE_UI_LINES {4}
#define WAVE_BASE_ROLL 256
#define WAVE_STEPS_PER_TEXEL {5}
#define WAVE_PHASE_MASK {6}

extern u8 *waveTM[WAVE_PHASES];
extern u8 *waveG[WAVE_PHASES];
extern u8 waveSky[WAVE_PHASES];

void waveTablesInit(void);
void waveRotateStep(u8 offset);

#endif
""".format(phases, tick_shift, rot_count, rot_frames, UI_LINES,
           round(256.0 * phases / P["wavelength"]), phases * 256 - 1))

    with open(os.path.join(OUT_DIR, "wavedata.c"), "w", newline="\n") as f:
        f.write("/* generated by tools/bake_tables.py - do not edit */\n")
        f.write('#include "wavedata.h"\n\n')
        f.write("\n".join(externs) + "\n\n")
        f.write("u8 *waveTM[WAVE_PHASES];\nu8 *waveG[WAVE_PHASES];\n")
        f.write("u8 waveSky[WAVE_PHASES];\n\n")
        f.write("void waveTablesInit(void)\n{\n")
        for name in ("tm", "g"):
            f.write("\n".join(inits[name]) + "\n")
        for p, n in enumerate(sky_counts):
            f.write("    waveSky[{0}] = {1};\n".format(p, n))
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
