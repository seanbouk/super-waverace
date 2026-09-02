-- GUI-MODE ONLY (scripted input), RELEASE build (no chaser): walk title ->
-- menu -> ARCADE -> rider -> course -> race, then from HOLD_FROM hold Y
-- (reverse) and trace the vertical/wave state every frame to
-- OUTDIR/rev.txt: tick, phase, phaseAcc, skiY, skiVv, vAlong, waterRow,
-- camPY - plus a screenshot every 10 frames while reversing. Written to
-- chase the "camera shakes up and down when reversing" report. WRAM
-- addresses from superwaverace.sym - they MOVE between builds, re-grep.
local out = os.getenv("OUTDIR") or "./"
local HOLD_FROM = tonumber(os.getenv("HOLD_FROM") or "1700")
local HOLD_TO = HOLD_FROM + 420
local A = {
  tick = 0x7E3C75, phase = 0x7E3C77, phaseAcc = 0x7E3C79, skiY = 0x7E3C56,
  skiVv = 0x7E3C58, vAlong = 0x7E3C7B, waterRow = 0x7E3C8A, camPY = 0x7E2012,
  raceState = 0x7E3D0B,
}
local frames = 0
local presses = {
  {450, 458, "start"}, -- title -> menu
  {520, 528, "down"},  -- TIME TRIALS
  {560, 568, "down"},  -- ARCADE
  {600, 608, "start"}, -- -> rider select
  {760, 768, "start"}, -- confirm rider 1 -> course select
  {920, 928, "start"}, -- SUNNY ISLAND -> race
}
local function r16(a) return emu.read16(a, emu.memType.snesMemory, true) end
local function onPoll()
  for _, pr in ipairs(presses) do
    if frames >= pr[1] and frames < pr[2] then
      local t = {}; t[pr[3]] = true
      pcall(emu.setInput, t, 0)
    end
  end
  if frames >= HOLD_FROM and frames < HOLD_TO then
    pcall(emu.setInput, {y = true}, 0)
  end
end
local function onFrame()
  frames = frames + 1
  if frames >= HOLD_FROM - 60 and frames < HOLD_TO + 60 then
    local fh = io.open(out .. "rev.txt", "a")
    fh:write(string.format("f%05d tick=%d ph=%d acc=%d skiY=%d vv=%d vA=%d wr=%d camPY=%d rs=%d%s\n",
      frames, emu.read16(A.tick, emu.memType.snesMemory),
      emu.read(A.phase, emu.memType.snesMemory),
      emu.read16(A.phaseAcc, emu.memType.snesMemory),
      r16(A.skiY), r16(A.skiVv), r16(A.vAlong),
      emu.read(A.waterRow, emu.memType.snesMemory),
      emu.read16(A.camPY, emu.memType.snesMemory),
      emu.read(A.raceState, emu.memType.snesMemory),
      (frames >= HOLD_FROM and frames < HOLD_TO) and " Y" or ""))
    fh:close()
    if frames % 10 == 0 then
      local sh = io.open(out .. string.format("r%05d.png", frames), "wb")
      sh:write(emu.takeScreenshot()); sh:close()
    end
  end
end
emu.addEventCallback(onPoll, emu.eventType.inputPolled)
emu.addEventCallback(onFrame, emu.eventType.endFrame)
