/*---------------------------------------------------------------------------------
    Super Waverace — god-mode camera over the rolling sea

    HDMA channels per frame:
      ch0 BG mode ($2105) : mode 1 UI band on top, mode 7 below   (static ROM)
      ch1 TM      ($212C) : UI band / sky / sea split             (baked ROM)
      ch2 COLDATA ($2132) : crest glow                            (baked ROM)
      ch3 M7A, ch4 M7C, ch5 M7X, ch6 M7Y, ch7 HOFS               (built, WRAM)

    buildCamTables (camera.asm) rebuilds ch3-7 from the baked per-phase
    distance/scale arrays using the hardware multiplier — sky lines skipped,
    double-buffered, pointers flipped in vblank.

    The wave phase advances with time AND with forward/backward motion, so
    driving at speed skips over the waves.

    Controls: d-pad up/down = forward/back, left/right = turn,
              L/R shoulders = strafe.
---------------------------------------------------------------------------------*/
#include <snes.h>
#include "wavedata.h"
#include "ui.h"

extern char sea_patterns, sea_patterns_end, sea_map, sea_palette;
extern void buildCamTables(void);

// ---- camera state shared with camera.asm (accessed via long addressing) ----
u8 camTheta;
u16 camPX, camPY;
u16 camPhaseOff, camBufOff;
u16 camSrcOff, camDstOff, camBlk1Ct;
s16 camSinVal, camCosVal;
// asm internals
u8 camSinMag, camCosMag, camSinNeg, camCosNeg;
// five double-buffered HDMA tables: stride 904 per register, 452 per buffer
u8 camTabs[4520];

#define TURN_SPEED 2
#define MOVE_SPEED 6
#define STRAFE_SPEED 4

#ifndef REG_SLHV
#define REG_SLHV (*(vuint8 *)0x2137)
#endif
#ifndef REG_OPVCT
#define REG_OPVCT (*(vuint8 *)0x213D)
#endif

dmaMemory dmaTM, dmaG, dmaT;
u16 pad0;
u16 tick;
u16 phase;
u16 phaseAcc;
s16 fwdVel;
u8 rotTimer, rotOfs;
u8 skip;
// profiling: scanlines + frames consumed by the last table build
u16 profStartLine, profLines, profFrames;
u16 vbl0;

//---------------------------------------------------------------------------------
static u16 scanline(void)
{
    u8 lo, hi;
    (void)REG_STAT78; // reset the OPVCT byte toggle
    (void)REG_SLHV;   // latch H/V counters
    lo = REG_OPVCT;
    hi = REG_OPVCT & 1;
    return ((u16)hi << 8) | lo;
}

//---------------------------------------------------------------------------------
static void camTabsInitHeaders(void)
{
    u16 t, b, o, i;
    // zero everything first: BSS is not cleared, and the never-rebuilt
    // sky/UI-band entries must be benign ($210D doubles as the UI band's
    // BG1 scroll, so junk there shears the text)
    for (i = 0; i < sizeof(camTabs); i++)
        camTabs[i] = 0;
    for (t = 0; t < 5; t++)
        for (b = 0; b < 2; b++)
        {
            o = t * 904 + b * 452;
            camTabs[o] = 0xFF;       // 127 per-line entries
            camTabs[o + 255] = 0xE1; // 97 per-line entries
            camTabs[o + 450] = 0x00; // end
        }
}

//---------------------------------------------------------------------------------
static void waveHdma(u16 ph, u16 bufOff)
{
    dmaTM.mem.p = waveTM[ph];
    dmaG.mem.p = waveG[ph];

    REG_HDMAEN = 0;

    // Static matrix parts: B = D = 0 (so rows come from M7Y alone), VOFS = 0
    REG_M7B = 0;
    REG_M7B = 0;
    REG_M7D = 0;
    REG_M7D = 0;
    REG_M7VOFS = 0;
    REG_M7VOFS = 0;

    // ch0: BG mode split for the text UI band
    uiHdma();

    // ch1: TM UI/sky/sea split
    REG_DMAP1 = 0x00;
    REG_BBAD1 = 0x2C;
    REG_A1T1LH = dmaTM.mem.c.addr;
    REG_A1B1 = dmaTM.mem.c.bank;

    // ch2: COLDATA crest glow
    REG_DMAP2 = 0x00;
    REG_BBAD2 = 0x32;
    REG_A1T2LH = dmaG.mem.c.addr;
    REG_A1B2 = dmaG.mem.c.bank;

    // ch3-7: the five built tables (write-twice registers)
    dmaT.mem.p = camTabs + bufOff;
    REG_DMAP3 = 0x02;
    REG_BBAD3 = 0x1B; // M7A
    REG_A1T3LH = dmaT.mem.c.addr;
    REG_A1B3 = dmaT.mem.c.bank;

    dmaT.mem.p = camTabs + 904 + bufOff;
    REG_DMAP4 = 0x02;
    REG_BBAD4 = 0x1D; // M7C
    REG_A1T4LH = dmaT.mem.c.addr;
    REG_A1B4 = dmaT.mem.c.bank;

    dmaT.mem.p = camTabs + 1808 + bufOff;
    REG_DMAP5 = 0x02;
    REG_BBAD5 = 0x1F; // M7X
    REG_A1T5LH = dmaT.mem.c.addr;
    REG_A1B5 = dmaT.mem.c.bank;

    dmaT.mem.p = camTabs + 2712 + bufOff;
    REG_DMAP6 = 0x02;
    REG_BBAD6 = 0x20; // M7Y
    REG_A1T6LH = dmaT.mem.c.addr;
    REG_A1B6 = dmaT.mem.c.bank;

    dmaT.mem.p = camTabs + 3616 + bufOff;
    REG_DMAP7 = 0x02;
    REG_BBAD7 = 0x0D; // M7HOFS
    REG_A1T7LH = dmaT.mem.c.addr;
    REG_A1B7 = dmaT.mem.c.bank;

    REG_HDMAEN = 0xFF; // all eight channels
}

//---------------------------------------------------------------------------------
int main(void)
{
    waveTablesInit();
    camTabsInitHeaders();

    bgInitMapTileSet7(&sea_patterns, &sea_map, &sea_palette,
                      (&sea_patterns_end - &sea_patterns), 0x0000);

    setMode7(0);
    uiInit();

    setPaletteColor(0, RGB8(248, 168, 96));

    // Additive colour math on BG1 with the fixed colour = crest glow
    REG_CGWSEL = 0x00;
    REG_CGADSUB = 0x01;

    setScreenOn();

    tick = 0;
    phaseAcc = 0;
    camTheta = 0;
    camPX = 512;
    camPY = 0;
    camSinVal = 0;
    camCosVal = 127;
    rotTimer = 0;
    rotOfs = 0;
    profLines = 0;
    profFrames = 0;

// build-time debug: camera drives itself (verifies the engine in the
// emulator test runner, where scripted input is unavailable)
#define AUTOPILOT 0

    while (1)
    {
        pad0 = padsCurrent(0);
#if AUTOPILOT
        pad0 |= KEY_RIGHT | KEY_UP;
#endif

        if (pad0 & KEY_LEFT)
            camTheta -= TURN_SPEED;
        if (pad0 & KEY_RIGHT)
            camTheta += TURN_SPEED;

        fwdVel = 0;
        if (pad0 & KEY_UP)
            fwdVel = MOVE_SPEED;
        if (pad0 & KEY_DOWN)
            fwdVel = -MOVE_SPEED;
        if (fwdVel)
        {
            camPX += (fwdVel * camSinVal) >> 7;
            camPY += (fwdVel * camCosVal) >> 7;
        }
        if (pad0 & KEY_R)
        {
            camPX += (STRAFE_SPEED * camCosVal) >> 7;
            camPY -= (STRAFE_SPEED * camSinVal) >> 7;
        }
        if (pad0 & KEY_L)
        {
            camPX -= (STRAFE_SPEED * camCosVal) >> 7;
            camPY += (STRAFE_SPEED * camSinVal) >> 7;
        }

        // Wave phase: rolls with time, and with forward motion — driving
        // fast means skipping over the crests
        phaseAcc = (phaseAcc + WAVE_BASE_ROLL + fwdVel * WAVE_STEPS_PER_TEXEL)
                   & WAVE_PHASE_MASK;
        phase = phaseAcc >> 8;

        camPhaseOff = phase * WAVE_RAW_STRIDE;
        camBufOff = (tick & 1) ? 452 : 0;
        tick++;

        // skip building the sky lines — their table entries are never shown
        skip = waveSky[phase];
        if (skip > 126)
            skip = 126;
        camBlk1Ct = 127 - skip;
        camSrcOff = camPhaseOff + 2 * skip;
        camDstOff = camBufOff + 1 + 2 * skip;

        vbl0 = snes_vblank_count;
        profStartLine = scanline();
        buildCamTables();
        profFrames = snes_vblank_count - vbl0;
        profLines = profFrames * 262 + scanline() - profStartLine;

        if ((tick & 3) == 0)
        {
            uiPrint(0, 0, "X");
            uiPrintNum(1, 0, camPX & 1023, 4);
            uiPrint(6, 0, "Y");
            uiPrintNum(7, 0, camPY & 1023, 4);
            uiPrint(12, 0, "H");
            uiPrintNum(13, 0, camTheta, 3);
            uiPrint(0, 1, "BUILD");
            uiPrintNum(5, 1, profLines, 4);
            uiPrint(9, 1, "LN");
            uiPrintNum(12, 1, profFrames, 1);
            uiPrint(13, 1, "F PH");
            uiPrintNum(17, 1, phase, 2);
        }

        WaitForVBlank();
        uiFlush();
        waveHdma(phase, camBufOff);

#if WAVE_ROT_FRAMES > 0
        rotTimer++;
        if (rotTimer >= WAVE_ROT_FRAMES / 2) // loop runs ~every 2 frames
        {
            rotTimer = 0;
            rotOfs++;
            if (rotOfs >= WAVE_ROT_COUNT)
                rotOfs = 0;
            waveRotateStep(rotOfs);
        }
#endif
    }
    return 0;
}
