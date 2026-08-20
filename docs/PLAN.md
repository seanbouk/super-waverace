# Development Plan

> **Status (Aug 2026): phases 1-3 complete and exceeded** - see README for what
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

---

## Race mode (Aug 2026): 3-lap race, player + 3 NPC racers

Design decisions (agreed):

- **Progress = racing-line waypoints.** One ordered polyline authored in the
  course painter (`path` in course.json) drives lap counting, position
  ranking (`lap x N + nextWaypoint`, tie-break distance-to-next), NPC
  steering, and start placement. No painted sector map, no extra ROM.
- **NPCs are kinematic followers**, not physics clones: steer along the line
  with per-racer speed/cornering profiles + the collision probe. Visually
  they are "moving buoys with ski art" — the buoy projection path (rowDepth,
  hardware divider, scale ladder, surface-row anchoring, waterline window)
  is reused as-is, so wave bob comes free.
- **Rear-view NPC art only** (4-5 sizes + lean, hflip free). Multi-heading
  art deferred until head-on passes prove it's needed.
- **One course first**; multi-course loader (bake N course.json → tiles/map/
  collision blobs + VRAM reload between races) is the last phase.

Phases (each headless-verifiable in Mesen; ✅ = done):

1. ✅ **Racing line + player progress** — painter path tool, bake exports
   pathX/pathY, next-waypoint/lap/lap-time tracking, and AUTOPILOT upgraded
   from "hold B" to a waypoint chaser (cross-product steering, corner
   throttle release) — deliberately the seed of the NPC brain. Verified:
   autopilot laps continuously, ~750-900 ticks/lap, threads both gates.
2. ✅ **NPCs as moving buoys** — 3 kinematic racers on the line with distinct
   speed profiles, placeholder buoy art, OAM sprites 5-7. Buoy projection
   extracted into projectPoint()/drawLadder() and shared; npcTrig helper in
   camera.asm serves sin/cos from the far-ROM table. Verified: all three
   visible at correct ladder scales from the grid, wrap laps continuously.
   Known gap for phase 4: NPC lap pace (~40-50s) trails a well-driven
   player (~24s) — speeds/rubber-banding are tuning knobs, not bugs.
3. ✅ **NPC ski art** — rear-view ski at all 5 buoy scale bands (no lean
   frames), cropped at the waterline so the bottom-anchored slot sits ON
   the water like the buoys. One tile set, recoloured per racer via OBJ
   palettes 1-3 (green/purple/orange; CGRAM 144-191). Sheet grew to 6144
   bytes, so the UI map moved 0x6800 -> 0x7000 in VRAM.
4. ✅ **Race flow** — countdown (3-2-1-GO, engines locked), live position
   ranking with sub-waypoint tie-breaks, 3-lap finish (FINISH! banner,
   clock frozen, engines cut), race HUD (LAP/POS/clock/last-lap) replacing
   the debug rows (DEBUG_UI define restores them). Schedule rubber-banding:
   each NPC has a fade lap (orange from the gun, purple after lap 1, green
   after lap 2) and picks one of four speed tiers from (should it be
   ahead?) x (how far ahead is it?), with gap caps both ways so leaders
   never run away and faded racers stay in sight. The tiers are PERCENTAGES
   of a ~3s EMA of the player's real forward speed (paceEma) - they
   self-calibrate to any course and driver; fixed numbers broke on the
   first real course redesign. The start grid (player + NPC slots +
   heading) is baked from the racing line's opening segment
   (WAVE_START_*/WAVE_NPC_*), and NPC sprites sit after the buoys
   (NPC_SPR) so buoy-heavy courses cannot collide with them in OAM.
   A checkered start/finish
   strip is baked into the sea texture at path[0] (floats on the swell,
   no collision). Laps count AT the start line (waypoint 0), not the last
   waypoint: lapCount seeds at -1 and the rolling start's first crossing
   makes lap 1, so the opening lap runs the extra grid gap; ranking uses
   monotone progress counters (pProg/npcProg). Post-course-1 additions:
   the world-mirror fix (see CLAUDE.md), doubled player speed (THRUST at
   >>6; overflow-safe >>5/>>2 velocity products), and anti-bunching
   (per-racer lateral aim bias + land-checked pairwise shove, no boids).
   Verified headless: 4TH at the gun, green holds the lead to a genuine
   lap-3 pass, FINISH! 1ST, ~28s laps.

   **Performance findings from this phase (important):**
   - The main loop takes 3-4 vblanks, not 2: the game runs at ~15-20Hz,
     varying with load (sprite-heavy scenes tip 3 -> 4). This PREDATES race
     mode (verified by profiling the pre-race commit).
   - The "30Hz / BUILD 262 lines / 45% spare" story came from a broken
     profiler: tcc silently drops the (void)-cast volatile reads that
     latched OPVCT, so scanline() returned garbage and profLines was
     profFrames*262 = a constant fiction. Fixed by assigning the reads.
     The REAL build cost is ~326 lines; the per-loop C logic is ~317 more.
   - All race timing therefore uses snes_vblank_count (real frames), never
     loop ticks. Anything time-based added later must do the same.
   - The real player pace is ~120 world/s (thrust only bites in water,
     corners coast) - NPC tiers are calibrated to that, not to top speed.
   - Backlog item: restore a fixed-rate loop (likely = move the per-loop C
     hot paths to asm with the hardware multiplier, as camera.asm did for
     the build). Would change all feel tuning - schedule it deliberately.
5. **Gate judging / penalties** (optional) — waypoints already carry the
   gate geometry; check crossing side against buoyType.
6. **Multi-course** — bake N courses, runtime loader, course select.

---

## Performance pass (Aug 2026) + post-jam options

The three planned C->asm ports plus a rowDepth binary search, each verified
bit-exact (dual-run harnesses; rowDepth exhaustively over all 14240 inputs)
and measured as loop ticks over an identical 2000-frame window:

| build                    | loops/2000f | avg vblanks |
|--------------------------|------------|-------------|
| C baseline               | 500        | 4.0         |
| + projectPoint asm       | 554        | 3.6         |
| + ski math asm           | 579        | 3.45        |
| + NPC steering asm       | 620        | ~3.2        |
| + rowDepth binary search | 652        | ~3.07       |

The hot math is gone from C; what remains is control flow, array indexing
and PVSnesLib call overhead (oamSet marshalling is the likeliest next bite,
diminishing returns). A LOCKED 2-vblank/30Hz loop is not reachable with more
of the same on stock hardware - it is SA-1 territory.

**Post-jam chip analysis (agreed direction):**
- **SA-1 is the pick**: same 65816 ISA (camera.asm ports nearly verbatim),
  ~4x clock + parallelism, own multiplier, HDMA can source from BW-RAM.
  1P 60Hz plausible, 2P 30Hz realistic.
- DSP-1 is the wrong tool: its Mode 7 raster command assumes a FLAT plane;
  as a generic math port its I/O overhead cancels the gains. Skip.
- Super FX works (GSU builds the HDMA tables in its own RAM, HDMA reads them
  from the cart bus; 60Hz plausible) but is a second toolchain + big rewrite.

**Two-player split-screen cost sketch** (deferred; no unknowns, ~all pieces
are variants of existing systems): one extra baked viewport table set (both
halves are identical viewports at different rows) + per-player wave phase
accumulators; buildCamTables parameterised per half (camBlk1Ct/SrcOff/DstOff
plumbing half-exists); per-viewport projection bounds with cull margins at
the split (no per-sprite clipping exists - Mario Kart pops at the seam too);
second HUD band via the mode-split HDMA; duplicated player state; P2 sky =
backdrop+COLDATA gradient. Stock = ~15Hz (rough); with SA-1 = 30Hz solid.
