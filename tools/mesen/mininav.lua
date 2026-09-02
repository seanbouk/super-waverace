local out = os.getenv("OUTDIR") or "./"
local frames = 0
local presses = {
  {450, 458, "start"},  -- title -> menu
  {520, 528, "down"},   -- to TIME TRIALS
  {540, 548, "down"},   -- to ARCADE
  {580, 588, "start"},  -- -> rider select
  {650, 658, "start"},  -- -> course select (minimap: SUNNY ISLAND)
  {780, 788, "down"},   -- SUNSET COVE
  {860, 868, "down"},   -- GREY LAKE
  {940, 948, "down"},   -- DEEP BLUE
  {1020, 1028, "down"}, -- DAWN COAST
  {1100, 1108, "down"}, -- TWILIGHT SKY
}
local shots = {760, 920, 1080, 1200}
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
      local fh = io.open(out .. string.format("mm%04d.png", f), "wb")
      fh:write(emu.takeScreenshot()); fh:close()
    end
  end
end
emu.addEventCallback(onPoll, emu.eventType.inputPolled)
emu.addEventCallback(onFrame, emu.eventType.endFrame)
