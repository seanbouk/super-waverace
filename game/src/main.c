/*---------------------------------------------------------------------------------
    Super Waverace — god-mode camera over the rolling sea

    Seven HDMA channels per frame:
      ch1 TM      ($212C) : sky/sea split            (baked ROM, per phase)
      ch2 COLDATA ($2132) : crest glow               (baked ROM, per phase)
      ch3 M7A     ($211B) : a·cos(θ)                 (built each frame, WRAM)
      ch4 M7C     ($211D) : -a·sin(θ)                (built)
      ch5 M7X     ($211F) : px + d·sin(θ)            (built)
      ch6 M7Y     ($2120) : py + d·cos(θ)            (built)
      ch7 M7HOFS  ($210D) : M7X - 128                (built)

    buildCamTables (camera.asm) rebuilds ch3-7 from the baked per-phase
    distance/scale arrays using the hardware multiplier — roughly a frame and
    a half of CPU, so the main loop runs at ~2 frames per iteration, which is
    also the sea's phase cadence. Tables are double-buffered; the HDMA source
    pointers flip in vblank.

    The wave field is defined in view space (swell always rolls toward the
    camera), which is what keeps the baked raycast valid under rotation.

    Controls: d-pad up/down = forward/back, left/right = turn,
              L/R shoulders = strafe.
---------------------------------------------------------------------------------*/
#include <snes.h>
#include "wavedata.h"

extern char sea_patterns, sea_patterns_end, sea_map, sea_palette;
extern void buildCamTables(void);

// ---- camera state shared with camera.asm (accessed via long addressing) ----
u8 camTheta;
u16 camPX, camPY;
u16 camPhaseOff, camBufOff;
s16 camSinVal, camCosVal;
// asm internals
u8 camSinMag, camCosMag, camSinNeg, camSinNegInv, camCosNeg;
u16 camTmp, camTmp2, camA16, camD16, camLineCt;
// five double-buffered HDMA tables: stride 904 per register, 452 per buffer
u8 camTabs[4520];

#define TURN_SPEED 2
#define MOVE_SPEED 6
#define STRAFE_SPEED 4

dmaMemory dmaTM, dmaG, dmaT;
u16 pad0;
u16 tick;
u16 phase;
u8 rotTimer, rotOfs;

//---------------------------------------------------------------------------------
static void camTabsInitHeaders(void)
{
    u16 t, b, o;
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

    // ch1: TM sky/sea split
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

    REG_HDMAEN = 0xFE; // channels 1-7
}

//---------------------------------------------------------------------------------
int main(void)
{
    waveTablesInit();
    camTabsInitHeaders();

    bgInitMapTileSet7(&sea_patterns, &sea_map, &sea_palette,
                      (&sea_patterns_end - &sea_patterns), 0x0000);

    setMode7(0);

    setPaletteColor(0, RGB8(248, 168, 96));

    // Additive colour math on BG1 with the fixed colour = crest glow
    REG_CGWSEL = 0x00;
    REG_CGADSUB = 0x01;

    setScreenOn();

    tick = 0;
    camTheta = 0;
    camPX = 512;
    camPY = 0;
    camSinVal = 0;
    camCosVal = 127;
    rotTimer = 0;
    rotOfs = 0;

    while (1)
    {
        pad0 = padsCurrent(0);

        if (pad0 & KEY_LEFT)
            camTheta -= TURN_SPEED;
        if (pad0 & KEY_RIGHT)
            camTheta += TURN_SPEED;
        if (pad0 & KEY_UP)
        {
            camPX += (MOVE_SPEED * camSinVal) >> 7;
            camPY += (MOVE_SPEED * camCosVal) >> 7;
        }
        if (pad0 & KEY_DOWN)
        {
            camPX -= (MOVE_SPEED * camSinVal) >> 7;
            camPY -= (MOVE_SPEED * camCosVal) >> 7;
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

        tick++;
        phase = tick & (WAVE_PHASES - 1);
        camPhaseOff = phase * WAVE_RAW_STRIDE;
        camBufOff = (tick & 1) ? 452 : 0;

        buildCamTables(); // ~1.5 frames of CPU

        WaitForVBlank();
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
