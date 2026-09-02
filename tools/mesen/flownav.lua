local out = "C:/Users/Sean/AppData/Local/Temp/claude/C--Users-Sean-Downloads-super-waverace/4cb30ab5-3928-4d8b-8c61-a2ad969e5cf1/scratchpad/flow/"
local frames = 0
-- {fromFrame, toFrame, button}
local presses = {
  {450, 458, "start"},  -- title -> menu
  {520, 528, "down"},   -- cursor to TIME TRIALS
  {560, 568, "down"},   -- cursor to 2P VS.
  {600, 608, "start"},  -- -> placeholder page
  {720, 728, "b"},      -- back (returns with menu open)
  {820, 828, "up"},     -- cursor to TIME TRIALS
  {880, 888, "start"},  -- -> course select
  {980, 988, "start"},  -- -> race (SUNNY ISLAND)
  {1700, 1708, "start"},-- pause
  {1800, 1808, "start"},-- resume
  {1900, 1908, "start"},-- pause again
  {2000, 2008, "b"},    -- quit to title
}
local shots = {90, 420, 500, 590, 700, 800, 870, 950, 1600, 1770, 1870, 1960, 2150}
local function onPoll()
  for _, pr in ipairs(presses) do
    if frames >= pr[1] and frames < pr[2] then
      local t = {}
      t[pr[3]] = true
      pcall(emu.setInput, t, 0)
    end
  end
end
local function onFrame()
  frames = frames + 1
  for _, f in ipairs(shots) do
    if frames == f then
      local fh = io.open(out .. string.format("f%04d.png", f), "wb")
      fh:write(emu.takeScreenshot()); fh:close()
    end
  end
end
emu.addEventCallback(onPoll, emu.eventType.inputPolled)
emu.addEventCallback(onFrame, emu.eventType.endFrame)
