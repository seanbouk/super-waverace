local out = os.getenv("OUTDIR") or "./"
local frames = 0
local presses = {
  {450, 458, "start"},  -- title -> menu
  {520, 528, "down"},   -- to TIME TRIALS
  {560, 568, "start"},  -- -> rider select
  {700, 708, "right"},  -- pick rider 2
  {750, 758, "right"},  -- pick rider 3
  {820, 828, "start"},  -- -> course select
  {900, 908, "start"},  -- -> TT race
}
local shots = {650, 790, 880, 1400}
local function onPoll()
  for _, pr in ipairs(presses) do
    if frames >= pr[1] and frames < pr[2] then
      local t = {}; t[pr[3]] = true; pcall(emu.setInput, t, 0)
    end
  end
end
local function onFrame()
  frames = frames + 1
  for _, f in ipairs(shots) do
    if frames == f then
      local fh = io.open(out .. string.format("tt%04d.png", f), "wb")
      fh:write(emu.takeScreenshot()); fh:close()
    end
  end
end
emu.addEventCallback(onPoll, emu.eventType.inputPolled)
emu.addEventCallback(onFrame, emu.eventType.endFrame)
