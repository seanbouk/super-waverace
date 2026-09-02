# Super Waverace

A Mode 7 SNES wave racer where the sea itself rolls — per-scanline register tricks
fake a sinusoidal ocean on hardware that can only rotate and scale a flat plane.

## Status

🌊 Playable, with a full game flow. The title screen is the attract mode
(a chaser-driven race on SUNNY ISLAND under the sliding sprite logo);
START opens a menu of **CHAMPIONSHIP** (all six courses in order, points
9/6/3/1, a "… IS CHAMPION" page at the end), **TIME TRIALS** (solo, endless
laps, best-lap HUD) and **ARCADE** (one 3-lap race). Each picks a rider —
Magnus, Callista, Milo, Dafydd, palette-only — then a course from a list
with a baked minimap. Championship races open with a flyover: the camera
cruises the racing line at constant speed under the race number and
course name, one lap or START. Races are 3 laps against three rubber-banded
NPCs — start grid, a sprite start-light tree, live positions, lap times,
chequered start line judged by a true crossing, FINISH banner — on the
rolling sea, around an island course with shorelines, rope float-lines and
L/R buoys (not solid: everyone drives through them). Buoys are judged:
passing each one on its correct side builds a 0–5 power chain (HUD bar)
that scales thrust and top speed from 67% to 133%; a wrong-side pass resets
it to 0 — you earn your speed. When you cross the line a live results
table floats in the sky over the finish-line scene: finishers with a flag
(and their points), the rest in live order, updating as the CPU riders
glide over the line and park ahead of you; PRESS START after 5s. Dithered
clouds drift through the sky with a heading-linked parallax; the racers are
double-height arcade sprites authored in Photoshop with four full rider
palettes; START pauses (B quits to the title). Six courses build today, all
geometric clones of the first with their own skies, water colours, ambient
light and wave profiles — real layouts are the remaining authoring work.
Verified on real hardware up to the Aug-21 build (race, power, gradient HUD,
start tree, clouds); pending a CRT pass: the tall racers / second OBJ table,
the scanline-IRQ mode switch (now two-stage) + sand distance fade, the
rider art, and the BG3 sky text. See `docs/PLAN.md` for the design notes,
performance findings and history. Target:
[SNES DEV Game Jam 2026](https://itch.io/jam/snes-dev-game-jam-2026) — LoROM,
≤512KB, no enhancement chips, no SRAM, NTSC+PAL.

![Rolling sea](docs/rolling-sea.png) ![Rolling sea, later phase](docs/rolling-sea-2.png)

**Controls:** hold **B** to accelerate, **Y** to reverse (half power) — thrust only
bites while the hull is in the water (hovercraft scrabble). D-pad **left/right**
steers; turn authority scales with speed (none when stopped). The ski floats on a
buoyancy spring: sitting still it bobs with the swell; at speed it skims crest to
crest, catching air where thrust and steering stop working. Pass **left of L
buoys and right of R buoys** to build the 0–5 power chain (the PW bar): each
correct pass raises your thrust and top speed, one wrong-side pass drops you
back to 0. Menus everywhere: d-pad, **START/A** confirm, **B** back;
**START** in a race pauses.

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
Sand-coloured pixels, the rope cord, floats and the teal clear-water shallows are
baked with bit 7 set, so they win via BG2-high (mode 7 priority: S3 S2 2H S1 BG1
S0 2L) and escape the glow; foam, pale shallows and open water keep it. Verified
on real hardware.

**Collision.** The course zones double as physics: the bake exports a 128×128
collision byte-map (water / sand / rope / buoy; one cell = 32 world units), a tiny
asm probe reads it, and movement resolves one axis at a time at the ski's position —
the blocked axis stops, the other keeps its momentum, so oblique contact slides
along shores and ropes instead of snagging. An embedded ski (rounding creep while
grinding + turning) is actively pushed back to open water.

**Wake spray.** Only ~24 world units of water are visible between the ski and the
bottom of the screen, and the ski covers half that per loop — so a one-shot splash
pinned to the water is gone before you can see it. Instead the wake is a
**conveyor**: two columns of 16×16 dithered cells spanning the hull, a static
source cell pinned at the waterline and the rest scrolling down at a rate taken
from your speed. Every whole-cell advance shifts an intensity ladder and writes a
new value at the top — dry below a churn threshold (so idling, reversing and
airborne throw nothing), denser with speed above it, and a peak burst on a hard
landing (injected immediately, so it appears with the impact and then travels back
down the band). Two details keep it honest: the scroll absorbs the frame-to-frame
movement of the waterline, so the trailing foam stays planted in the water while
the swell bobs the ski; and a minimum drain rate keeps the ladder advancing while
anything is left on it, so stopping washes the wake away instead of freezing it
(the scroll drives the whole inject cycle — at zero speed it would otherwise never
clear). Because the band is always populated, the ~17 Hz loop stops mattering: it
reads as continuous churn rather than an object to track. The OBJ window is
bounded to the hull's submerged rows so this area is drawable at all — see
`buildWinTab`.

**Buoys.** Course markers (yellow L / red R — judged by the power system) live
twice: a collision cell + painted base ripple in the texture, and a sprite projected
each frame — the distance table gives the screen row, the hardware divider gives the
column. The SNES cannot scale sprites, so each buoy is authored at **five sizes**
(8/12/16/24/32 px) switched at perspective-correct distances — derived from the
player's own ski (32 px wide at `WAVE_SKI_DIST`) rather than tuned by eye, so an
object level with the player is drawn the same size as the player. Flat-bottomed
circles keep a stable silhouette, every size carries its letter, and all sizes are
bottom-anchored to the surface row so scale swaps never read as movement. A buoy
tucked behind a crest rides up onto the wave in front rather than hiding. Rope
floats are magenta so they never read as R buoys. The racers themselves are
**double height** (32×64 for the player — arcade proportions): each is two
vertically stacked sprites sharing one projection, living in a second OBJ name
table at VRAM freed by moving the UI/HUD/cloud data into the previously unused
0x4000 bank — and since sprite-vs-sprite priority is purely OAM order, the NPC
sprite pairs are dealt out nearest-first every frame so passing racers stack
correctly.

**The UI band and the sky.** The screen runs in BG mode 1 from the top down to a
baked switch line just above the wave cycle's highest horizon (a scanline timer
IRQ, fired into hblank, switches the whole PPU mode mid-frame — this freed an
HDMA channel to repaint the dry sand's palette entry per scanline, so the beach
runs a full lit range — richer and darker up close, paler and washed toward
the horizon — while ignoring the swell entirely:
the sand's fixed light divorces land from the breathing water), giving a 4-row
HUD band plus a tiled
sky: a 16-colour azure gradient with 2D dithering (palette row 2), far smoother
than per-scanline colour math could manage. The HUD (TIME / RANK / LAP /
SPEED / POWER titles over double-height values, the power chain as filled
diamond pips) keeps row 0 and columns 0/31 empty for CRT overscan, and its text colour sweeps yellow→green through
the titles, green→yellow through the values' top half and yellow→red through
their bottom half — per scanline, with no HDMA channel to spare: every glyph
pixel *row* carries its own palette index, so three static 8-colour CGRAM
ramps do the per-line colouring for free. The few lines between the switch and
the true (moving) horizon stay backdrop-only, shaded by a per-scanline COLDATA
ramp that continues the same gradient, so the seam is invisible. This works
because the M7 HOFS/VOFS HDMA rows above the horizon are pre-zeroed — and those
registers double as BG1's scroll in mode 1, so the sky tiles render unscrolled
for free. Clouds ride BG3 (2bpp) over the gradient: a strip of dithered
cumulus whose horizontal scroll is simply the camera heading ×4 — the 256px
map wraps exactly four times per full turn, a perfect parallax loop for one
register write per frame (made by the scanline IRQ at the band's edge, so
the band's own BG3 rows — the intro card's text — stay put). The same
layer carries the in-race results table and the intro card in a baked 2bpp
sky font: white with a 1px zenith-coloured edge, floating over any sky. (They were BG2 for a day, which real hardware
punished: EXTBG — on all frame for the sea's priority layer — mangles BG2's
fetches outside mode 7, an effect no emulator models.) The PVSnesLib
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

## Display modes: what is on screen where

Every screen is one of two recipes — the **race screen** (mode 7 sea under a
mode 1 band, switched mid-frame) or a **full-screen mode 1 page** — by
decision: no third layout exists. The race screen, top to bottom:

| Lines | PPU mode | Layers on (TM) | What they carry |
|---|---|---|---|
| 0–31 | 1 | BG1 + BG3 + OBJ (`0x15`) | **HUD band.** BG1 (4bpp, map rows 0–3): the gradient HUD font (race), or console-font menu text (title PRESS START, main menu, PAUSED). Its blank tile is *solid* colour 15 = the course zenith, so the band is one flat colour on every sky. BG3 (2bpp): blank, except the championship intro card's three lines in the sky font — the band's BG3 scroll is held at 0 by the frame-top NMI. OBJ: the title logo (two words of 32×32 sprites), the start-light tree as it floats away. |
| 32–87 | 1 | BG1 + BG3 + OBJ (`0x15`) | **Sky.** BG1: the baked 16-colour gradient tiles (palette row 2, map rows 4–11), paled toward the horizon by a per-line COLDATA add. BG3: the cloud strip (map rows 5–8, priority bit so it draws over the gradient), scrolled by the heading ×4 px — written by the stage-0 scanline IRQ at line 31, so it never reaches the band; or, from the player's finish, the live results table on those same rows plus PRESS START on row 10, at scroll 0. |
| 88 → the wave horizon | 1 | OBJ only (`0x10`) | **Safe strip.** Backdrop only, the same COLDATA ramp continuing the gradient, so the switch line is invisible however high the swell. |
| horizon → 224 | 7 (EXTBG) | BG1 + BG2 + OBJ (`0x13`) | **The sea.** BG1 is the Mode 7 plane, its matrix and scroll rewritten per scanline by HDMA (the rolling waves). BG2 is EXTBG's copy of it: course pixels baked with bit 7 render through BG2-high, above the glow's colour math. OBJ: racers, buoys, spray, with window 1 masking the hull's submerged rows per line (HDMA) so the ski sits *in* the water. Colour math adds the fixed colour to BG1 + backdrop (crest glow); HDMA repaints the sand entry down the frame (distance fade). |

The mode switch is a scanline timer IRQ that fires **twice** per frame
(`camera.asm` `irqSwitch`): stage 0 at line 31 writes the cloud rows' BG3
scroll — in the active part of line 32, a blank BG3 row, clear of the
hblank HDMA burst that could split the write-twice pair — and stage 1 at
line 87 waits for hblank and writes BGMODE 7. The NMI callback restores
mode 1 (+ BG3 priority, `0x09`) and BG3 scroll 0 at the top of every
frame. All eight HDMA channels are spoken for: ch0 CGRAM (sand fade), ch1
TM (the band/sky/strip/sea split above), ch2 COLDATA (glow + sky ramp),
ch3–6 the Mode 7 matrix/centre/scroll pairs, ch7 the OBJ window edges.

**Full-screen mode 1 pages** — rider select, course select, the champion
page (and the old placeholder page) — park the timer IRQ, switch HDMA off
and run mode `0x09` all frame with BG1 + BG3 + OBJ, `$210D` and COLDATA
reset. BG1 shows the same map: rows 0–3 (the band) and 4–11 (the sky
tiles) exactly as the race does, and console-font text only in rows 12
and below — rows the race's mode switch never shows, so menus and races
share the map with no cleanup. BG3 keeps the clouds (the course select
drifts them idly); OBJ carries the tall rider sprites (rider select, the
champion page) or is hidden. Never BG2 during mode-1 lines: with EXTBG on,
real hardware fills it with garbage that no emulator reproduces. `$2106`
mosaic on BG1+2+3 sweeps between every pair of states.

## Colour map (CGRAM)

The line in the sand — update this table whenever an allocation changes.

| Entries | Owner | Notes |
|---------|-------|-------|
| 0 | Backdrop | Sky horizon minus the strip's COLDATA add: the safe strip above the horizon lands on `sky_horizon`. Per course |
| 1–7 | Water | `1..N` rotating deep stripes (ping-pong colours), `N+1` peaks, `N+2` lattice. N ≤ 5 fits here |
| 8–15 | Course | 8 sand, 9 sand shade (unused), 10 foam (pale blue), 11 wet sand/rope, 12 float magenta, 13 shallow blue, 14 calm wake, 15 shallow sand (unused) |
| 16–31 | UI text | Font palette for the mode-1 text band (palette row 1); 29–30 = BG3 cloud white/shade (2bpp group 7), 31 = the solid blank tile's colour = HUD/menu backdrop = sky zenith. Per course |
| 32–47 | Sky band | Palette row 2: 33–47 = the band's 15 anchors, zenith → horizon (32 unused). Per course (`sky`/`sky_horizon`; nothing else may live here) |
| 48–49 | Start line | Checker black / true white (glow-exempt) |
| 50 | Shore teal | Sandy seafoam teal: the shallows tile's land edge, dithering into the bluer teal (glow-exempt) |
| 51–63 | BG reserve | Unallocated |
| 64–111 | HUD ramps | Palette rows 4–6: the gradient-font colour ramps (titles / value tops / value bottoms) |
| 112–127 | Minimap | Palette row 7: the course-select minimap (per-course water/sand/shore + buoy dots) |
| 128–143 | Rider + spray | OBJ palette 0: the player's role slots (neutrals, skin/clothing/jetski pairs, 13 = spray shade). Per course (ambient-lit) |
| 144–191 | NPC racers | OBJ palettes 1–3: full rider recolours (skin + two clothing pairs + jetski pair; tiles shared). Per course (ambient-lit) |
| 192–207 | Start lamps | OBJ palette 4: the start-tree lamps — self-lit, exempt from the ambient |
| 208–223 | Buoys | OBJ palette 5: red / warm-yellow pairs + outline/white at the rider slot numbers. Per course (ambient-lit) |
| 224–239 | Title: WAVERACER | OBJ palette 6 (`assets/title-waveracer.png`, up to 15 colours) |
| 240–255 | Title: Super | OBJ palette 7 (`assets/title-super.png`, white = transparent) |

Entries 1–15 and 48–51 are also per course. A course's `course.json` may carry a
`palette` (role → `#rrggbb`: sand, sand_shade, foam, wet_sand, float, calm, teal,
teal_sand, check_dark, check_white, the sand fade's `sand_far`/`sand_deep`, and
`sky` / `sky_horizon` — the band's top and bottom colours; the horizon defaults to
zenith + the strip's white add, and any authored horizon needs every channel ≥ 112
so the mode-7 strip above the waterline still matches) and an `ambient` RGB multiplier (`#ffffff` =
neutral) that the bake applies to every in-world colour — course, water, sand
fade, riders, buoys, spray, clouds — at zero runtime cost. The HUD ramps and the
start lamps are exempt; the sky is authored directly rather than multiplied.

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
budget readouts, and the course's **palette + ambient light** (colour swatches
per palette role, an ambient multiplier previewed live on the map). Exports/imports
`course.json` (drop it in `assets/courses/<nn>_<name>/`); the bake composes it over
the water pattern (auto shoreline foam + 2-band shallow surf on the water side),
derives the collision map, tints everything by the ambient, and exports the
waypoints for lap counting and NPC steering.
