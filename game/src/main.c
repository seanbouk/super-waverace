/*---------------------------------------------------------------------------------
    Super Waverace — phase 3: the rolling sea

    Each frame, four HDMA channels replay a pre-baked per-scanline script:
      TM      ($212C) : backdrop-only above the horizon, BG1 below
      M7A     ($211B) : horizontal scale (perspective width) per scanline
      M7Y     ($2120) : which texture row the scanline samples — with the matrix
                        B=C=D zeroed, the sampled row IS the M7Y value, which is
                        the first ray/wave crossing from the bake-time raycast
      COLDATA ($2132) : additive white per scanline — crest glow (each scanline
                        is a single distance, so whole-row brightening is right)
    Cycling through the baked phase tables rolls the sea; rotating the deep-blue
    palette entries makes the water surface flow independently of the swell.
---------------------------------------------------------------------------------*/
#include <snes.h>
#include "wavedata.h"

extern char sea_patterns, sea_patterns_end, sea_map, sea_palette;

dmaMemory dmaA, dmaY, dmaTM, dmaG;
u16 pad0;
u16 tick;
u16 phase;
u8 tickShift;
u8 rotTimer, rotOfs;

//---------------------------------------------------------------------------------
void waveHdma(u16 ph)
{
    dmaTM.mem.p = waveTM[ph];
    dmaA.mem.p = waveA[ph];
    dmaY.mem.p = waveY[ph];
    dmaG.mem.p = waveG[ph];

    REG_HDMAEN = 0;

    // Static matrix: B = C = D = 0, so texture row == M7Y exactly
    REG_M7B = 0;
    REG_M7B = 0;
    REG_M7C = 0;
    REG_M7C = 0;
    REG_M7D = 0;
    REG_M7D = 0;

    // Center X = 512 (map middle); HOFS = 512-128 puts screen centre there
    REG_M7X = 512 & 255;
    REG_M7X = 512 >> 8;
    REG_M7HOFS = 384 & 255;
    REG_M7HOFS = 384 >> 8;
    REG_M7VOFS = 0;
    REG_M7VOFS = 0;

    // Channel 1: TM — sky/sea split (1 byte to $212C)
    REG_DMAP1 = 0x00;
    REG_BBAD1 = 0x2C;
    REG_A1T1LH = dmaTM.mem.c.addr;
    REG_A1B1 = dmaTM.mem.c.bank;

    // Channel 2: M7A — write-twice (2 bytes to $211B)
    REG_DMAP2 = 0x02;
    REG_BBAD2 = 0x1B;
    REG_A1T2LH = dmaA.mem.c.addr;
    REG_A1B2 = dmaA.mem.c.bank;

    // Channel 3: M7Y — write-twice (2 bytes to $2120)
    REG_DMAP3 = 0x02;
    REG_BBAD3 = 0x20;
    REG_A1T3LH = dmaY.mem.c.addr;
    REG_A1B3 = dmaY.mem.c.bank;

    // Channel 4: COLDATA — crest glow (1 byte to $2132)
    REG_DMAP4 = 0x00;
    REG_BBAD4 = 0x32;
    REG_A1T4LH = dmaG.mem.c.addr;
    REG_A1B4 = dmaG.mem.c.bank;

    REG_HDMAEN = 0x1E; // channels 1+2+3+4
}

//---------------------------------------------------------------------------------
int main(void)
{
    waveTablesInit();

    // Sea texture: 1024x1024 mode 7 map at VRAM 0x0000
    bgInitMapTileSet7(&sea_patterns, &sea_map, &sea_palette,
                      (&sea_patterns_end - &sea_patterns), 0x0000);

    setMode7(0);

    // Backdrop above the horizon: dusk sky (palette index 0 is unused by the art)
    setPaletteColor(0, RGB8(248, 168, 96));

    // Additive colour math on BG1 with the fixed colour = crest glow
    REG_CGWSEL = 0x00;  // fixed colour operand, math always on
    REG_CGADSUB = 0x01; // add, BG1 only

    setScreenOn();

    tick = 0;
    tickShift = WAVE_TICK_SHIFT;
    rotTimer = 0;
    rotOfs = 0;

    while (1)
    {
        pad0 = padsCurrent(0);

        // Up = choppy fast sea, down = lazy swell
        if (pad0 & KEY_UP)
            tickShift = WAVE_TICK_SHIFT > 0 ? WAVE_TICK_SHIFT - 1 : 0;
        else if (pad0 & KEY_DOWN)
            tickShift = WAVE_TICK_SHIFT + 1;
        else
            tickShift = WAVE_TICK_SHIFT;

        tick++;
        phase = (tick >> tickShift) & (WAVE_PHASES - 1);

        waveHdma(phase);
        WaitForVBlank();

        // Palette rotation for surface flow (vblank-safe: right after NMI)
#if WAVE_ROT_FRAMES > 0
        rotTimer++;
        if (rotTimer >= WAVE_ROT_FRAMES)
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
