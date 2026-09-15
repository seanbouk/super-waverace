-- sustain duty check: per-voice fraction of frames with ENVX > 0 over
-- frames 150-500. Plinky/faded notes = low duty; held notes = high.
-- This is the metric that catches the SNESMod no-envelope fade bug -
-- 40-frame snapshots (dspdump) read plinky voices as "between phrases".
-- env: OUTDIR
local outdir = os.getenv("OUTDIR") or "./"
local hits = { 0, 0, 0, 0, 0, 0, 0, 0 }
local n = 0

emu.addEventCallback(function()
  local f = emu.getState()["frameCount"]
  if f >= 150 and f <= 500 then
    n = n + 1
    for v = 0, 7 do
      if emu.read(v * 16 + 8, emu.memType.spcDspRegisters) > 0 then
        hits[v + 1] = hits[v + 1] + 1
      end
    end
  end
  if f == 500 then
    local parts = {}
    for v = 1, 8 do
      parts[#parts + 1] = string.format("v%d=%d%%", v - 1,
                                        math.floor(hits[v] * 100 / n))
    end
    local fh = io.open(outdir .. "duty.txt", "w")
    fh:write("ENVX>0 duty over " .. n .. " frames: "
             .. table.concat(parts, " "))
    fh:close()
  end
end, emu.eventType.endFrame)
