-- screenshots at fixed frames (env OUTDIR, FRAMES = "600,900,1500"); for
-- testrunner AUTOPILOT builds. Rides --timeout out (emu.exit is unreliable).
local out = os.getenv("OUTDIR") or "./"
local want = {}
for f in string.gmatch(os.getenv("FRAMES") or "600,900,1500", "%d+") do want[tonumber(f)] = true end
local n = 0
emu.addEventCallback(function()
  n = n + 1
  if want[n] then
    local fh = io.open(out .. string.format("s%05d.png", n), "wb")
    fh:write(emu.takeScreenshot()); fh:close()
  end
end, emu.eventType.endFrame)
