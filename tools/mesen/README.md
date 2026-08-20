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
  Only valid for comparing builds with identical timing (see CLAUDE.md).
