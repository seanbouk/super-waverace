-- tickshot.lua - tick-keyed equivalence harness: position trace + screenshots
-- keyed on the game's loop tick, NOT the frame count, so builds whose init
-- length differs (which shifts tick/frame alignment) still compare exactly.
-- Logs skiWX/skiWY/camTheta16 every 25 ticks to trace.csv; screenshots 4
-- frames after ticks 100/500/1000 (takeScreenshot lags reads by 3 frames).
-- GOTCHAS this script learned the hard way: (1) BSS is garbage until the
-- game's init runs, so tick reads nonsense for the first frames - arm only
-- after tick is seen < 5, or the garbage fires every trigger against a
-- black screen; (2) runs are only comparable with settings.json
-- Snes.RamPowerOnState=AllZeros - with Random, pre-existing uninitialised
-- reads make physics diverge RUN TO RUN on the same ROM (restore Random
-- after - zeros hide init bugs that hardware shows).
-- ENV (all bare hex, no 0x - tonumber rejects the prefix; addresses move
-- between builds, re-grep superwaverace.sym): TICKADDR, WXADDR, WYADDR,
-- THADDR, OUTDIR (trailing separator), TAG (names the outputs).
local outdir = os.getenv("OUTDIR") or "./"
local tag = os.getenv("TAG") or "run"
local tickAddr = tonumber(os.getenv("TICKADDR"), 16)
local wxAddr = tonumber(os.getenv("WXADDR"), 16)
local wyAddr = tonumber(os.getenv("WYADDR"), 16)
local thAddr = tonumber(os.getenv("THADDR"), 16)

local targets = { 100, 500, 1000 }
local shotIdx = 1
local pending = -1
local lastLogged = -1
local armed = false
local frames = 0
local lines = {}

local function finish()
    local f = io.open(outdir .. tag .. "_trace.csv", "w")
    f:write(table.concat(lines, "\n"))
    f:close()
    emu.exit(0)
end

local function onFrame()
    frames = frames + 1
    if frames > 20000 then -- backstop: never ride the wall-clock timeout
        finish()
        return
    end
    local t = emu.read16(tickAddr, emu.memType.snesMemory)
    if not armed then
        if t < 5 then armed = true end
        return
    end
    if t % 25 == 0 and t ~= lastLogged then
        lastLogged = t
        lines[#lines + 1] = string.format("%d,%d,%d,%d", t,
            emu.read16(wxAddr, emu.memType.snesMemory),
            emu.read16(wyAddr, emu.memType.snesMemory),
            emu.read16(thAddr, emu.memType.snesMemory))
    end
    if shotIdx <= #targets and pending < 0 and t >= targets[shotIdx] then
        pending = 4
    end
    if pending > 0 then
        pending = pending - 1
    elseif pending == 0 then
        local png = emu.takeScreenshot()
        local f = io.open(outdir .. tag .. "_t" .. targets[shotIdx] .. ".png", "wb")
        f:write(png)
        f:close()
        shotIdx = shotIdx + 1
        pending = -1
    end
    if t >= 1200 and shotIdx > #targets then
        finish()
    end
end

emu.addEventCallback(onFrame, emu.eventType.endFrame)
