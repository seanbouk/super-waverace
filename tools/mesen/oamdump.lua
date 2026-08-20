-- sprites 0-25 (x/y/tile/attr) + screenshot at fixed frames. Tag pre/post
-- runs via TAG. CAUTION: only compares meaningfully between builds with
-- identical timing - a faster loop shifts tick/frame alignment and every
-- physics-driven sprite legitimately moves (see CLAUDE.md).
local outdir = os.getenv("OUTDIR") or "./"
local tag = os.getenv("TAG") or "pre"
local log = io.open(outdir .. "oam_" .. tag .. ".log", "w")
local frames = { [400]=1, [900]=1, [1400]=1 }
local n = 0
emu.addEventCallback(function()
  n = n + 1
  if frames[n] then
    local f = io.open(outdir .. "oam_" .. tag .. "_" .. n .. ".png", "wb")
    f:write(emu.takeScreenshot())
    f:close()
    log:write("frame " .. n .. "\n")
    for i = 0, 25 do
      log:write(string.format("  s%02d %3d %3d %3d %02X\n", i,
        emu.read(i*4+0, emu.memType.snesSpriteRam),
        emu.read(i*4+1, emu.memType.snesSpriteRam),
        emu.read(i*4+2, emu.memType.snesSpriteRam),
        emu.read(i*4+3, emu.memType.snesSpriteRam)))
    end
    log:flush()
  end
  if n > 1400 then
    log:close()
    emu.exit(0)
  end
end, emu.eventType.endFrame)
