-- GUI-MODE ONLY (scripted input): walk title -> menu -> CHAMPIONSHIP ->
-- rider select, then let a CHAMP_AUTO 1 build cycle all six races hands-
-- free. Logs every flow-state change (raceMode/champOn/champRace/
-- champStage/raceState + the points tables) to OUTDIR/log.txt and shoots
-- a screenshot ~100 frames after each change. WRAM addresses from
-- superwaverace.sym - they MOVE between builds, re-grep. Kill Mesen when
-- the log says DONE.
local out = os.getenv("OUTDIR") or "./"
local A = {
  raceState = 0x7E3D0B, raceMode = 0x7E3D59, champOn = 0x7E3D66,
  champRace = 0x7E3D67, champStage = 0x7E3D68, champPts = 0x7E3D69,
  racePts = 0x7E3D6D,
  -- progress probes (a "PROG" line every 600 frames tells a wedged
  -- chaser from a hung CPU): tick, skiWX/WY, vAlong, lapCount, pProg
  tick = 0x7E3C75, skiWX = 0x7E3CA7, skiWY = 0x7E3CA9, vAlong = 0x7E3C7B,
  lapCount = 0x7E3CBB, pProg = 0x7E3D1D, apStuck = 0x7E3EE3,
  -- finish bookkeeping: a "FIN" line whenever finCount changes, with
  -- every rider's progress (NPC laps count waypoint 0 like the player's)
  finCount = 0x7E3D94, npcProg = 0x7E3D1F, npcLap = 0x7E3D8A,
}
local prevFin = -1
local frames = 0
local presses = {
  {450, 458, "start"}, -- title -> menu
  {520, 528, "start"}, -- confirm CHAMPIONSHIP (cursor rests on it)
  {680, 688, "start"}, -- rider select: confirm rider 1
}
if os.getenv("ARCADE") then -- the ARCADE route instead (results page)
  presses = {
    {450, 458, "start"}, -- title -> menu
    {520, 528, "down"},  -- TIME TRIALS
    {560, 568, "down"},  -- ARCADE
    {600, 608, "start"}, -- -> rider select
    {760, 768, "start"}, -- confirm rider 1 -> course select
    {920, 928, "start"}, -- SUNNY ISLAND -> race (the chaser drives)
  }
end
local prev = ""
local shots = {}
local done = false
local seenFinish = false
local function rd(a) return emu.read(a, emu.memType.snesMemory) end
local function onPoll()
  for _, pr in ipairs(presses) do
    if frames >= pr[1] and frames < pr[2] then
      local t = {}; t[pr[3]] = true
      pcall(emu.setInput, t, 0)
    end
  end
end
local function onFrame()
  frames = frames + 1
  local st = string.format("mode=%d on=%d race=%d stage=%d rs=%d",
    rd(A.raceMode), rd(A.champOn), rd(A.champRace), rd(A.champStage),
    rd(A.raceState))
  if st ~= prev then
    local pts = string.format(" pts=%d/%d/%d/%d last=%d/%d/%d/%d",
      rd(A.champPts), rd(A.champPts+1), rd(A.champPts+2), rd(A.champPts+3),
      rd(A.racePts), rd(A.racePts+1), rd(A.racePts+2), rd(A.racePts+3))
    local fh = io.open(out .. "log.txt", "a")
    fh:write(string.format("f%06d %s%s\n", frames, st, pts)); fh:close()
    shots[frames + 100] = st:gsub("[^%w]", "_")
    if st:find("rs=2") then seenFinish = true end
    -- DONE = back on the title after at least one finish (both routes)
    if not done and seenFinish and st:find("mode=1 on=0") then
      done = true
      shots[frames + 400] = "title_again"
      local fh2 = io.open(out .. "log.txt", "a")
      fh2:write("DONE\n"); fh2:close()
    end
    prev = st
  end
  local fc = rd(A.finCount)
  if fc ~= prevFin then
    prevFin = fc
    local fh = io.open(out .. "log.txt", "a")
    fh:write(string.format("f%06d FIN count=%d pProg=%d npcProg=%d/%d/%d npcLap=%d/%d/%d\n",
      frames, fc, emu.read16(A.pProg, emu.memType.snesMemory),
      emu.read16(A.npcProg, emu.memType.snesMemory),
      emu.read16(A.npcProg + 2, emu.memType.snesMemory),
      emu.read16(A.npcProg + 4, emu.memType.snesMemory),
      rd(A.npcLap), rd(A.npcLap + 1), rd(A.npcLap + 2)))
    fh:close()
    if fc > 0 then shots[frames + 60] = "fin" .. fc end
  end
  if frames % 600 == 0 then
    local fh = io.open(out .. "log.txt", "a")
    fh:write(string.format("f%06d PROG tick=%d x=%d y=%d v=%d lap=%d prog=%d stuck=%d\n",
      frames, emu.read16(A.tick, emu.memType.snesMemory),
      emu.read16(A.skiWX, emu.memType.snesMemory),
      emu.read16(A.skiWY, emu.memType.snesMemory),
      emu.read16(A.vAlong, emu.memType.snesMemory, true),
      rd(A.lapCount), emu.read16(A.pProg, emu.memType.snesMemory),
      rd(A.apStuck)))
    fh:close()
    if frames % 1800 == 0 then shots[frames] = "periodic" end
  end
  local tag = shots[frames]
  if tag then
    local fh = io.open(out .. string.format("f%06d_%s.png", frames, tag), "wb")
    fh:write(emu.takeScreenshot()); fh:close()
  end
end
emu.addEventCallback(onPoll, emu.eventType.inputPolled)
emu.addEventCallback(onFrame, emu.eventType.endFrame)
