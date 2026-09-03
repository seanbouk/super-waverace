-- GUI-MODE ONLY (scripted input): title -> menu -> ARCADE -> rider select,
-- where P2 joins (SELECT on pad 1 = the SPLIT_AUTO build's stand-in for
-- P2's START - Mesen 2.1.1's Lua setInput drives pad 1 whatever port index
-- it is given, measured), P1 confirms both, course select,
-- then the 2P split race. With a CHAMP_AUTO 1 + SPLIT_AUTO 1 build the
-- chaser drives P1 and P2 mirrors it, so the race finishes and the 2P
-- results page follows. Logs flow state every 300 frames + screenshots.
-- WRAM addresses from superwaverace.sym - they MOVE between builds.
local out = os.getenv("OUTDIR") or "./"
local A = {
  split = 0x7E2026, p2Join = 0x7E202B, pFin = 0x7E202D, svpFin = 0x7E21A0,
  raceState = 0x7E3ED1, raceMode = 0x7E3F1F, champStage = 0x7E3F2E,
  finCount = 0x7E3F5A,
}
local frames = 0
-- {from, to, button, port}
local presses = {
  {450, 458, "start", 0},  -- title -> menu
  {520, 528, "down", 0},   -- TIME TRIALS
  {560, 568, "down", 0},   -- ARCADE
  {600, 608, "start", 0},  -- -> rider select (up by ~700)
  {760, 768, "select", 0}, -- P2 joins (the SPLIT_AUTO shim: SELECT on pad 1
                           -- = P2's START; Mesen's Lua cannot drive pad 2)
  {860, 868, "start", 0},  -- P1 confirms both -> course select
  {1020, 1028, "start", 0}, -- SUNNY ISLAND -> the 2P race
  {16500, 16508, "b", 0},   -- (after the race: rider select -> menu, to see
                            -- the band's text over the attract race)
}
local shots = { [700]=1, [800]=1, [900]=1, [1100]=1, [1500]=1, [2500]=1,
  [4000]=1, [6000]=1, [8000]=1, [10000]=1, [12000]=1, [14000]=1, [16000]=1,
  [18000]=1, [20000]=1 }
local prev = ""
local function rd(a) return emu.read(a, emu.memType.snesMemory) end
local function onPoll()
  for _, pr in ipairs(presses) do
    if frames >= pr[1] and frames < pr[2] then
      local t = {}; t[pr[3]] = true
      pcall(emu.setInput, t, pr[4])
    end
  end
end
local function onFrame()
  frames = frames + 1
  local st = string.format("mode=%d split=%d join=%d rs=%d fin=%d/%d n=%d stage=%d",
    rd(A.raceMode), rd(A.split), rd(A.p2Join), rd(A.raceState), rd(A.pFin),
    rd(A.svpFin), rd(A.finCount), rd(A.champStage))
  if st ~= prev or frames % 600 == 0 then
    local fh = io.open(out .. "log.txt", "a")
    fh:write(string.format("f%06d %s\n", frames, st)); fh:close()
    if st ~= prev then shots[frames + 90] = 1 end
    prev = st
  end
  if shots[frames] then
    local fh = io.open(out .. string.format("f%06d.png", frames), "wb")
    fh:write(emu.takeScreenshot()); fh:close()
  end
end
emu.addEventCallback(onPoll, emu.eventType.inputPolled)
emu.addEventCallback(onFrame, emu.eventType.endFrame)
