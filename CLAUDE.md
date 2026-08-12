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
- The on-screen debug UI is the other half of verification: X/Y/H/V, BUILD
  profiler (262 lines/rebuild is the baseline), PH phase, WET/AIR, K/S/V
  physics row. If numbers look wrong in a screenshot, trust them over vibes.

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
- **EXTBG bit 7 is per PIXEL** (priority flag; colour = low 7 bits). The bake
  sets it on glow-exempt pixels. Priority order: S3 S2 2H S1 BG1 S0 2L.
- **The VBlank ISR's OAM DMA uses channel 7's registers** and fires only on
  the vblank that ends WaitForVBlank. Order in the main loop is load-bearing:
  sprite/OAM updates BEFORE WaitForVBlank; waveHdma (which reprograms all 8
  channel configs) immediately AFTER. Don't reorder.
- **$210D is shared**: M7HOFS and the UI band's BG1 scroll. Sky/UI-band rows
  of the built tables must stay zero (they're pre-zeroed, builder skips them).
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
  slots silently.
- **Windows line endings**: git checkout rewrites working files to CRLF;
  python patch scripts must read with universal newlines and write
  newline='\n'. Write files before asserting patch success, never after.

## Tuning knobs (game feel — user-driven, ask before big changes)

`game/src/main.c` top: TURN_SPEED, THRUST (drag >>4 sets top speed =
THRUST*16), GRAV, DIP, MAX_VV_UP/DOWN, splash retention (>>2 on entry),
grip (vSide -= vSide>>3) and rudder (vAlong += |vSide|>>3). Buoy scale bands
are a geometric ladder (192/268/382/534). Wave feel comes from
tools/wave_params.json via the wave lab.

## State / not yet done

- Race mode in progress — see docs/PLAN.md "Race mode" for the agreed design
  (waypoint progress, kinematic NPCs, rear-view art) and phase list. Phase 1
  (racing line + player laps/timing + waypoint autopilot) is done; NPCs,
  race flow, and multi-course are not.
- Buoy pass-sides (L/R) recorded but not judged; no opponents yet;
  no sound (jam judges music — PVSnesLib has an .it tracker driver, unused);
  sand is collidable but there's no "run aground" state; no title screen.
- PAL: accepted trade = runs slower (30Hz loop becomes 25Hz); must still boot.
- Real-hardware verified: EXTBG rendering, full HDMA stack, general play.
- CPU: ~45% of the 2-frame loop free; ROM ~58% free; CGRAM map in README.
