#!/usr/bin/env python3
"""Bake-time generator for the Mode 7 rolling sea.

Ports the raycast from tools/wave-visualizer into pre-computed HDMA tables:
for each wave phase, each of the 224 scanlines gets
  - M7A  : horizontal texel-per-pixel scale (8.8 fixed) ~ perspective width
  - M7Y  : which texture row that scanline samples = first ray/wave crossing
           (with M7B=M7C=M7D=0 the sampled row is EXACTLY the M7Y value)
  - TM   : main-screen designation - backdrop-only for sky lines, BG1 for sea

Also generates the (original, procedural) sea texture as an indexed PNG for
gfx4snes to convert to Mode 7 tiles/map/palette.

Outputs (into game/): sea.png, wavetables.asm, wavedata.c, wavedata.h

World units are texels of the 1024x1024 Mode 7 map.
"""

import math
import os
import struct
import zlib

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "game")

# ---- tunables -----------------------------------------------------------
SCANLINES = 224
PHASES = 32          # baked wave phase steps per full cycle
CAM_H = 34.0         # camera height above mean sea level (texels)
PITCH = -10.0        # camera pitch, degrees (negative = looking down)
FOV_V = 25.0         # vertical fov, degrees (ray fan across 224 scanlines)
FOV_H = 60.0         # horizontal fov, degrees (sets M7A scale)
AMP = 18.0           # wave amplitude (texels)
WAVELEN = 128.0      # wavelength (texels) - MUST divide 1024 for clean wrap
MAX_X = 2048.0       # cap on ray distance (world x); beyond this = sky
SKY_TM = 0x10        # sky lines: sprites + backdrop only
SEA_TM = 0x11        # sea lines: BG1 + sprites
SKY_RGB = (248, 168, 96)  # backdrop / palette index 0 (dusk orange)

TAN_HALF_H = math.tan(math.radians(FOV_H) / 2)


# ---- raycast (same math as the web visualizer) --------------------------

def wave_y(x, phi):
    return AMP * math.sin(2 * math.pi * x / WAVELEN + phi)


def cast(angle, phi):
    """First crossing of ray y = CAM_H + x*tan(angle) with the wave.
    Returns world x of the hit, or None (sky)."""
    t = math.tan(angle)

    def above(x):
        return (CAM_H + x * t) - wave_y(x, phi)

    if above(0.0) <= 0.0:
        return 0.0
    x_prev = 0.0
    x = 0.25
    while x <= MAX_X:
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
        x += max(0.25, x * 0.01)  # adaptive step: fine near, coarse far
    return None


def raycast_phase(phi):
    """224 scanline hits, top to bottom. Returns (n_sky, [x per sea line])."""
    hits = []
    for i in range(SCANLINES):
        frac = i / (SCANLINES - 1)
        ang = math.radians(PITCH + FOV_V / 2 - frac * FOV_V)
        hits.append(cast(ang, phi))
    # Misses are geometrically a contiguous top block; enforce it for safety.
    n_sky = 0
    while n_sky < SCANLINES and hits[n_sky] is None:
        n_sky += 1
    sea = [h if h is not None else MAX_X for h in hits[n_sky:]]
    return n_sky, sea


# ---- HDMA table encoding -------------------------------------------------

def hdma_table(n_repeat, repeat_bytes, line_entries):
    """Build an HDMA table: n_repeat lines holding repeat_bytes (repeat mode),
    then per-line data (continuous mode) from line_entries. Terminated."""
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


def phase_tables(phi):
    n_sky, sea_x = raycast_phase(phi)

    a_entries, y_entries = [], []
    for x in sea_x:
        a = max(1, min(0x7FFF, round(x * TAN_HALF_H / 128.0 * 256.0)))
        v = round(x) & 0x03FF
        a_entries.append((a & 0xFF, a >> 8))
        y_entries.append((v & 0xFF, (v >> 8) & 0x1F))

    tab_a = hdma_table(n_sky, (0, 1), a_entries)   # sky value: scale 1, unseen
    tab_y = hdma_table(n_sky, (0, 0), y_entries)
    # TM only needs one data entry after the sky block - it holds after that.
    tab_tm = hdma_table(n_sky, (SKY_TM,), [(SEA_TM,)])
    return tab_a, tab_y, tab_tm


# ---- sea texture ----------------------------------------------------------

def build_texture():
    """1024x1024 indexed image from a 64x64 periodic ripple pattern
    (max 64 unique 8x8 tiles - well within Mode 7's 256)."""
    P = 64
    pat = [[0] * P for _ in range(P)]
    tau = 2 * math.pi
    for y in range(P):
        for x in range(P):
            # periodic ripple field (all frequencies divide the tile size)
            v = (math.sin(tau * x / 64 + 1.8 * math.sin(tau * y / 32))
                 + 0.7 * math.sin(tau * (x + y) / 32)
                 + 0.5 * math.sin(tau * y / 64 + 1.2 * math.sin(tau * x / 16)))
            # v in roughly [-2.2, 2.2] -> water indices 1..4
            if v < -0.9:
                c = 1
            elif v < 0.3:
                c = 2
            elif v < 1.3:
                c = 3
            else:
                c = 4
            # sparse foam flecks on the brightest ridges
            if v > 1.9 and (x * 7 + y * 13) % 5 == 0:
                c = 5
            pat[y][x] = c

    rows = [bytes(pat[y % P][x % P] for x in range(1024)) for y in range(1024)]

    palette = [(0, 0, 0)] * 256
    palette[0] = SKY_RGB              # backdrop = sky (never used in the art)
    palette[1] = (8, 40, 96)          # deep
    palette[2] = (16, 72, 144)        # mid
    palette[3] = (40, 112, 192)       # light
    palette[4] = (88, 168, 224)       # crest
    palette[5] = (232, 248, 255)      # foam
    return rows, palette


def write_png(path, rows, palette):
    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        return c + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    w, h = len(rows[0]), len(rows)
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 3, 0, 0, 0)
    plte = b"".join(bytes(c) for c in palette)
    raw = b"".join(b"\x00" + r for r in rows)
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


def main():
    os.makedirs(OUT_DIR, exist_ok=True)

    rows, palette = build_texture()
    write_png(os.path.join(OUT_DIR, "sea.png"), rows, palette)

    asm = ['.include "hdr.asm"', ""]
    externs, inits_a, inits_y, inits_tm = [], [], [], []
    total = 0
    for p in range(PHASES):
        phi = 2 * math.pi * p / PHASES
        tab_a, tab_y, tab_tm = phase_tables(phi)
        total += len(tab_a) + len(tab_y) + len(tab_tm)
        asm.append('.section ".wave{0}" superfree'.format(p))
        for name, tab in (("a", tab_a), ("y", tab_y), ("tm", tab_tm)):
            label = "wave_{0}_p{1}".format(name, p)
            asm.append(label + ":")
            asm.append(db_lines(tab))
        asm.append(".ends")
        asm.append("")
        for name, lst in (("a", inits_a), ("y", inits_y), ("tm", inits_tm)):
            label = "wave_{0}_p{1}".format(name, p)
            externs.append("extern char {0};".format(label))
            lst.append("    wave{0}[{1}] = (u8 *)&{2};"
                       .format(name.upper() if name != "tm" else "TM", p, label))

    with open(os.path.join(OUT_DIR, "wavetables.asm"), "w", newline="\n") as f:
        f.write("\n".join(asm))

    with open(os.path.join(OUT_DIR, "wavedata.h"), "w", newline="\n") as f:
        f.write("""#ifndef WAVEDATA_H
#define WAVEDATA_H

#include <snes.h>

#define WAVE_PHASES {0}

extern u8 *waveA[WAVE_PHASES];
extern u8 *waveY[WAVE_PHASES];
extern u8 *waveTM[WAVE_PHASES];

void waveTablesInit(void);

#endif
""".format(PHASES))

    with open(os.path.join(OUT_DIR, "wavedata.c"), "w", newline="\n") as f:
        f.write("/* generated by tools/bake_tables.py - do not edit */\n")
        f.write('#include "wavedata.h"\n\n')
        f.write("\n".join(externs) + "\n\n")
        f.write("u8 *waveA[WAVE_PHASES];\nu8 *waveY[WAVE_PHASES];\nu8 *waveTM[WAVE_PHASES];\n\n")
        f.write("void waveTablesInit(void)\n{\n")
        f.write("\n".join(inits_a) + "\n")
        f.write("\n".join(inits_y) + "\n")
        f.write("\n".join(inits_tm) + "\n")
        f.write("}\n")

    n_sky0 = raycast_phase(0.0)[0]
    print("baked {0} phases, {1} bytes of HDMA tables, sky lines at phase 0: {2}"
          .format(PHASES, total, n_sky0))


if __name__ == "__main__":
    main()
