-- screenshots across a full 3-lap autopilot race (run with --timeout=140)
local outdir = os.getenv("OUTDIR") or "./"
local shots = { [60]=1, [150]=1, [400]=1, [1200]=1, [2100]=1,
                [3300]=1, [4200]=1, [5100]=1, [5900]=1 }
local n = 0
emu.addEventCallback(function()
  n = n + 1
  if shots[n] then
    local f = io.open(outdir .. "race" .. n .. ".png", "wb")
    f:write(emu.takeScreenshot())
    f:close()
  end
  if n >= 6000 then
    emu.exit(0)
  end
end, emu.eventType.endFrame)
