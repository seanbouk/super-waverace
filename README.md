# Super Waverace

A Mode 7 style SNES game — a wave racer where the sea itself rolls, using per-scanline zoom/position tricks to fake a sinusoidal ocean on hardware that can only rotate and scale a flat plane.

## Status

🌊 Phase 3 — the rolling sea works in Mode 7. See `docs/PLAN.md` for the roadmap
(target: [SNES DEV Game Jam 2026](https://itch.io/jam/snes-dev-game-jam-2026)).

![Rolling sea](docs/rolling-sea.png) ![Rolling sea, later phase](docs/rolling-sea-2.png)

The sea is a per-scanline raycast baked at build time (`tools/bake_tables.py`) into
HDMA tables: `TM` splits sky from sea, `M7A` sets perspective width, and — with the
matrix row zeroed — `M7Y` picks the exact texture row each scanline samples, so near
crests correctly occlude the water behind them. 32 baked phases cycle to roll the
swell (~28KB of ROM, near-zero CPU). D-pad up/down changes the sea speed.

## Colour map (CGRAM)

The line in the sand — update this table whenever an allocation changes.

| Entries | Owner | Notes |
|---------|-------|-------|
| 0 | Backdrop | Sky above the horizon (dusk orange, set at runtime) |
| 1–7 | Water | `1..N` rotating deep stripes (N = rotCount, ≤4), `N+1` peaks, `N+2` lattice |
| 8–15 | Water reserve | Foam / spray variants to come |
| 16–127 | BG reserve | Unallocated (future sky gfx, HUD) |
| 128–255 | Sprites | 8 OBJ palettes × 16 colours — do not touch from BG code |

## Building the ROM

The game (in `game/`) is built with [PVSnesLib 4.6.0](https://github.com/alekmaul/pvsneslib).
Output is `game/superwaverace.sfc` — LoROM, no SRAM, no enhancement chips, per the jam rules.

**Windows (Git Bash):**

```bash
winget install ezwinports.make   # once
./scripts/setup-windows.sh       # once: installs PVSnesLib to ~/pvsneslib + Git Bash patches
export PVSNESLIB_HOME=$HOME/pvsneslib
make -C game
```

**CI:** every push to `main` builds the ROM on Linux, uploads it as a workflow artifact,
and deploys the web player to the `gh-pages` branch.

## Play in the browser

**https://seanbouk.github.io/super-waverace/** — the latest ROM from `main`, running in
[EmulatorJS](https://emulatorjs.org/) (snes9x core). For local testing: build the ROM,
copy it into `web/`, and serve that folder with any static file server.

## Tools

### Wave Visualizer (`tools/wave-visualizer/`)

A self-contained web tool (open `index.html` in a browser) for getting the per-scanline raycast geometry right. Instead of defining the floor directly, each of the SNES's 224 scanlines casts a ray from the camera and records the *first* crossing with a sine wave — a top-to-bottom raycaster, so near crests correctly hide the troughs and waves behind them.

Four charts:

1. **The sea** — the sine wave itself (+x is into the distance, +z in game terms)
2. **The eye** — the 224 rays fanned out from the camera, truncated at their hit points
3. **First crossing** — per scanline, the lowest x where its ray meets the wave (plateaus = hidden water)
4. **SNES display** — the 224 scanlines shaded near→far, with misses as sky

Controls for camera height/pitch/FOV, wave amplitude/wavelength/phase, ray distance, and an animated rolling-sea mode.

**Simplification (deliberate):** the hit's x value is used for both "which part of the wave" and "how far away it is". True texture position (arc length) and true distance (ray length) differ slightly, but occlusion — the effect that matters — comes entirely from the first-crossing rule and stays exact.

**Tuning workflow:** the lab's controls mirror `tools/bake_tables.py` exactly (texel units,
32-phase quantisation, crest glow). Tune the feel, hit **Export wave_params.json**, drop the
file into `tools/`, and `make` — the ROM now matches what the lab showed.

### Water Tile Designer (`tools/water-designer/`)

Designs the sea texture: tileable Perlin noise split into a Wind Waker-style off-white
lattice (the "stringy middles" of the noise) over deep-blue layers, with directional
stripes assigned to rotating palette slots — the SNES rotates those CGRAM entries at
runtime so the water surface flows independently of the swell (fake parallax, one layer).
Live-previews the rotation, counts unique 8×8 tiles (max 256) and colours. Exports
`sea_pattern.png` + `water_params.json` into `assets/`, which the bake script prefers
over its built-in procedural texture.
