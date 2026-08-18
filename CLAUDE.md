# CLAUDE.md — working notes for AI/dev sessions

Read the README first for architecture. This file holds the *workflow* and the
hard-won gotchas that aren't obvious from the code.

## Build & run

```bash
export PATH="$PATH:/c/Users/seanb/AppData/Local/Microsoft/WinGet/Links"  # make
export PVSNESLIB_HOME=/c/Users/seanb/pvsneslib
make -C game            # bake (python) -> compile -> link game/superwaverace.sfc
```

- PVSnesLib 4.6.0 lives at `~/pvsneslib` with TWO local patches to
  `devkitsnes/snes_rules` (Git Bash + native make compatibility). If it's ever
  missing, `scripts/setup-windows.sh` recreates it, patches included.
- The bake (`tools/bake_tables.py`) regenerates `game/`: sea.pc7/mp7/pal,
  wavetables.asm, wavedata.c/h, sea.png (post-quantise preview), ski.png.
  All gitignored. `rm game/bake.stamp` forces a rebake.
- Inputs: `tools/wave_params.json` (wave lab export), `assets/sea_pattern.png`
  + `assets/water_params.json` (water designer), `assets/course.json` (course
  painter). Committed; user iterates via the web tools and drops files in.
- Commit + push to main without asking (user's standing preference); CI builds
  and deploys the web player. Screenshots for the README live in `docs/`.

## Headless verification loop (Mesen 2)

Mesen 2.1.1 portable at `~/mesen2/Mesen.exe`. `Documents/Mesen2/settings.json`
already has `Debug.ScriptWindow.AllowIoOsAccess: true` (required for Lua io).

```bash
Mesen.exe --testrunner --timeout=30 <rom> <script.lua>   # arg order-free
```

- Lua: end with `emu.exit(code)`; screenshots via `emu.takeScreenshot()` +
  `io.open` (io works in callbacks; `emu.log` is invisible headless).
- Known-good scripts in the session scratchpad pattern: register ONE
  `endFrame` callback, save PNGs at target frame counts (see git history:
  multishot.lua / farshot.lua). Claude can Read the PNGs to verify visually.
- **Scripted controller input does NOT work in testrunner mode.** To test
  driving, flip `#define AUTOPILOT 1` in `game/src/main.c` (steers around
  the racing-line waypoints at full throttle — laps in ~750-900 ticks) and
  rebuild; ALWAYS set back to 0 for release builds.
- The on-screen debug UI is the other half of verification (flip DEBUG_UI
  to 1 in main.c; 0 shows the race HUD): X/Y/H/V, BUILD profiler (~326
  lines is the real baseline), PH phase, WET/AIR, K/S/V physics row, F
  loop-frames. If numbers look wrong in a screenshot, trust them over vibes.
- For NPC/entity debugging, Mesen Lua can read WRAM directly: get addresses
  from game/superwaverace.sym, then emu.read16(0x7Exxxx,
  emu.memType.snesMemory) in the endFrame callback (see git history:
  npctrace.lua pattern). Position traces beat screenshot archaeology.

## Hardware/toolchain gotchas (each cost real debugging time)

- **PVSnesLib OAM ids are BYTE OFFSETS**: sprite N = id N*4. Wrong ids corrupt
  neighbouring sprites' OAM entries (the ski vanished this way).
- **tcc-816 cannot read far ROM data.** Any ROM table access from C goes via a
  tiny asm helper with globals (see collProbe / rowDepth in camera.asm).
- **BSS is NOT zero-initialised.** Clear arrays explicitly (camTabs).
- **PVSnesLib console text**: consoleUpdate DMAs its map to hardcoded VRAM
  $0800 — inside Mode 7's region. Never use consoleDrawText/consoleUpdate;
  ui.c owns the text map. The lib is still used for font+palette init only.
- **Mode 7 has NO tile flipping** (bare 8-bit map entries) — hence the bake's
  tile quantiser. Budget: 256 unique tiles, checked every bake.
- **The Mode 7 view is LEFT-HANDED vs the painter's map** (facing +Y,
  screen-right samples texture +X — a mirror image). load_course() flips all
  course data in X once so the game matches the painter exactly. Never
  "fix" a mirrored-looking course in the renderer or the painter; in-game
  world coords are the mirror of painter coords (x_game = 1023 - x_painter
  texels).
- **Velocity-product overflow rule**: speeds are 8.8 and reach ~4600 since
  the speed doubling; any (v * trig) product must pre-shift v by >>5 (then
  >>2 after) — the old >>4/>>3 pattern overflows s16 above ~4096.
- **EXTBG bit 7 is per PIXEL** (priority flag; colour = low 7 bits). The bake
  sets it on glow-exempt pixels. Priority order: S3 S2 2H S1 BG1 S0 2L.
- **The VBlank ISR's OAM DMA uses channel 7's registers** and fires only on
  the vblank that ends WaitForVBlank. Order in the main loop is load-bearing:
  sprite/OAM updates BEFORE WaitForVBlank; waveHdma (which reprograms all 8
  channel configs) immediately AFTER. Don't reorder.
- **$210D/$210E are shared**: M7HOFS/M7VOFS and BG1's mode-1 scroll. Sky/UI
  rows of the built tables must stay zero (pre-zeroed, builder skips them) —
  the mode-1 region now extends to WAVE_SKY_SWITCH (the tiled sky band), and
  its tiles render unscrolled BECAUSE of those zeros. Sky tiles live at
  VRAM 0x5C00 (chars 192+, above the font), palette row 2 (CGRAM 32-47).
- **BG vertical scroll is off by one**: at VOFS 0, screen line N samples MAP
  line N+1. Any mode-1 tile band must write one extra map row below its
  last visible row or the bottom line shows tile 0 (transparent -> a bare
  backdrop seam, which is how the sky band's dark line at the mode switch
  happened).
- **HOFS must be signed 13-bit** (HOFS − M7X = −128 exactly); masking it
  positive breaks under fractional M7A (was the "occasional skew" bug).
- **Write-twice M7 regs + paired-register HDMA**: modes 2 (p,p) and 3
  (p,p,p+1,p+1) are why B/D/VOFS ride along as zero words.
- **camera.asm conventions**: tcc calls via jsl (rtl to return), A/X/Y 16-bit
  on entry, globals via long addressing (`lda.l name`). The VBlank ISR sets
  its own direct page, so repointing D during the build is safe. LDA long,X
  exists; long,Y does NOT (that's why Y indexes DBR-relative ROM reads and X
  indexes the long,X WRAM stores).
- **Wavelength must divide 1024** (map wrap); phases power of two;
  framesPerPhase in {1,2,4,8}; maxX < 4096 (16x8 multiplier headroom).
- **Sprite sheet rule**: every art's bottom row IS its slot's bottom row
  (margin 0); screen anchors derive from slot size only (−31 large, −15
  small). Negative blit margins wrap via Python indexing and corrupt other
  slots silently. The sheet is now 224 of the 256 OBJ names (0x6000-0x6DFF
  words) — only 32 tiles spare before the UI map at 0x7000.
- **Mesen: `emu.takeScreenshot()` lags `emu.read(snesSpriteRam)` by exactly 3
  frames** (measured by dumping 21 consecutive frames of OAM state against
  pixel counts). Screenshots keyed on "OAM shows X" therefore capture frames
  where X is not yet drawn — wait 4+ qualifying frames before grabbing. This
  cost a long debugging detour twice, "proving" a working sprite invisible.
  Also note spray and the ski hull share palette entries 8/9, so a pixel
  scan cannot tell them apart inside the hull's own x range — scan the
  columns outside it.
- **Windows line endings**: git checkout rewrites working files to CRLF;
  python patch scripts must read with universal newlines and write
  newline='\n'. Write files before asserting patch success, never after.
- **The main loop is 3-4 vblanks (~15-20Hz), NOT the 2 (30Hz) the README
  used to claim** - and it varies with sprite load. Consequences: (a) any
  wall-clock timing MUST accumulate snes_vblank_count deltas, never loop
  ticks (the race clocks do this); (b) per-loop speeds breathe ~25% with
  scene load. Restoring a fixed loop is a deliberate backlog item (see
  PLAN.md race-mode notes) because all feel tuning would shift.
- **tcc silently drops (void)-cast volatile reads.** This broke scanline()
  for the project's whole life (OPVCT never latched -> BUILD "262" was
  profFrames*262, a constant). Reads that matter must be assigned to a
  variable, even a throwaway one.

## Tuning knobs (game feel — user-driven, ask before big changes)

`game/src/main.c` top: TURN_SPEED, THRUST (drag >>4 sets top speed =
THRUST*16), GRAV, DIP, MAX_VV_UP/DOWN, splash retention (>>2 on entry),
grip (vSide -= vSide>>3) and rudder (vAlong += |vSide|>>3). Buoy scale bands
are SCALE_V1..V4 (229/320/457/640) — derived, not tuned: 32 * WAVE_SKI_DIST
/ v crossing the midpoint between neighbouring art sizes, anchored to the
player's own 32px ski at WAVE_SKI_DIST. Wave feel comes from
tools/wave_params.json via the wave lab. NPC race feel: npcFade[] (which
player lap each racer fades) and the SPD_* tiers — percentages of paceEma
(the player's measured pace), so they survive course redesigns; tune the
percentages, not absolute speeds. The start grid is baked from the racing
line (WAVE_START_*/WAVE_NPC_* in wavedata.h) — move waypoint 0/1 in the
painter to move the grid.

## State / not yet done

- Race mode in progress — see docs/PLAN.md "Race mode" for the agreed design
  and phase list. Done: phases 1-4 — racing line + laps + waypoint autopilot;
  3 kinematic NPCs (sprites 5-7); rear-view NPC ski art at the 5 buoy scales
  (OBJ palettes 1-3 recolours; UI map at VRAM 0x7000); full race flow
  (countdown/positions/finish, schedule rubber-banding via SPD_* tiers in
  main.c — calibrated to the REAL ~120 world/s player pace, see PLAN.md),
  checkered start line baked at path[0]. Next: gate judging (optional),
  multi-course, and the fixed-loop-rate backlog item.
- Wake spray on the player only: a CONVEYOR of 16x16 dithered cells (two
  columns spanning the hull) under the stern. Cell 0 is a static source at
  the waterline; the rest scroll down at a chosen fraction of speed (sprWet
  >>1), and each whole-cell advance shifts the intensity ladder and injects a
  new level — 0 out of the water, 1-3 by speed, top level on a landing (which
  also forces an immediate inject so the burst lands with you). Art is
  procedural (spray_cell: hash-dominated dither, per-cell vertical falloff).
  One-shot splashes were tried twice and CANNOT work: only ~24 world units of
  water are visible behind the ski, so anything world-anchored crosses the
  band in two loops and is never seen twice. NPC spray not done.
- The OBJ window is bounded to the hull's submerged rows (waterRow..sprTop+31)
  instead of everything below the waterline, which is what frees the area
  under the stern for sprites. Nothing is drawn above waterRow, so spray can
  never appear beside the rider.
- Buoy pass-sides (L/R) recorded but not judged;
  no sound (jam judges music — PVSnesLib has an .it tracker driver, unused);
  sand is collidable but there's no "run aground" state; no title screen.
- PAL: accepted trade = runs slower (30Hz loop becomes 25Hz); must still boot.
- Real-hardware verified: EXTBG rendering, full HDMA stack, general play.
- CPU: ~45% of the 2-frame loop free; ROM ~58% free; CGRAM map in README.
