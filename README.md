# Super Waverace

A Mode 7 SNES wave racer where the sea itself rolls — per-scanline register tricks
fake a sinusoidal ocean on hardware that can only rotate and scale a flat plane.

## Status

🌊 Playable: a full 3-lap race against three rubber-banded NPC racers — start
grid, 3-2-1-GO countdown, live positions, lap times, chequered start line and
a FINISH banner — on the rolling sea, around an island course with shorelines,
rope float-lines and L/R buoys, collision with slide-along. Verified on real
hardware (pre-race build). See `docs/PLAN.md` "Race mode" for what's left
(gate judging, multi-course) and for important performance findings. Target:
[SNES DEV Game Jam 2026](https://itch.io/jam/snes-dev-game-jam-2026) — LoROM,
≤512KB, no enhancement chips, no SRAM, NTSC+PAL. See `docs/PLAN.md` for history.

![Rolling sea](docs/rolling-sea.png) ![Rolling sea, later phase](docs/rolling-sea-2.png)

**Controls:** hold **B** to accelerate, **Y** to reverse (half power) — thrust only
bites while the hull is in the water (hovercraft scrabble). D-pad **left/right**
steers; turn authority scales with speed (none when stopped). The ski floats on a
buoyancy spring: sitting still it bobs with the swell; at speed it skims crest to
crest, catching air where thrust and steering stop working.

## How the effects work

Mode 7 can only rotate and scale a flat plane — everything below is per-scanline
register trickery driven by HDMA.

**The waves.** Instead of describing the sea surface, we ask, for each of the 224
scanlines: *what is the nearest bit of wave this line's view ray hits?* — a raycaster
turned on its side (top-to-bottom instead of Wolfenstein's left-to-right). Because
only the *first* crossing wins, near crests naturally hide the troughs and waves
behind them. None of this runs on the SNES: `tools/bake_tables.py` raycasts every
scanline for 32 wave phases at build time and bakes the results.

**Putting a hit on screen.** The Mode 7 matrix is set to `B = D = 0`, which makes
the sampled texture position depend only on the per-scanline registers. `TM` flips
between backdrop-only (sky) and BG1 (sea) at the horizon line, which moves per phase.

**The camera / renderer.** Each frame a 65816 routine (`game/camera.asm`) rebuilds
the camera-dependent HDMA streams — `M7A` = a·cos θ, `M7C` = −a·sin θ,
`M7X` = px + d·sin θ, `M7Y` = py + d·cos θ, `HOFS` = M7X−128 — from the baked
per-scanline distance/scale arrays, using the S-CPU hardware multiplier. The build
is sign-specialised (four loop variants, zero branches per scanline), feeds the
multiplier straight from ROM, keeps shared operands latched, runs scratch in a
private direct page, and skips sky lines. Cost: **262 scanlines ≈ 1 frame**
(down from 524 unoptimised), double-buffered in WRAM, pointers flipped in vblank.
The main loop runs at 30Hz — build fills one frame, game logic gets the other
(~45% of the loop spare). Honest simplification: the wave field is defined in view
space, so the swell always rolls toward the camera — turning rotates the texture
but not the wave direction (a world-fixed swell would need a rebake per heading).

**HDMA channel map** (all 8 in use):
ch0 BG mode split (UI band), ch1 TM sky/sea (baked per phase), ch2 COLDATA crest
glow (baked per phase), ch3 M7A+B / ch4 M7C+D / ch5 M7X+Y / ch6 HOFS+VOFS
(paired-register mode-3 streams built each frame; B, D, VOFS ride along as
permanently-zero words), ch7 window waterline (WH0+WH1).

**World scale.** One texture texel spans **4 world units**: raycast, wave field,
physics and speeds all live in world units (the swell's size and feel never change),
while the map's 1024 texels stretch across a 4096-unit world. The scale is applied
at exactly three points: the baked M7A array (÷4) and a mask-and-shift (&4095, >>2)
where the table builder feeds M7X/M7Y.

**Pale crests.** ch2 writes the fixed-colour register (`$2132`) per scanline with
additive colour math enabled on BG1. Wave-top rows get white added (`sin²` falloff
baked in). Whole-row brightening is geometrically correct here: each scanline shows
a single distance, so a crest row is crest all the way across. (Mid-screen CGRAM
writes — the obvious alternative — glitch during active display.)

**Water flow / fake parallax.** Deep-water areas are painted in stripes across
consecutive palette indices; every N frames the CPU rotates those CGRAM entries, so
the surface flows independently of the swell. Stripe colours ping-pong (from → to →
from) so the cycle never hard-jumps, and bands are quantised by phase (not sine
value) so each covers equal area.

**The jet ski and its waterline.** The ski floats at a fixed distance ahead of the
camera; the bake exports, per phase, the surface's screen row at that distance and
the wave height there. Physics is a buoyancy spring toward "a dip under the surface"
(splash damping on entry, gravity when airborne, hard depth floor). Submersion
feedback is the HDMA **window**: below the waterline row, window 1 opens full-width
and masks OBJ, clipping the hull per pixel-row — the waterline rides the sprite as
it bobs, and nearer water swallows it.

**Glow-free land (EXTBG).** Crest glow is colour math on BG1, and land is BG1 — so
Mode 7's hidden second layer exempts it: with EXTBG enabled, BG2 duplicates the
image treating **pixel bit 7** as a per-pixel priority flag (colour = low 7 bits).
Sand-coloured pixels, the rope cord, floats and sandy shallows are baked with bit 7
set, so they win via BG2-high (mode 7 priority: S3 S2 2H S1 BG1 S0 2L) and escape
the glow; foam, pale shallows and open water keep it. Verified on real hardware.

**Collision.** The course zones double as physics: the bake exports a 128×128
collision byte-map (water / sand / rope / buoy; one cell = 32 world units), a tiny
asm probe reads it, and movement resolves one axis at a time at the ski's position —
the blocked axis stops, the other keeps its momentum, so oblique contact slides
along shores and ropes instead of snagging. An embedded ski (rounding creep while
grinding + turning) is actively pushed back to open water.

**Impact splash.** Landing throws two 16×16 plumes out of the hull sides, mirrored
by hflip from one art set, sized by the vertical speed the physics is about to
damp. There is no spray in the air because nothing spawns unless the ski is in
the water, and the plumes anchor to the *water surface row* rather than the hull,
so a thrown splash stays with the water while the ski flies on — which is what
makes bouncing along a swell read as a rhythm of splashes. Free of projection
maths (the ski sits at a fixed screen column), so the whole effect is a timer and
two OAM writes.

**Buoys.** Course markers (yellow L / red R — pass sides not yet enforced) live
twice: a collision cell + painted base ripple in the texture, and a sprite projected
each frame — the distance table gives the screen row, the hardware divider gives the
column. The SNES cannot scale sprites, so each buoy is authored at **five sizes**
(8/12/16/24/32 px) switched at perspective-correct distances — derived from the
player's own ski (32 px at `WAVE_SKI_DIST`) rather than tuned by eye, so an object
level with the player is drawn the same size as the player. Flat-bottomed circles keep a
stable silhouette, every size carries its letter, and all sizes are bottom-anchored
to the surface row so scale swaps never read as movement. A buoy tucked behind a
crest rides up onto the wave in front rather than hiding. Rope floats are magenta
so they never read as R buoys.

**The UI band and the sky.** The screen runs in BG mode 1 from the top down to a
baked switch line just above the wave cycle's highest horizon (HDMA on `$2105`
switches the whole PPU mode mid-frame), giving 3 rows of tiled text plus a tiled
sky: a 16-colour azure gradient with 2D dithering (palette row 2), far smoother
than per-scanline colour math could manage. The few lines between the switch and
the true (moving) horizon stay backdrop-only, shaded by a per-scanline COLDATA
ramp that continues the same gradient, so the seam is invisible. This works
because the M7 HOFS/VOFS HDMA rows above the horizon are pre-zeroed — and those
registers double as BG1's scroll in mode 1, so the sky tiles render unscrolled
for free. The PVSnesLib
console uploads its map to a hardcoded VRAM address inside the Mode 7 region, so
`game/src/ui.c` owns its own map buffer and DMAs it in vblank; the library is still
used for its font and palette. Shows position/heading/speed, build profiler,
WET/AIR and physics state.

**Motion-coupled swell.** Wave phase advances with time *and* forward motion, so
driving fast genuinely skips across crests, and reversing backs the swell down.

**The texture.** One 1024×1024 Mode 7 map (the hardware's only option), from a
128×128 tileable pattern designed in `tools/water-designer/`. Max 256 unique 8×8
tiles (Mode 7 has no tile flipping); when course art pushes the composed map over
budget, the bake's quantiser merges the most-similar tiles — water first (course
tiles never merge with water and cost 8× to merge at all). `game/sea.png` previews
the exact post-quantise data the SNES ships.

## Colour map (CGRAM)

The line in the sand — update this table whenever an allocation changes.

| Entries | Owner | Notes |
|---------|-------|-------|
| 0 | Backdrop | Sky safe strip above the horizon (deep azure, set at runtime; shaded by COLDATA) |
| 1–7 | Water | `1..N` rotating deep stripes (ping-pong colours), `N+1` peaks, `N+2` lattice. N ≤ 5 fits here |
| 8–15 | Course | 8 sand, 9 sand shade (unused), 10 foam (pale blue), 11 wet sand/rope, 12 float magenta, 13 shallow blue, 14 calm wake, 15 shallow sand (unused) |
| 16–31 | UI text | Font palette for the mode-1 text band (palette row 1) |
| 32–47 | Sky band | Palette row 2: the mode-1 sky gradient tiles (loaded over CGRAM at boot — nothing else may live here) |
| 48–49 | Start line | Checker black / true white (glow-exempt) |
| 50–127 | BG reserve | Unallocated |
| 128–143 | Ski + buoys | OBJ palette 0 (shared: ski, buoy yellows/reds, letters) |
| 144–191 | NPC racers | OBJ palettes 1–3: green/purple/orange recolours of the ski palette (tiles shared) |
| 192–255 | Sprites reserve | OBJ palettes 4–7 — do not touch from BG code |

## Building the ROM

Built with [PVSnesLib 4.6.0](https://github.com/alekmaul/pvsneslib) plus Python 3
(the bake step). Output `game/superwaverace.sfc` — LoROM, no SRAM, no chips.

**Windows (Git Bash):**

```bash
winget install ezwinports.make   # once
./scripts/setup-windows.sh       # once: installs PVSnesLib to ~/pvsneslib + Git Bash patches
export PVSNESLIB_HOME=$HOME/pvsneslib
make -C game
```

**CI:** every push to `main` builds the ROM on Linux, uploads it as an artifact, and
deploys the web player to `gh-pages`.

## Play in the browser

**https://seanbouk.github.io/super-waverace/** — the latest ROM from `main`, in
[EmulatorJS](https://emulatorjs.org/) (snes9x core). The page's "download the ROM"
link serves the exact deployed build (handy for flashcarts).

## Tools

All are self-contained web pages — open `index.html` in a browser.

### Wave Visualizer (`tools/wave-visualizer/`)

The geometry lab: 224 rays cast against a sine wave, first crossing wins. Four
charts (the wave, the ray fan, first-crossing per scanline, simulated SNES display
with crest-glow preview). Controls mirror the bake exactly (texel units, phase
quantisation, glow). **Export wave_params.json** → drop into `tools/` → `make`:
the ROM matches the lab.

### Water Tile Designer (`tools/water-designer/`)

Designs the sea texture: tileable Perlin noise split into a Wind Waker-style
off-white lattice over deep-blue layers, with directional stripes assigned to the
rotating palette slots. Independent surface/deep noise controls, rotation preview,
live tile/colour budget. Exports `sea_pattern.png` + `water_params.json` into
`assets/`, which the bake prefers over its procedural fallback.

### Course Painter (`tools/course-painter/`)

The course editor: paint water/sand zones at tile resolution (wrap-aware — maps are
islands on a repeating 4096-unit sea), draw rope float-lines as polylines, place
L/R buoys, and draw the racing line (an ordered waypoint loop — point 0 is the
start/finish; arrows show race direction). Fill tool, undo, tiled 2×2 preview,
budget readouts. Exports/imports `assets/course.json`; the bake composes it over
the water pattern (auto shoreline foam + 2-band shallow surf on the water side),
derives the collision map, and exports the waypoints for lap counting and (soon)
NPC steering.
