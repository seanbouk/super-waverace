# Headless Mesen 2 test scripts

Battle-tested Lua harnesses for `Mesen.exe --testrunner --timeout=N <rom> <script>`.
All write into `OUTDIR` (env var, must end with a path separator; defaults to `./`).
Most need a build with `AUTOPILOT 1` in main.c (scripted input doesn't work in
testrunner mode). Remember the two measurement gotchas in CLAUDE.md:
takeScreenshot lags OAM reads by exactly 3 frames, and loop-tick counts over a
fixed frame window (tickrate.lua) are the only trustworthy before/after numbers.

- `raceshot.lua`  — screenshots across a full 3-lap autopilot race (~100s)
- `startshot.lua` — screenshots of the start grid / first seconds
- `tickrate.lua`  — loop throughput: tick counter value after exactly 2000
  frames. TICKADDR = tick's WRAM address from superwaverace.sym (hex, e.g.
  7E3C68 — it MOVES between builds, always re-grep), TICKTAG names the output.
- `counters.lua`  — logs two u16 WRAM counters every 500 frames (dual-run
  equivalence harnesses: calls/bad). ADDR_CALLS / ADDR_BAD env vars, hex.
- `oamdump.lua`   — sprites 0-25 (x/y/tile/attr) + screenshot at fixed frames.
- `vtrace.lua` + `vstats.py` — per-frame vertical physics trace (skiY, surf88,
  skiVv + one extra u16; env TICKADDR/YADDR/SURFADDR/VVADDR/GRAVADDR/OUTDIR/TAG)
  and its summary (airborne %, max height, launch speed, longest flight). Use
  it before trusting any feel change - see CLAUDE.md "Mars experiment".
- `flownav.lua` / `ttnav.lua` / `mininav.lua` — GUI-mode scripted-input
  walks of the game flow (title>menu>pages>race>pause>quit / the Time
  Trials path incl. rider select / the course-select minimap cursor walk).
  Frame-timed presses + screenshots; RETIME THE PRESS TABLES when screens
  or timings change. Run like menuinput.lua; kill Mesen when done.
- `champnav.lua` — GUI-mode walk into CHAMPIONSHIP with a `CHAMP_AUTO 1`
  build (main.c): the chaser then drives all six races and every page
  auto-advances. Logs each flow-state change (raceMode/champOn/champRace/
  champStage/raceState + both points tables) and a PROG line every 600
  frames (tick/position/speed/lap/stuck - tells a wedged chaser from a
  hang) to OUTDIR/log.txt, screenshots ~100 frames after each change and
  every 1800 frames, writes DONE when the final standings hand back to
  the title (~65K frames; set Mesen's EmulationSpeed to 0 = unthrottled
  for the run). `ARCADE=1` in the env walks the ARCADE route instead
  (rider + course select, one chaser-driven race, the results page).
  WRAM addresses in its header MOVE between builds.
- `menuinput.lua` — GUI MODE ONLY (no --testrunner): scripted pad input via
  emu.setInput for the course-select menu (Down, then Start) + screenshots.
  Needs Snes.Port1.Type = SnesController in Mesen's settings; kill Mesen when done.
  Only valid for comparing builds with identical timing (see CLAUDE.md).
- `tickshot.lua`  — TICK-keyed position trace + screenshots: the equivalence
  harness for builds whose INIT length differs (fixed-frame comparison is
  invalid there — init shifts tick/frame alignment). Screenshots still
  jitter by ±1 frame (HUD clock digits, odd sea rows); the trace is the
  ground truth. TICKADDR/WXADDR/WYADDR/THADDR/OUTDIR/TAG env vars.
- `tabdump.lua`   — dumps camTabs (7200 bytes, the renderer's whole HDMA
  output) at tick 300, plus the $7F wave arrays with DUMP7F=1. The
  jitter-free way to prove two builds render identically. TICKADDR/TABADDR.

Dual-build equivalence runs need `Snes.RamPowerOnState: "AllZeros"` in
Mesen's settings.json for the duration (there are latent read-before-write
reads; with Random they make same-ROM runs diverge). RESTORE "Random" after
— zeros hide init bugs that hardware shows.
