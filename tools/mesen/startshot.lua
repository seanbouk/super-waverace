-- screenshots of the start grid and the first seconds of racing
local outdir = os.getenv("OUTDIR") or "./"
local shots = { [90]=1, [180]=1, [300]=1, [450]=1 }
local n = 0
emu.addEventCallback(function()
  n = n + 1
  if shots[n] then
    local f = io.open(outdir .. "start" .. n .. ".png", "wb")
    f:write(emu.takeScreenshot())
    f:close()
  end
  if n >= 460 then
    emu.exit(0)
  end
end, emu.eventType.endFrame)
