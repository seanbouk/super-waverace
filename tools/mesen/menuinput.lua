-- menuinput.lua - scripted controller input for the course-select menu.
-- GUI MODE ONLY (no --testrunner: that mode ignores emu.setInput) and
-- Mesen's Snes.Port1.Type must be SnesController. Run:
--   OUTDIR=<dir with trailing slash> Mesen.exe game/superwaverace.sfc tools/mesen/menuinput.lua
-- then kill Mesen after ~10s (GUI mode never exits by itself).
-- Holds Down on frames 120-130 and Start on 250-260; screenshots at 60
-- (menu), 200 (cursor should sit on the second course) and 420 (that
-- course's race should be up). Adapt the button tables for other menus.
local out = os.getenv("OUTDIR") or "./"
local frames = 0
local log = io.open(out .. "log.txt", "w")
local function press(tbl)
  local ok, err = pcall(function() emu.setInput(tbl, 0) end)
  if not ok then ok, err = pcall(function() emu.setInput(0, tbl) end) end
  if not ok and frames % 10 == 0 then log:write("setInput failed: " .. tostring(err) .. "\n"); log:flush() end
end
local function onPoll()
  if frames >= 120 and frames < 130 then press({down = true}) end
  if frames >= 250 and frames < 260 then press({start = true}) end
end
local function shot(name)
  local f = io.open(out .. name, "wb"); f:write(emu.takeScreenshot()); f:close()
end
local function onFrame()
  frames = frames + 1
  if frames == 60 then shot("f060_menu.png") end
  if frames == 200 then shot("f200_after_down.png") end
  if frames == 420 then shot("f420_after_start.png") end
  if frames == 421 then log:write("done\n"); log:close() end
end
emu.addEventCallback(onPoll, emu.eventType.inputPolled)
emu.addEventCallback(onFrame, emu.eventType.endFrame)
