# CLAUDE.md — working notes for AI/dev sessions

Read the README first for architecture. This file holds the *workflow* and the
hard-won gotchas that aren't obvious from the code.

## Build & run

```bash
# make: WinGet puts ezwinports.make on the USER PATH (Links/ on one machine, the
# package's bin/ on another) - Git Bash inherits it; add it here only if not.
export PVSNESLIB_HOME=$HOME/pvsneslib
make -C game            # bake (python) -> compile -> link game/superwaverace.sfc
```

Machines: `seanb` (original) and `Sean` (Aug-28 clone, `C:\Users\Sean\Downloads\super waverace`
- a path WITH a space, which the setup script's `-I.` patch exists for).

- PVSnesLib 4.6.0 lives at `~/pvsneslib` with TWO local patches to
  `devkitsnes/snes_rules` (Git Bash + native make compatibility). If it's ever
  missing, `scripts/setup-windows.sh` recreates it, patches included.
- The bake (`tools/bake_tables.py`) regenerates `game/`: sea.pc7/mp7/pal,
  wavetables.asm, wavedata.c/h, sea.png (post-quantise preview), ski.png.
  All gitignored. `rm game/bake.stamp` forces a rebake.
- Inputs: `assets/courses/<nn>_<name>/course.json` (course painter; the
  MENU NAME comes from the folder name past the underscore - the bake
  FAILS if a legacy `assets/course.json` exists). A course folder may
  carry its own `sea_pattern.png` / `water_params.json` (else the shared
  `assets/` ones apply) and name a wave profile: `"wave_profile": "calm"`
  -> `assets/waves/calm.json` (wave lab export; identical profiles dedupe
  in ROM). `tools/wave_params.json` is the DEFAULT profile AND the global
  camera (camH/pitch/fov/skiDist - profiles must not change those; the
  bake asserts). course.json may also carry `"palette"` (role ->
  "#rrggbb"; roles = PALETTE_ROLES + sand_far/sand_deep in the bake, the
  painter's Palette & light group edits them; `sky` = the zenith, CGRAM 0
  + the band anchors 32-47 - the band ALWAYS pales toward the horizon from
  it because the mode-7 safe strip continues the last anchor via a uniform
  COLDATA add; `sky_horizon` = the band's bottom colour for a chromatic
  sky: then backdrop 0 = horizon - that add (14/31 today) so the strip
  still lands on the horizon, which is why every horizon channel must be
  >= 112/255 - the bake warns otherwise. Band tiles use indices 1-15 only
  and the HUD/menu blank tile 0 is SOLID colour 15 = CGRAM 31 = the
  zenith per course, because backdrop 0 is no longer the zenith; the HUD
  FONT's background pixels are index 15 too - CGRAM 79/95/111 = csZen,
  the zenith - because colour 0 in a glyph cell showed the backdrop as a
  muddy box behind the race HUD text on every chromatic-sky course) and
  `"ambient"` ("#rrggbb" multiplier, applied at bake to
  CGRAM 1-15/48-51, the sand fade, the cloud pair 29-30 and OBJ palettes
  0-3 + 5; NOT to the HUD, lamps or the sky colour). Absent = defaults =
  byte-identical to the pre-palette bake. Committed; user iterates via
  the web tools and drops files in. The bake prints a per-course/
  per-profile byte-budget report and WARNINGs for shade pairs that
  collapse under a dark ambient.
- Commit + push to main without asking (user's standing preference); CI builds
  and deploys the web player. Screenshots for the README live in `docs/`.

## New machine checklist (nothing below is in the repo)

1. Clone; run `scripts/setup-windows.sh` -> recreates `~/pvsneslib` (4.6.0)
   WITH the two `devkitsnes/snes_rules` patches. Install make via WinGet
   (the PATH export above).
2. Mesen 2.1.1 portable -> `~/mesen2/Mesen.exe`. Run it once, then in
   `Documents/Mesen2/settings.json` set `Debug.ScriptWindow.AllowIoOsAccess`
   = true (Lua io) and `Snes.RamPowerOnState` = "Random" (hardware-like
   garbage; flip to "AllZeros" ONLY for dual-build equivalence runs).
3. `make -C game` should print the byte-budget report and link a 512K-cap
   LoROM; `Mesen.exe --testrunner --timeout=60 game/superwaverace.sfc
   tools/mesen/tickshot.lua` (with the env vars from its header) proves
   the harness works. The conversation history does not travel: this
   file + docs/PLAN.md "Multi-course" + "Game flow" ARE the state.
   Multi-course phases 1-4 done, phase 5 (content) is in progress: SIX
   courses build, all geometric clones of course 1 with distinct
   palettes/skies/wave profiles (see the State section) - real layouts
   are the remaining authoring work. The game-flow plan is COMPLETE
   through phase 3 (title/attract, menu, rider + course select, time
   trials, arcade, championship with flyover intros and the in-race
   results table); 2P split screen is post-jam.

Lessons from the Aug-28 machine (`Sean`), all of which bit:
- `winget install Git.Git`, `Python.Python.3.12`, `ezwinports.make` all need
  `--source winget` (msstore ambiguity) and the Store's `python`/`python3`
  App Execution Alias stubs MUST be deleted from
  `%LOCALAPPDATA%\Microsoft\WindowsApps` - the Makefile prefers `python3`,
  which resolves to the stub and fails with "Python was not found".
- The bake is pure stdlib (json/math/struct/zlib) - no pip installs.
- Mesen 2.1.1 is a self-extracting exe: the FIRST launch unpacks its DLLs
  into `Documents/Mesen2` and only writes settings.json on a clean exit.
  Hand-writing a minimal settings.json with just the two keys above works
  (Mesen fills the defaults).
- **The smoke test needs `AUTOPILOT 1`**: since the course-select screen,
  a stock build sits on the menu forever (testrunner has no input) and
  `tick` is race BSS = Random garbage, so tickshot never arms and the
  trace stays empty. Flip, build, run, flip back.
- **`emu.exit()` does NOT terminate the testrunner on this machine**
  (verified: a script calling emu.exit(0) at frame 30 kept running at
  frame 200). Every run rides `--timeout` to the wall and returns -1;
  outputs are still written (finish() rewrites its files each frame), so
  the harnesses work - just budget the timeout and ignore exit codes.
- Mesen.exe is a GUI process: from PowerShell use `Start-Process -Wait`
  or the call returns instantly with no exit code. From Git Bash it waits.
- **`make buildWithSummary` exits Error 1 when there is NOTHING to build**
  (all objects current). Looks like a build failure, is not - touch a
  source if you need a real relink. Bit twice.
- **PVSnesLib oamSet writes only the low 8 bits of X**, and
  oamSetEx(OBJ_SHOW) CLEARS the OAM high-table x8 sign bit: a sprite at
  small negative X wraps to the right edge. OAM_X8(oid) (main.c, the
  OAM_TALL pattern) restores it - required for anything entering from
  the left.
- **A hand-written settings.json leaves `Snes.Port1.Type` = "None"**: no
  controller is plugged into the emulated console, so NO input works in
  Mesen (the first-run dialog normally sets this + a keyboard preset).
  Set `"Snes": {"Port1": {"Type": "SnesController"}}` and bind keys in
  Settings > Input > Player 1 > Setup (preset). This masqueraded as a
  game bug ("menu ignores up/down/start") for a while.

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
- **Scripted controller input does NOT work in testrunner mode** - but it
  DOES in GUI mode: `Mesen.exe rom.sfc script.lua` (no --testrunner) with
  Port1 = SnesController honours `emu.setInput({down=true}, 0)` from an
  inputPolled callback (tools/mesen/menuinput.lua - kill the process when
  done, it has no exit). Use it for menus; for driving, flip
  `#define AUTOPILOT 1` in `game/src/main.c` (steers around
  the racing-line waypoints at full throttle — laps in ~750-900 ticks;
  also auto-confirms the course select after ~90 frames and the results
  after the FINISH banner, so the whole state loop cycles hands-free) and
  rebuild; ALWAYS set back to 0 for release builds.
- The on-screen debug UI is the other half of verification (flip DEBUG_UI
  to 1 in main.c; 0 shows the race HUD): X/Y/H/V, BUILD profiler (~326
  lines is the real baseline), PH phase, WET/AIR, K/S/V physics row, F
  loop-frames. If numbers look wrong in a screenshot, trust them over vibes.
- For NPC/entity debugging, Mesen Lua can read WRAM directly: get addresses
  from game/superwaverace.sym, then emu.read16(0x7Exxxx,
  emu.memType.snesMemory) in the endFrame callback (see git history:
  npctrace.lua pattern). Position traces beat screenshot archaeology.
- Equivalence between builds whose INIT length differs (fixed frames are
  invalid then): tickshot.lua (tick-keyed trace) + tabdump.lua (camTabs
  dump = the renderer's whole HDMA output, jitter-free). Both need
  settings.json Snes.RamPowerOnState=AllZeros FOR THE RUNS (latent
  read-before-write BSS makes same-ROM runs diverge under Random!) -
  restore Random after. Tick-keyed screenshots still jitter +/-1 frame
  (HUD clock digits differ); compare data, not pixels.
- The read-before-write BSS list is FIXED (raceInit seeds phase/camBufOff/
  vAlong/vSide, plus the trig quads and aim pipes camera.asm's 16-bit u8
  reads overrun - masked, seeded anyway so the Mesen log stays clean).
  The ONLY expected uninit flags now are $000016/17: tcc's own DP scratch,
  a codegen artifact, benign. Anything else in the log is a NEW bug.

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
- **The mode-1/mode-7 switch is a SCANLINE IRQ, not HDMA** (since the sand
  fade): hdr.asm is HAND-MAINTAINED (AUTOHDR := 0, un-gitignored) with the
  native IRQ vector -> irqStub, a 4-byte jml in BANK 0 (bank 0 had 11
  bytes free - the stub is the only thing that fits there) -> irqSwitch
  (camera.asm): ack $4211 (mandatory or it refires), spin on the $4212
  hblank flag, write $2105=7. The IRQ runs a STAGE TABLE per frame
  (Sep 3: irqLine[i]/irqAct[i], irqN; irqStage = the one being serviced;
  the handler re-arms VTIMEL for the next; vblTop resets stage 0 + line 0
  every frame; layoutSet() fills the table). Acts: 0 = the cloud rows'
  BG3HOFS (cloudHofs, set by the main-loop tail: heading>>6 in RM_RACE/
  RM_INTRO, 0 under the results table) - written in the ACTIVE part of
  the next line (a blank BG3 row, so invisible), NOT in hblank where the
  8-channel HDMA burst could split the write-twice pair and poison the
  shared BGOFS latch; vblTop writes BG3HOFS 0 at frame top so the BAND's
  BG3 (the intro card text) sits still while the clouds turn; 1 = mode 7;
  2 = mode 1 (0x09). 1P: {31: 0, 87: 1}. The 2P split: {switch-1: 1,
  103: 2, 120+switch-1: 1}. H dot 260 throughout. The NMI
  callback (nmiSet -> vblTop) restores mode 0x09 at frame top EVERY frame
  (the main loop is slower than the frame rate). irqOn (camera.asm) does
  the cli. Menus park the timer IRQ (NMITIMEN 0x81): their own BG3HOFS
  writes (course-select drift) land after vblTop's 0 in the same vblank. Freed HDMA ch0 = CGRAM stream ($2121 mode 3:
  CGADD,CGADD,CGDATA,CGDATA = one palette entry per table entry): the
  baked sand_fade table repaints CGRAM 8 down the frame in hold-runs -
  the wave-phase-independent sand distance fade. Measured cost: zero
  (648 vs 649 ticks/2000f). NOT yet hardware-verified.
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
- **Signed x unsigned products go UNSIGNED under tcc, with a LOGICAL
  shift.** `((vAlong >> 4) * wvSteps) >> 4` with wvSteps u16 and a
  NEGATIVE vAlong (reversing) gave ~+4000 instead of -56: the wave phase
  leapt half a cycle every tick = "the camera shakes when I back up",
  latent since the per-course wave profiles made wvSteps a variable
  (Aug 28). Cast the unsigned operand to s16 before multiplying anything
  that can be negative. Reproduce/verify with tools/mesen/revtrace.lua
  (GUI mode, release build: holds Y and traces phase/skiY/waterRow).
- **The renderer's viewport is parameterised (Sep 3, for the 2P split):**
  buildCamTables takes camBlk1Ct AND camBlk2Ct (lines to build in HDMA
  block 1 = lines 0-126 and block 2 = 127-223; either may be 0 - a
  viewport can start inside block 2; C computes them from the first
  built line L: block-1 entry offset 1+4L, block-2 offset 2+4L); rowDepth
  reads rdLast/rdLastOfs (last row / its byte offset: 223/446 for 1P,
  103/206 for a 2P half); projectPoint's visible depth range is
  pjNear/pjSpan (176/445 1P, 140/445 CAM2); the phase stride is wvStride
  (448 / 208) and the ski's px-per-texel skiPpt. layoutSet() sets them
  all. The 2P tables: CAM2 in tools/wave_params.json (pitch/fovV/lines;
  camH/fovH/skiDist SHARED - the a-formula, sprite scales and the ski
  anchor hang off those), baked as wave_rawd2_f<n> + WAVE_LINES2/
  WAVE_VP_B_TOP/WAVE_SKY_SWITCH2/WAVE_RAW_STRIDE2/WAVE_SKI_PPT_Q4_2, loaded
  by waveProfLoad2 (same WRAM as 1P's set - only one layout runs at a
  time; waveSky[] = the half's per-phase horizon). The 2P TM table is
  COMPOSED PER TICK in tmBuf (12 bytes: backdrop above each half's
  horizon, sea below, BG1 for the HUD strip) - the halves run different
  phases, so no baked per-phase table can serve them; COLDATA is a fixed
  ramp (no crest glow in 2P). `#define SPLIT 1` (main.c, ALWAYS 0 for the
  game) is the measurement spike: same camera into both halves - see
  PLAN.md "Two-player split screen". Screenshots showed the trap of a
  FIXED sky switch: sea rows above the true horizon render the far-cap
  distance as stretched streaks - hence the per-tick TM.
- **make skips a source touched in the SAME SECOND its object was
  built** (mtime granularity): a `sed` flip + `touch` + `make` right after
  a build silently rebuilds NOTHING and you copy a stale ROM. `sleep 2`
  between builds in any flip-build-copy chain, and cmp the copies.
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
- **Sprite-vs-sprite priority is OAM INDEX ONLY** (lower id in front; the
  priority field orders against BGs, never other sprites). Consequence:
  multi-sprite entities need ADJACENT ids, and entities that overlap at
  varying depths need their OAM slots assigned by depth per tick. Layout:
  player pair 0-1, buoys 2-17 (WAVE_MAX_BUOYS 16), NPC pairs 18-23 handed
  out nearest-first (projections buffered, 3-element sort network), spray
  24-31, lamps 32-37, title logo 38-44 (the SPLIT spike puts viewport B's
  buoys at 24-37, racers 38-43, ski 46-47 - the old "16-21" note here
  cost an hour: the B ski landed on NPC pair 3). A half-covered stacked racer reads as a racer floating by
  exactly the seam height - that's how this bug presents.
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
- **Anything that DMAs to VRAM right after WaitForVBlank must be TINY.**
  tcc code is slow: composing one 32-entry menu row costs ~45 scanlines,
  so "WaitForVBlank; compose+DMA two rows" put the DMAs at scanline 12
  and 57 of the NEXT frame - active display, where the PPU drops VRAM
  writes, silently. The course-select cursor never moved on screen while
  courseSel changed underneath (START then launched the right course).
  Pattern: compose into RAM buffers mid-frame, kick only the DMAs after
  the wait (uiMenuCompose / uiMenuRowDma). Diagnosed with a Lua memory
  callback on $420B logging ppu.scanline - that pattern finds any
  "write landed outside vblank" bug in one run.
- **tcc silently drops (void)-cast volatile reads.** This broke scanline()
  for the project's whole life (OPVCT never latched -> BUILD "262" was
  profFrames*262, a constant). Reads that matter must be assigned to a
  variable, even a throwaway one.
- **tcc miscompiles `for` with an EMPTY condition**: `for (x = 0;; x++)`
  emits a branch-to-self (80 FE) right after the init stores - an instant
  silent hang before the body ever runs. Use do/while (verified) instead.
  Found via the PC-sampling Lua pattern (emu.getState) + hand-disassembly.
- **tcc copies only 16 BITS of a pointer-var to pointer-var assignment**
  (`a.mem.p = b.mem.p` never writes the bank byte). LITERAL stores
  (`p = (u8 *)&symbol`) write all 4 bytes, and passing a pointer VALUE as
  a function arg works - it is var-to-var copies that truncate. Copy
  dmaMemory addr+bank member-by-member. Related: **returning `char *`
  from a function loses the bank too** - courseNameTo copies into a
  caller buffer instead. Both found via Mesen exec/memory callbacks.
- **courseLoad NEEDS HDMA OFF, not just force blank** (waveRawLoad
  borrows the PPU multiplier and the sand-fade channel keeps repainting
  CGRAM even in force blank): every game-flow path that loads a course or
  touches VRAM between states does REG_HDMAEN = 0 first - the quit-a-race
  -> attract path shipped without it once and produced garbage wave
  tables + leaked palette entries ("glitchy jank" on the title).
- **The course/profile loaders (courseLoad in main.c)** swap everything
  per course under force blank: courseGeom + waveProfLoad (generated),
  waveRawLoad (delta-d decode + a synthesis), packed collision ->
  $7F7000, TILES from the cross-course pool (the course's 512-byte u16
  pool-id table -> $7FC000 via copyTo7F, then tilesTo7F assembles the
  16K set in $7F8000; DMA it to the tile plane BEFORE the map decode
  reuses that buffer - order is load-bearing), map (SPARSE codec: the
  256-byte default block = the water pattern's tile ids per (row&15,
  col&15), copied to $7FC200, then skip/literal tokens - plain RLE loses
  to the 16-period water texture; the old copy-from-16-back codec was 2x
  bigger) through the $7F8000 buffer, both to the VRAM
  planes via dmaCopyVram7 (verified byte-identical to the bake with a
  Lua VRAM dump - scratchpad pattern: read 2i+1 = tile plane, 2i = map),
  and
  ONLY CGRAM entries 1-15 + 48-51 (a full palette load wipes the
  boot-loaded UI/sky/HUD/OBJ rows - this shipped one build as all-black
  sprites). NEVER call bgInitMapTileSet7 after boot for this reason.
- **The wave d/a arrays live in WRAM $7F0000/$7F3800** (WRD/WRA in
  camera.asm), expanded at load by waveRawLoad: ROM stores only d,
  delta-encoded (~7K instead of 28.7K); a = max(1,(d*18919+32768)>>16)
  is synthesised with the PPU 16x8 multiplier (M7A/M7B as d*74*256-d*25;
  needs force blank + HDMA off - fine at init, remember it per course
  load). 18919 = round(tan(fovH/2)/2*65536); the bake asserts the 74/25
  split, so changing fovH fails the bake instead of silently diverging.
  The camera (camH/pitch/fov/skiDist) is global across ALL courses BY
  DESIGN - never make it per-course, half the projection constants and
  the sprite scale bands assume it.
- **The collision map is packed 2 bits/cell** (4K not 16K): collProbe
  unpacks (byte ofs>>2, bit (ofs&3)*2). Verified exhaustively: an
  in-ROM checksum loop over all 16384 cells vs the bake's printed sum.

## Rider art pipeline (user-authored, Photoshop)

assets/rider_stand.png + rider_turn.png (32x64 indexed PNGs, true-alpha
background, hard pixels) + assets/rider.act, whose entry ORDER is the
role contract: 0 black, 1 white, 2 lt grey, 3 dk grey, 4/5 skin pair,
6/7 clothing A (blues), 8/9 clothing B (reds), 10/11 jetski accent
(authored green; DISPLAYED warm yellow - ACT colours are authoring
colours, SKI_PALETTE/NPC_PALETTES are what shows). Bake maps pixels by
exact RGB match and fails listing unknown colours. Waterline = row 58.
The turn frame is authored leaning LEFT and mirrored at bake (runtime:
KEY_LEFT sets hflip). Buoys borrow player slots 9/10 (red) and 11/12
(yellow); spray uses 2 (white) + 13 (a FIXED grey - the greys at 3/4
are the per-rider HULL pair now, and spray must not tint with it); the
start lamps have their OWN palette (OBJ 4, CGRAM 192, LAMP_PALETTE in
the bake). NPC recolours override slots 3-12 (hull + skin + kit - five
adjustable pairs per rider). Buoys draw with OBJ palette 5 (CGRAM 208,
BUOY_PALETTE = the player's slots 1/2/9-12 copied, so the buoy art's
baked indices still work) - WAVE_BUOY_PAL in drawLadder. All of 0-3 + 5
are baked PER COURSE under its ambient (crs<n>_obj / crs<n>_buoy).

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
line (startX/Y/Theta + npcGrid* runtime vars, set by waveTablesInit — the
per-course geometry is ALL runtime now: buoyCount/pathCount too, with
WAVE_MAX_BUOYS/WAVE_MAX_PATH fixing array sizes and the OAM layout) —
move waypoint 0/1 in the painter to move the grid.

## State / not yet done

- Game flow (reworked Sep 1, phase 1 of the menu plan - see PLAN.md "Game
  flow"): boot -> TITLE = ATTRACT (a chaser-driven race on SUNNY ISLAND
  behind overlays: the title logo is TWO WORDS of 32x32 sprites that
  slide in from opposite edges (eased) and settle overlapped -
  assets/title-waveracer.png (up to 128x32, real alpha, OBJ palette 6 =
  CGRAM 224, 4 blocks: table-1 names 0/4/128/132, from the right to
  x=64) under assets/title-super.png (up to 96x32, WHITE = transparent,
  OBJ palette 7 = CGRAM 240, 3 blocks: table-1 136/140 + table-2 136
  with OAM_TALL, from the left to x=56, drawn 8px higher and OVER via
  lower OAM ids). Each word: up to 15 opaque colours (waveracer is AT
  the limit). Slots are bake-ASSERTED blank; titleBlock hides blocks
  outside 9-bit OAM x. Absent art = a text placeholder in the waveracer
  slot; clouds are blanked entirely during attract - and
  console-font text in the HUD band; BG3 scroll frozen so the title sits
  still) -> START/A -> main menu (CHAMPIONSHIP / TIME TRIALS / 2P VS.,
  same attract race behind; B back, START/A confirm - that convention
  everywhere) -> ARCADE (single race vs NPCs) and TIME TRIALS (solo,
  endless, no lap counter, BEST = best lap M'SS"T in the RANK cell (the
  B glyph took unused G's slot - the HUD font's VRAM window is exactly
  full); raceTT gates the NPC blocks, the finish, and the HUD variant.
  drawSki's pal arg IS the OBJ palette number: npcPalTab holds final
  palettes, and re-adding the old "1 +" once dressed an NPC in the lamps) both run rider select (4 riders,
  palette-only: playerPal drives the player sprites, npcPalTab the NPCs;
  P2 joins here post-jam) -> course select -> race. CHAMPIONSHIP (phase
  3, Sep 2): rider select -> for every course in folder order: INTRO
  FLYOVER (courseLoad + raceInit with attract=1 = the chaser steering
  the racing line, raceMode RM_INTRO = "RACE n OF 6" / course name /
  flashing PRESS START in the SKY FONT on BG3 rows 1-3 = the HUD band
  (the baked band TM is 0x15 = BG3 on since Sep 2, its BG3 map rows
  blank; skyUp = 1, introDraw composes skyRows[0..2], the vblank tail
  DMAs them to rows 1-3 in RM_INTRO and to the cloud rows otherwise;
  skyRestore blanks rows 1-3 too) - NOT band console text: those
  glyphs' colour-0 cells show the BACKDROP, a wrong-colour box on every
  chromatic-sky course. The clouds stay during the flyover AND scroll
  with the heading (the stage-0 IRQ scrolls only the sky rows; the band
  rows holding the text stay at 0). NO RACERS: player sprites, spray
  and both NPC blocks are RM_INTRO-gated; CONSTANT SPEED: skiVX/VY are
  SET each tick = INTRO_PASSES x skiThrustF(INTRO_THRUST) along the
  heading, thrust/drag skipped (~2500 8.8, half pace); it ends after ONE
  FULL LAP (lapCount 255 -> 0 at the rolling start -> 1) or on START ->
  mosaic, uiClear (else the HUD inherits the card's text), raceInit
  AGAIN with attract=0 - no reload, raceInit owns every race variable)
  -> race -> results -> champPage (full-screen mode 1: the four riders
  as tall sprites in standings order, leader raised, a 4-line table
  place/name/+race pts/total/P1 in map rows 21-24, prompt row 26 - rows
  >= 28 are BELOW the 224 visible lines; the final page's title is "<NAME> IS
  CHAMPION" - shown ONCE, after the last race; per-race standings live
  in the sky table's totals column). THE LINE (Sep 2): laps count on a
  TRUE CROSSING of the chequered strip at path[0] - the along-track dot
  vs startNx/Ny (= sin/cos of startTheta, 1.7, from npcTrig at raceInit)
  flips sign, armed when seen behind the line within 400 Manhattan
  units, and a lap needs (pathCount-1) waypoints of progress since the
  last one (lapBase) so backing over the line farms nothing - NOT on
  entering waypoint 0's 200-unit radius, which read ~10 yards early for
  the whole project. Waypoint 0 still drives PROGRESS (nextWp/pProg).
  NPCs (npcLap/npcArm/npcLapBase) use the identical test - the line
  tests use their OWN deltas lnDx/lnDy: the NPC waypoint-reach test
  reads wpdx/wpdy AFTER them and reusing those crippled NPC progress
  once. THE FINISH: a finished NPC (npcDone) eases off - npcSpd decays
  >>4/tick, ~220 units of glide - and is skipped once below 64 (still
  "ahead" for the position counter, never shoved); the finished PLAYER
  gets extra drag (>>2) and pulls up in ~50 units, so later finishers
  roll past and settle AHEAD of the camera, in view (with equal glides
  they stopped behind it, invisible). riderFinish() books each rider
  into finList with its place's points (9/6/3/1, paid at once in a
  championship). From the PLAYER's finish a live RESULTS TABLE floats
  in the sky: BG3 2bpp text (skyf_gfx, ids 986-1023 = the last free 4bpp
  window after the minimap chars; white fill, 1px right + 1px down
  shade in index 3 = CGRAM 31 = the course ZENITH; uiSkyCompose/Append/
  RowDma in ui.c) written over the four cloud rows 5-8: "1ST CALLISTA #
  9 27" = place, name, finished flag, race points, championship total
  (finished riders first, then the rest in live order - liveOrder(): the
  position counter's own comparison - recomposed only when the order
  changes, DMA'd in the vblank tail; BG3HOFS held at 0 under it, the
  clouds simply gone); FINISH! stays in the HUD. After 5s (finFr) PRESS
  START appears on BG3 row 10 (just above the horizon) and the race
  WAITS for START (no auto end; CHAMP_AUTO builds end at 5s): the
  unfinished are placed where they stand (raceFinish -> riderFinish).
  skyRestore() (force blank: cloud_map back over rows 5-8, row 10
  blanked) runs in every post-race path BEFORE the next screen - the
  championship branch (next intro, or PAGE_FINAL after the last race)
  and the attract branch (ARCADE: the sky table IS its result;
  pause-quit lands there too). Tables index by rider id = palette
  (riders ARE palettes): 0 MAGNUS, 1 CALLISTA, 2 MILO, 3 DAFYDD
  (sbRider). champOn/champRace/champStage drive main()'s outer loop
  (stage 0 intro pending, 1 intro running, 2 race running, 3 = page up,
  read only by the Lua harness); pause+B abandons -> title. Text goes
  through the menuBuf string builder (sbClear/sbCat/sbNum): no printf,
  and functions must not RETURN char* (bank loss). uiMenuRow CLEARS the
  row it writes - multi-column rows compose via uiMenuAppend + one DMA.
  `CHAMP_AUTO 1` (main.c, ALWAYS 0 for release) = the chaser drives the
  championship races + every page auto-advances + a wedged reverse-out
  teleports the pivot to the next waypoint (Sunset Cove pockets the
  chaser between walls at x~1250/1407 forever - real pads don't): run
  tools/mesen/champnav.lua in GUI mode for the hands-free 6-race sweep.
  Rider-select/menu text may
  ONLY use map rows >= 12: rows 4-11 are the sky band the race shows.
  BUOYS ARE NOT SOLID (Sep 2, all modes): the bake no longer writes
  collision value 3 under them (coll sum 3371 -> 3329 per course), and
  the gate judge gives a pass THROUGH the buoy (|gLat| <= 128 = one
  32-unit cell at the normals' 64 scale) the benefit of the doubt. The
  NPC probes' `collVal == 3` pass-through is dead code now, harmless.
  The chaser has stuck-recovery (reverse out after ~2s wedged - it could
  deterministically grind forever on the start-line rope pocket). The
  course select draws the highlighted course's 48x48 minimap (painter
  orientation; palette row 7 = CGRAM 112-127; chars after the sky rows;
  csMini/csMiniPal re-DMA'd per cursor move). START in a race =
  PAUSE (attract races skip the countdown AND the finish - raceState
  forced to 1, ltState 3, the lap-3 check gated on !attract - and
  overtaken NPCs respawn 4 waypoints ahead with progress pinned above the
  player's, so the demo overtakes recycled traffic forever; the chaser
  FEATHERS small corrections - apFine steers at quarter rate - because a
  single full-rate loop tick is 2 binary degrees and pans the far texture
  several pixels in one step, which read as "ground jumps" tied to the
  bang-bang steering; real corners keep full authority. Measured: 172
  feathered vs 42 full-rate ticks over 1200 attract frames).
  PAUSE (the loop simply stops - physics/clock/phase freeze, HDMA replays
  the last tables, but waveHdma must be re-kicked per frame because the
  ISR's OAM DMA clobbers ch7); START resumes (full HUD redraw via the
  hudInit/h* cache reset), B quits to the title. Screens are EITHER mode 7
  with band overlays OR full mode 1 - no third option, by decision.
  mosaicSweep() ($2106, BG1+2+3) pixelates between states; sprites are
  not mosaic'd (hidden or tolerated). AUTOPILOT builds skip the flow
  (straight to course select, attract forced) so every harness still
  works; `attract` is the runtime chaser flag now. courseSelect (full-
  screen mode 1, console font + the 48x48 minimap, timer IRQ parked +
  HDMA off + $210D/COLDATA reset) is ARCADE's and TIME TRIALS' track
  picker; the championship runs every course in folder order instead.
  Race end: see THE FINISH below (the sky results table, START to leave;
  ARCADE then returns to the title). raceInit owns EVERY race variable;
  replay verified bit-identical (race 2 trace == race 1, flowshot pattern in
  git history). Menu text lives in BG1 map rows below the sky band - rows
  the race's mode switch never shows, so menu and race share the map with
  zero cleanup. Multi-course is the active project: docs/PLAN.md
  "Multi-course" has the agreed design; phases 1-3 done - two courses
  build today - now FIVE (Aug 28): 01_sunny_island (defaults), 02_sunset_cove
  (orange zenith, warm ambient, calm), 03_grey_lake (overcast, teal-grey
  water via its own sea_pattern.png, lawn-green sand, "flat" profile amp
  0.6), 04_deep_blue (deeper blue water with a hint of red, grey sand,
  "gentle" amp 1.5), 05_dawn_coast (pink zenith + pink ambient, pale sand,
  calm). All are geometric clones of course 1 - layouts are the open
  authoring work. Per-course water colour = a recoloured copy of the
  shared pattern (same indices, new PLTE) dropped in the course folder.
  06_twilight_sky (navy zenith over a sodium-amber horizon, amber ambient,
  black sand). Menu names may be up to 20 chars (MENU_NAME_MAX; menuBuf
  24). **The Mars experiment (Aug 29, REMOVED by git revert - do not
  re-add blind):** a 7th course with per-course gravity + a takeoff
  "bounce" kick. Findings worth keeping: (1) gravity alone is INVISIBLE -
  the in-water damping (skiVv -= skiVv>>1 per loop) leaves a crest at
  ~0.2 texel/loop, so height is capped by the wave amplitude whatever g
  is (measured ISLAND 6.8 vs half-g 7.1 texels); (2) a kick of (peak
  surface rise since landing * gain/8) at takeoff DOES work (4/8 at g24 =
  18.6 texels, 76% airborne, 1.9s flights) but the game around it does
  not: 2s flights carry the ski onto beaches where it sits at 0 km/h (no
  run-aground state) and gates get missed (no steering in the air). The
  user judged the result "not as good as imagined" and dropped it. If
  big air ever returns, it needs a shore-landing answer first (respawn at
  the last waypoint or a bounce back to water). Measure with
  tools/mesen/vtrace.lua + vstats.py, kept for this purpose.
  The ROM is a **512K LoROM** since Aug 29 (hdr.asm .ROMBANKS 16 /
  ROMSIZE $09) and courses cost ~11K each thanks to the tile pool (see
  the loader bullet + PLAN.md "ROM budget"). The menu lists them with an Up/Down cursor
  (cursor Up/Down + START verified in Mesen GUI mode with scripted input,
  Aug 28, after fixing the redraw - see the vblank-DMA gotcha). Phase 4 (palette
  + ambient per course, buoys on OBJ palette 5) landed Aug 28: OBJ
  palettes 0-3 + 5 are now loaded by courseLoad, NOT at boot (npc_pals is
  gone; ski_pal only seeds oamInitGfxSet). Next: phase 5, content.
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
  sand is collidable but there's no "run aground" state.
- PAL: accepted trade = runs slower (30Hz loop becomes 25Hz); must still boot.
- Real-hardware verified: EXTBG rendering, full HDMA stack, general play,
  BG3 clouds, power/HUD/start-tree (Aug-21 build). PENDING a CRT pass:
  the tall racers / OBJ name table 2, the scanline-IRQ mode switch + sand
  distance-fade CGRAM HDMA, the Photoshop rider art + 5-pair palettes,
  the teal shore rework, and everything from Sep 2: the two-stage IRQ's
  mid-frame BG3HOFS write (active line 32), BG3 enabled in the band's TM,
  the 2bpp sky font (results table + intro card).
- CPU: ~45% of the 2-frame loop free; ROM ~58% free; CGRAM map in README.
