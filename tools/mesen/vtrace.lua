-- per-frame vertical physics trace: frame, tick, skiY, surf88, skiVv, courseGrav
-- ENV: OUTDIR, TAG, TICKADDR, YADDR, SURFADDR, VVADDR, GRAVADDR (bare hex)
local out = os.getenv("OUTDIR") or "./"
local tag = os.getenv("TAG") or "v"
local M = emu.memType.snesMemory
local A = {}
for _, k in ipairs({"TICKADDR", "YADDR", "SURFADDR", "VVADDR", "GRAVADDR"}) do A[k] = tonumber(os.getenv(k), 16) end
local frames, armed, lines = 0, false, {}
local function s16(v) if v >= 32768 then return v - 65536 end return v end
local function onFrame()
  frames = frames + 1
  local t = emu.read16(A.TICKADDR, M)
  if not armed then if t < 5 then armed = true end return end
  lines[#lines + 1] = string.format("%d,%d,%d,%d,%d,%d", frames, t,
    s16(emu.read16(A.YADDR, M)), s16(emu.read16(A.SURFADDR, M)), s16(emu.read16(A.VVADDR, M)), emu.read16(A.GRAVADDR, M))
  if #lines % 60 == 0 or t >= 900 then
    local f = io.open(out .. tag .. "_vtrace.csv", "w"); f:write(table.concat(lines, "\n")); f:close()
  end
end
emu.addEventCallback(onFrame, emu.eventType.endFrame)
