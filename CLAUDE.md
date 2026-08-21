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
- Reusable harness scripts are COMMITTED in tools/mesen/ (see its README):
  raceshot/startshot (screenshot sweeps), tickrate (the only trustworthy
  before/after perf number), counters (dual-run harness readout), oamdump.
  Pattern: ONE endFrame callback, save PNGs at target frames, emu.exit();
  Claude can Read the PNGs to verify visually. WRAM addresses come from
  superwaverace.sym and MOVE between builds - always re-grep.
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
- **tcc-816 compiles every 16-bit multiply into a ~100+ cycle library call**
  — the reason hot math lives in camera.asm. projectPoint (17 calls/loop,
  8 multiplies each) is asm now: ported bit-exactly (same floor semantics),
  verified by a dual-run harness (6797 calls, 0 output mismatches) because
  fixed-frame OAM comparison is invalid — a faster loop shifts the tick/frame
  alignment and everything physics-driven moves. The ski math bundle
  (split/merge, thrust, world position, camera pivot) is asm too — same
  bit-exact + dual-run-harness discipline (1826 calls, 0 mismatches); the
  mag/sign trig quads are seeded at init now, because skiWorld/skiSplit
  consume them BEFORE the first buildCamTables and BSS garbage reached the
  first loop's physics. The NPC steering products (npcAim: wrapped deltas +
  lateral bias + cross/dot; npcVel: velocity components) are asm too, same
  discipline (2790 calls, 0 mismatches). rowDepth is a binary search now
  (8 probes flat instead of up to 223 linear steps; verified EXHAUSTIVELY -
  all 14240 phase x depth inputs, 0 mismatches against the retired scan).
  Measured: 500 -> 554 -> 579 -> 620 -> 652 loops per 2000 frames (+30%
  total; loop averages ~3.07 vblanks). The hot-math porting pass is done -
  what remains in C is control flow, arrays and PVSnesLib calls.
- **The projection-block profiler (P in the debug UI) wraps to garbage**
  (e.g. 5458) when the bracket straddles a frame edge without an NMI between
  its two scanline() reads. Trust mid-frame readings; for real before/after
  numbers, count loop ticks over a fixed frame window (tickrate.lua pattern).
- **BSS is NOT zero-initialised.** Clear arrays explicitly (camTabs). This
  bit AGAIN with skiFlip: never initialised, garbage until the first steer,
  so the ski rendered v-flipped through the countdown — latent since the
  sprite was born, only seen when a timing change moved the frames sampled.
- **PVSnesLib console text**: consoleUpdate DMAs its map to hardcoded VRAM
  $0800 — inside Mode 7's region. Never use consoleDrawText/consoleUpdate;
  ui.c owns the text map. The lib is still used for font+palette init only.
- **HUD gradient text needs NO HDMA channel** (all 8 are taken): the baked
  font gives every glyph PIXEL ROW its own palette index 1-8, and three
  static CGRAM ramps (rows 4-6, 64-111) colour the text per scanline for
  free. The glyph order string in bake_tables.py HUD_GLYPHS must match
  hudIdx() in ui.c. HUD chars live at VRAM 0x7800 = map ids 640+ (tile
  ids are 10-bit; ids past 255 work fine). Band is 4 tile rows (UI_LINES
  32): row 0 and columns 0/31 stay EMPTY - CRT overscan crops them. The
  race clock shows hundredths (frames-in-second * 5 / 3). LAST LAP is no
  longer displayed (still tracked). The countdown + GO are the sprite
  start-light tree (LIGHT_SPR, 6 sprites; see the sheet bullet), risen
  and hidden row-by-row at the HUD's lower edge; only FINISH! remains a
  text banner (over the rank/lap cells, restored after ~6s).
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
- **EXTBG mangles BG2 outside mode 7 ON REAL HARDWARE ONLY.** SETINI bit
  6 stays on all frame for the sea's priority layer; the moment BG2 was
  main-screen-enabled during the mode-1 band (the first cloud attempt),
  real hardware filled the band with structured black/white jank -
  degenerate mode-7-style fetches through BG2's pipeline. NO emulator
  reproduces it (they implement EXTBG only when mode==7), so a clean
  Mesen run proves nothing here: never enable BG2 in TM during mode-1
  lines while EXTBG is set.
- **Clouds are therefore BG3 (2bpp), not BG1 or BG2**: scrolling BG1
  would need per-row HDMA table writes AND would slide the gradient
  dither; BG2 is EXTBG's (above). Band TM = 0x15, mode byte 0x09 (BG3
  priority: cloud cells carry the priority bit and draw over the BG1
  gradient - and over sprites, which is fine up there). Scroll: BG3HOFS
  = camTheta16 >> 6 (4px per binary degree; 256px map = exactly 4 wraps
  per full turn), one write-twice per frame, BOTH bytes back-to-back in
  vblank - all BG scroll regs share the prev-byte latch and the HDMA's
  $210D stream poisons it mid-frame. BG3VOFS = -1 once at init (scroll
  off-by-one). 2bpp chars sit right after the HUD font (CLOUD_CHAR0 is
  DERIVED from len(HUD_GLYPHS) - a hardcoded base silently overlapped
  when the font grew; asserted against the 0x8000 VRAM end), map at
  0x7400, palette group 7 = CGRAM 28-31 (spare UI-text-row entries:
  29 white, 30 shade). Blank map entries point at the set's char 0,
  which the bake guarantees blank.
- **A rebake does NOT recompile .c files** (the Makefile has no header
  deps): when wavedata.h DEFINES move (char bases, counts), touch the
  sources or the objects keep the old values - this shipped a build
  where ui.obj uploaded cloud chars to the old base while the bake
  mapped them at the new one.
- **Mesen settings.json now has Snes.RamPowerOnState=Random**
  (hardware-like power-on garbage instead of zeros) - keep it: zeros
  hide uninitialised-memory bugs that hardware will show.
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
- **WLA-DX sizes immediates from the TEXTUALLY last sep/rep, not the runtime
  path.** A `rep #$20` inside one branch arm makes the assembler encode the
  OTHER arm's `lda #imm` as 3 bytes while the CPU executes it in 8-bit mode
  — and the spare byte is $00 = BRK: an instant runaway (this crashed boot
  when npcTrig gained an interior rep). Keep branchy 8-bit sections
  single-mode, 16-bit work in a straight-line tail — the shape
  buildCamTables' trig section always had.
- **Wavelength must divide 1024** (map wrap); phases power of two;
  framesPerPhase in {1,2,4,8}; maxX < 4096 (16x8 multiplier headroom).
- **Sprite sheet rule**: every art's bottom row IS its slot's bottom row
  (margin 0); screen anchors derive from slot size only (−31 large, −15
  small). Negative blit margins wrap via Python indexing and corrupt other
  slots silently. TWO OBJ name tables now: table 1 (0x6000-0x6FFF) holds
  buoys/spray/lamps (the old racer slots are blank - reusable); table 2
  (0x7000-0x7FFF) holds the TALL RACERS (32x64 master, all NPC scales),
  each drawn as two stacked sprites sharing one projection - the BOTTOM
  sprite keeps the old 32x32 geometry so every waterline/window/spray
  constant is untouched (master waterline row 58 = bottom-sprite row 26).
  Table-2 sprites need OAM byte 3 bit 0 set: oamSet only takes the low 8
  gfx bits, so OAM_TALL(oid) ORs it in after EVERY tall oamSet. The VRAM
  for table 2 came from relocating UI map (0x4000), BG3 cloud map
  (0x4400), HUD font (0x4800) and cloud chars into the once-free 0x4000
  bank; BG1/BG3 char bases are 0x4000 now, so font ids are 256+ (UI_ATTR
  0x0500) and sky ids 448+ (still physically at 0x5000/0x5C00).
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
power ladder thrTab[6] in init (96..192, [3]=THRUST=the anchor feel; u8 -
multiplier input - and [5]*32 must stay under the 8192 overflow envelope),
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
  checkered start line baked at path[0]. The asm performance pass is done
  (see PLAN.md: 500 -> 652 loops/2000 frames, ~3.07 vblanks avg; locked 30Hz
  = SA-1, post-jam). Next: gate judging (optional), multi-course.
- Wake spray on the player only: a CONVEYOR of 16x16 dithered cells (two
  columns spanning the hull) under the stern. Cell 0 is a static source at
  the waterline; the rest scroll down at a chosen fraction of speed (sprWet
  >>1), and each whole-cell advance shifts the intensity ladder and injects a
  new level — 0 out of the water, 1-3 by speed, top level on a landing (which
  also forces an immediate inject so the burst lands with you). Art is
  procedural (spray_cell: hash-dominated dither, per-cell vertical falloff).
  The ladder hangs off waterRow, which rides the swell, so the scroll absorbs
  the frame-to-frame change in waterRow — otherwise the whole wake is dragged
  up and down with the ski instead of staying planted in the water.
  Speed gating: SPRAY_WET_MIN is the churn threshold (no foam below it, so
  reverse/airborne/idle are dry), SPRAY_WET_SHIFT spaces the art levels above
  it, and SPRAY_DRAIN keeps the ladder advancing while anything is still on it
  — without that a sudden stop freezes the wake on screen forever, since the
  scroll (and therefore the whole inject/shift cycle) is speed-driven. The
  landing burst gates on vAlong, NOT sprWet: forward speed survives a jump
  (no drag in the air) while the smoothed churn has decayed by touchdown.
  One-shot splashes were tried twice and CANNOT work: only ~24 world units of
  water are visible behind the ski, so anything world-anchored crosses the
  band in two loops and is never seen twice. NPC spray not done.
- The OBJ window is bounded to the hull's submerged rows (waterRow..sprTop+31)
  instead of everything below the waterline, which is what frees the area
  under the stern for sprites. Nothing is drawn above waterRow, so spray can
  never appear beside the rider.
- Power gates (buoy judging) done: the bake sorts buoys into racing-line
  order with the line direction at each (gateX/Y/Left/Nx/Ny/Wp) and LINTS
  labels against the side the racing line actually passes (cross > 0 = L,
  verified 14/14 on the authored course - never re-derive the handedness,
  the mirror flip makes reasoning about it treacherous). Runtime judges ONE
  armed gate: the along-track dot flips sign on the crossing tick (immune to
  tunnelling at any speed, unlike painted areas), the cross product picks
  the side; correct +1 power (cap 5), wrong resets to 0, thrTab rescales
  thrust. The gate arms only near its own segment (gRel window) so the
  infinite perpendicular can't slice a distant course leg; a gate left
  behind un-crossed is judged wherever you are (the buoy is a limit, not a
  target - any width of pass counts). Deltas pre-shift >>4 so the s8*s16
  products fit. HUD power bar redraws ONLY on change - 7 uiPrint/tick cost
  a measured 4% of the loop (652 -> 624); guarded it's 645 (~1%, the gate
  math). NPC balance vs the power ladder not yet revisited.
  No sound (jam judges music — PVSnesLib has an .it tracker driver, unused);
  sand is collidable but there's no "run aground" state; no title screen.
- PAL: accepted trade = runs slower (30Hz loop becomes 25Hz); must still boot.
- Real-hardware verified: EXTBG rendering, full HDMA stack, general play.
- CPU: ~45% of the 2-frame loop free; ROM ~58% free; CGRAM map in README.
