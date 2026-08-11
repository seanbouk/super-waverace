# Development Plan

> **Status (Aug 2026): phases 1�3 complete and exceeded** � see README for what
> actually got built. Notable deviations from the plan below: the bake emits
> Mode 7 data directly (gfx4snes dropped, so palette indices stay stable for
> rotation); the camera became a full runtime HDMA table builder (hardware
> multiplier) rather than purely baked tables; EXTBG, a PPU-window waterline,
> colour-math crest glow, courses, collision and buoys were added along the way.

Target: [SNES DEV Game Jam 2026](https://itch.io/jam/snes-dev-game-jam-2026) — submissions close **31 October 2026**.

## Jam constraints (hard requirements)

- **LoROM** only, max **512KB**
- **No enhancement chips** (SA-1, Super FX, etc.)
- **No SRAM**
- Must work on **real hardware**, both **NTSC and PAL**
- All assets original or free-licensed (no rips)
- Judged on: graphics, music/sound, technical execution, controls, gameplay creativity

## Toolchain decision

**PVSnesLib** (v4.6.x) — C library over WLA-DX assembly. Chosen because:

- Jam-recommended, actively maintained, prebuilt Windows binaries
- LoROM output by default, fits all jam constraints
- Batteries included: backgrounds, sprites/OAM, pad input, SPC700 music driver (tracker modules), and a Mode 7 example to crib from
- C for game logic velocity; the per-scanline HDMA/Mode 7 core will be hand-written 65816 assembly since tcc-816's codegen is too slow for hot paths

Local emulator for development: **Mesen 2** (best debugger, event viewer shows HDMA timing per scanline — essential for phase 3). Sanity-check on bsnes for accuracy, and on real hardware via flashcart.

---

## Phase 1 — Hello World ROM

Goal: a building, bootable LoROM that prints text, compiled from this repo.

1. Install PVSnesLib Windows release; wire up its `make` toolchain
2. Create `game/` with the standard PVSnesLib project layout (Makefile, `src/`, `res/`)
3. Hello-world: console init, "HELLO WAVERACE" on BG1, idle loop
4. Verify: correct LoROM header, checksum, runs in Mesen 2
5. GitHub Action that builds the ROM on push (Linux PVSnesLib release in CI) and uploads it as an artifact

## Phase 2 — ROM running in a web page

Goal: the current ROM playable in a browser, locally and on GitHub Pages.

1. **EmulatorJS** (self-hosted, snes9x core) embedded in `web/index.html`
2. Local testing: any static file server
3. GitHub Actions workflow: build ROM → copy into the Pages site alongside the emulator → deploy to GitHub Pages, so every push updates the playable build
4. PAL/NTSC: target NTSC (60Hz) as primary; verify the ROM boots and runs in PAL mode in-emulator (accepted trade-off: PAL runs slower and letterboxed). Avoid frame-count-based logic that would *break* (rather than just slow down) at 50Hz
5. Confirm on real hardware (NTSC console + flashcart)

Note: the web build is a dev/demo convenience — the jam judges on real hardware, so Mesen 2 + flashcart remain the ground truth.

## Phase 3 — The rolling sea in Mode 7

Goal: the wave-visualizer effect (tools/wave-visualizer) running on hardware.

Key insight: **bake the raycast at build time**. The per-scanline math never needs to run on the 65816.

1. Extend the wave visualizer (or add a build script) to **export HDMA tables**: for each of N phase steps of the rolling wave (e.g. 32), 224 per-scanline entries of Mode 7 register values — scale (M7A/M7D) and vertical offset (which map row that scanline samples), straight from the first-crossing raycast
2. ROM budget is a non-issue: 32 steps × 224 lines × ~4 bytes ≈ 28KB of the 512KB
3. Runtime per frame: advance the phase counter and repoint 2–3 HDMA channels at the pre-baked table. Near-zero CPU; the wave rolls for free
4. Sky scanlines (rays that miss): separate HDMA to switch to backdrop colour / sky BG above the horizon line
5. Build order:
   a. Static classic Mode 7 perspective floor via HDMA (the well-trodden F-Zero technique) to prove the pipeline
   b. Swap the perspective table for a wave table exported from the visualizer — crests should now hide troughs
   c. Animate: cycle through the N phase tables per frame
6. Later (post-phase-3): camera height/pitch changes need multiple table banks or runtime table interpolation — deferred until the ship/controls phase

Known hardware quirk to handle: Mode 7 matrix registers are write-twice 8-bit; HDMA tables must be laid out accordingly (two-writes-per-register mode).
