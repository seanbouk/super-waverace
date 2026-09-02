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
5. ✅ **Gate judging → the power system** — every buoy is a judged gate:
   pass on its correct side for +1 power (0-5), wrong side resets to 0, and
   thrust/top speed scale 67%..133% with power (power 3 = the old fixed
   feel; you start at 0 and earn your speed). Detection is a sign flip of
   the along-track dot product against the buoy's perpendicular — immune to
   tunnelling at any speed — with the buoy as a LIMIT, not a target: any
   lateral distance counts, only the side matters. The bake orders buoys
   along the racing line and lints labels against the side the line really
   passes. HUD 5-cell power bar. Verified headless: autopilot chains to 5,
   resets only at genuinely tight buoys, gates advance in strict order
   across laps; loop cost ~1% (652 -> 645 ticks/2000 frames).
   NOT yet rebalanced: NPC pace tiers ride paceEma so they self-adjust, but
   the race schedule assumed the old constant player speed — revisit.
6. **Multi-course** — bake N courses, runtime loader, course select.

---

## Multi-course (Aug 2026) — agreed design

Targets: **3 courses minimum, 5 better, 8 is the wish**; **wave params
per course is a hard requirement** (each course names a wave profile;
identical profiles dedupe in ROM, so 8 unique swells is an authoring/bytes
choice, not an architecture one). Course select opens the game as a plain
text list for now (mini-map maybe later; attract mode exists as a separate
future idea — no mode-7 flyover at track select).

Locked-in design decisions:
- **The camera is global** (camH/pitch/fovV/fovH, skiDist) — never
  per-course. The a-from-d synthesis, sprite scale bands, ski row and
  projection constants all assume it; the bake asserts it.
- **Wave profiles pool**: per-course amp/wavelength/phases/framesPerPhase/
  crestGlow/glowGamma + water texture/rotation. The mode-switch line
  (WAVE_SKY_SWITCH) is the min across all courses' horizons — the roughest
  course (probably the current one) sets the sky band for everyone.
- **Palette roles are fixed, RGB varies**: courses recolour by CGRAM index
  (8 = sand, 15 = teal, ...), never by re-arting tiles. The sand-fade
  table, glow exemption and EXTBG bits survive untouched.
- **Per-course ambient light**: an RGB multiplier in course.json applied
  AT BAKE to every in-world palette — course colours, sand fade, all four
  rider OBJ palettes, buoys, spray — so sunset/overcast maps dim the world
  with zero runtime cost. Exempt: HUD text (readability) and the start
  lamps (self-lit). The bake lints 5-bit shade-pair collapse under dark
  ambients. Buoys move to their own OBJ palette (row 5) first — today they
  alias the player's clothing-B/accent slots.
- **On-disk structure**: assets/courses/<nn>_<name>/course.json (palette +
  ambient + wave-profile name embedded; optional per-course
  sea_pattern.png), wave profiles as wave-lab exports. Current
  assets/course.json becomes course 01.

ROM maths (512K jam cap): fixed content ~175K; per course ~37K raw
(16K tiles + 16K map + 4K packed collision + pal/fade) minus map RLE
(open water crushes); per unique wave profile ~12K (delta-d ~7K + phase
HDMA tables ~5K; a is synthesised). 8 courses + 8 profiles ≈ ~465K.
The bake grows a byte-budget report; calm 16-phase profiles cost half.

Phases (1-4 done):
1. **Decouple** ✓ — wave d/a to WRAM $7F (waveRawLoad expands: delta-d +
   PPU-multiplier a synthesis), collision packed 2bpc, course geometry
   (counts/start/grid/gates) runtime. Each step verified bit-exact
   (tickshot/tabdump harnesses; exhaustive collision checksum).
2. **State machine** ✓ — SELECT (full-screen mode 1, console font, IRQ
   parked, HDMA off, $210D/COLDATA reset) -> RACE -> RESULTS (START
   returns) -> SELECT; raceInit() owns ALL race BSS (the latent
   read-before-write list is fixed - only tcc's $16/17 scratch flags
   remain, benign); two-race harness proves race 2 == race 1 bit-exact.
3. **Multi-bake** ✓ — course folders, map codec (copy-from-16-back; RLE
   loses to the water texture's tile period) + asm decoder, wave-profile
   pool with dedupe (calm 16-phase profile = 6K, half the rough one),
   generated loaders (courseGeom/waveProfLoad/courseNameTo) + runtime
   courseLoad(), byte-budget report. Verified: WRAM/VRAM byte-exact vs
   the bake, two-race flow bit-identical to the pre-restructure build,
   ISLAND + LAGOON selectable. ~29K/course + ~6-12K/profile.
   (Aug 28: five courses - SUNNY ISLAND, SUNSET COVE, GREY LAKE, DEEP
   BLUE, DAWN COAST - all clones of course 1 with their own palette, sky,
   ambient, wave profile and, for two, a recoloured sea pattern. The 256K
   ROM is ~98% full: bump to 512K in hdr.asm before course 6.)
4. **Palette + ambient per course** ✓ — course.json `palette` (role ->
   #rrggbb: the ten course CGRAM roles + the sand fade's far/deep ends)
   and `ambient` (#rrggbb multiplier). The bake tints entries 1-15 +
   48-51, the sand-fade HDMA table and OBJ palettes 0-3 (riders + spray)
   per course; buoys moved to their own OBJ palette 5 (CGRAM 208) so they
   stop aliasing the player's pairs; courseLoad uploads 128 bytes at 128
   and 32 at 208 (lamps at 192 and the HUD stay lit). The sky is per
   course too (`sky` = zenith -> CGRAM 0 + the 16 band anchors at 32; the
   BG3 cloud pair 29-30 rides the ambient). Shade-pair lint per palette. Painter grew
   a Palette & light group with live map preview; export writes only
   non-default values. Verified: ISLAND (no palette/ambient) bakes
   byte-identical to the phase-3 build (every blob + fade + the OBJ block
   == old ski_pal+npc_pals, sky == old sky_pal2); LAGOON is a sunset
   (orange zenith, #ffb080 ambient, warm sand, red floats) - the first
   styled course, screenshot-checked. ~+320 bytes/course.
5. **Content** — author courses 2..N; per-course gate lint + NPC pace
   check ride along. (Aug 29: six clone courses with distinct looks -
   SUNNY ISLAND, SUNSET COVE, GREY LAKE, DEEP BLUE, DAWN COAST, TWILIGHT
   SKY; layouts still to author. A low-gravity/high-bounce "Mars" course
   was built, measured and removed the same day - see CLAUDE.md "Mars
   experiment" for what was learned before trying big air again.)

### ROM budget (Aug 29) — what was done and the options kept for later

Measured before the work: 252K of a 256K LoROM, courses 149K (60%) of
which tiles 80K / maps 48K / collision 20K; profiles 29K; code+lib ~54K;
shared gfx 18K. Facts that drove the decisions: only 266 DISTINCT tiles
exist across all courses' 16K tile sets (one water pattern + 8-periodic
shore/rope patterns); 74% of a real course's map cells are the pure water
pattern; clone courses differ only by palette.

Done:
- **512K LoROM** (hdr.asm .ROMBANKS 16 / ROMSIZE $09) - the jam cap.
- **Cross-course tile pool**: ROM holds the union of distinct tiles once
  (`tile_pool`, asserted <= one 32K bank = 512 tiles); each course stores a
  256-entry u16 pool-id table (512 bytes, not 16K) and `tilesTo7F`
  (camera.asm) assembles the 16K set in the $7F8000 buffer at load
  (index table staged at $7FC000). Identical per-course blobs (collision,
  OBJ palettes...) are emitted once and shared by label. Verified: the
  loaded VRAM tile + map planes are byte-identical to the bake and to the
  pre-pool build. Result: 6 courses = 66K + 16K pool (was ~30K each).

- **Map codec: pattern + sparse overrides** (Option B, done Aug 29): a
  256-byte default block (per (row & 15, col & 15): the commonest tile id)
  plus skip/literal tokens over the 16384 cells; mapTo7F fills from the
  default (staged at $7FC200) then applies the stream. Island map 9782 ->
  4998 bytes; a clone course is now ~6K. Verified: decoded VRAM map plane
  byte-identical to the bake and to the old codec's output.

Kept for later, in the order they pay off:
- **C. Collision derived from the map** (class-per-tile table, 64 bytes
  instead of 4K; classify 16384 cells at load). REJECTED for now by the
  user: collision is going to need authoring control beyond "what the
  tile is" once gameplay testing starts. Revisit only if 4K/course ever
  matters (it does not at 512K).
- **D. Synthesise the crest-glow HDMA tables at load** from `d` (already
  in WRAM) + a 256-entry sin^gamma table, into the free $7FC000+ WRAM
  (16K). ~5K per profile (~40K at 8 profiles), and the same trick covers
  a two-player second-viewport table set. Needs the bit-exact discipline
  (bake computes glow through the same quantised table). Riskiest.
- **E. Authoring: keep most profiles at 16 phases** (6K) - only a rough
  sea needs 32 (12K). Free.
- **F. Code (~54K)** is tcc-verbose but not worth attacking for bytes.
  Reserve ~40K for music (SPC driver + module + samples) and ~30K for
  game modes / two-player code.

Projection at 8 courses / 8 profiles with today's format: ~120K courses
+ pool, ~80K profiles, ~72K code+gfx = ~270K of 512K, leaving ~240K.

---

## Game flow / menus (Sep 2026) - agreed design

Decisions: the title screen IS the attract mode (chaser-driven race on
SUNNY ISLAND behind overlays); screens are EITHER mode 7 with overlays in
the top band (lines 0-88) OR full-screen mode 1 - no third option (sprite
text over the sea and moving the mode switch were considered and
rejected); menu controls everywhere: d-pad, START/A confirm, B back;
riders are palette-only; championship = all courses in order, 9/6/3/1
points, standings after each race, final standings at the end; time trial
= rider + track select (track select gets a baked minimap from the zone
grid), endless solo laps, best-lap HUD, no ghosts (no SRAM); intro
"flyby" = the normal camera cruising the racing line (the camera is
global BY DESIGN - no swooping); mosaic ($2106) between states; the BG3
title strip is 2bpp = 3 colours + transparent for the eventual graphic.

Phases:
1. DONE (Sep 1) - runtime attract flag (AUTOPILOT builds unchanged),
   TITLE/MENU overlays over the attract race (band text + the two-word
   sprite logo with slide-in), textScreen placeholders, PAUSE (freeze
   loop, waveHdma re-kick per frame, resume or quit-to-title),
   mosaicSweep transitions. Verified in Mesen GUI mode with scripted
   input across the whole loop.
1.5. DONE (Sep 2) - the third menu entry is ARCADE, not 2P VS.: a single
   race (course select, 3 laps vs the NPCs - the classic flow), so
   plain racing stays available while phases 2-3 are built. 2P VS. is
   not a menu entry any more: when split screen lands post-jam, a
   second player will JOIN from Arcade's rider select ("P2 press
   start"), which phase 2 builds. TIME TRIALS is a placeholder page
   until phase 2.
2. Time trial - IN PROGRESS. Done (Sep 2, part a): rider select (the
   four riders as tall sprites, palette-only; picked palette drives the
   player, the other three drive the NPCs; feeds ARCADE too, and is
   where P2 will join post-jam; TEXT ONLY IN MAP ROWS >= 12 - the sky
   band rows 4-11 are shown by the race and a title written there burns
   into every following race), the TT race variant (solo - the NPC
   update AND projection blocks are !raceTT-gated; endless laps; TOP
   best-lap HUD cell - "BEST" needs a B glyph and the HUD font's VRAM
   window is exactly full; lap counter to 99), and chaser stuck-recovery
   (barely moving ~2s while racing -> reverse out ~3s: the chaser could
   deterministically wedge on the start-line rope pocket and grind
   forever - traces pinned at x=1407; real pads never trigger it).
   Part b (Sep 2): course select shows a 48x48 minimap of the
   highlighted course (bottom-right, rows 22-27 x cols 24-29) - baked
   from the zone grid in PAINTER orientation (the world the player sees),
   with the course's own ambient-lit water/sand/shore colours on the
   previously-unallocated palette row 7 (CGRAM 112-127), buoy dots
   (yellow/red) and a white start marker; 36 4bpp chars parked after the
   sky rows, tiles+palette re-DMA'd per cursor move (courseGeom repoints
   csMini mid-frame, the vblank kicks 1152+32 bytes). PHASE 2 COMPLETE.
3. Championship - course sequence, intro card (name + racing-line
   cruise), points + standings screens.
Post-jam: 2P VS. (split screen - see the cost sketch below).

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
