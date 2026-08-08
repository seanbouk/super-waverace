/*---------------------------------------------------------------------------------
    Super Waverace — phase 3: the rolling sea

    Each frame, three HDMA channels replay a pre-baked per-scanline script:
      TM  ($212C) : backdrop-only above the horizon, BG1 below
      M7A ($211B) : horizontal scale (perspective width) per scanline
      M7Y ($2120) : which texture row the scanline samples — with the matrix
                    B=C=D zeroed, the sampled row IS the M7Y value, which is
                    the first ray/wave crossing from the bake-time raycast.
    Cycling through the baked phase tables rolls the sea.
---------------------------------------------------------------------------------*/
#include <snes.h>
#include "wavedata.h"

extern char sea_patterns, sea_patterns_end, sea_map, sea_palette;

dmaMemory dmaA, dmaY, dmaTM;
u16 pad0;
u16 tick;
u16 speed;
u16 phase;

//---------------------------------------------------------------------------------
void waveHdma(u16 ph)
{
    dmaTM.mem.p = waveTM[ph];
    dmaA.mem.p = waveA[ph];
    dmaY.mem.p = waveY[ph];

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

    REG_HDMAEN = 0x0E; // channels 1+2+3
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

    setScreenOn();

    tick = 0;
    speed = 2;

    while (1)
    {
        pad0 = padsCurrent(0);

        // Up = choppy fast sea, down = lazy swell
        if (pad0 & KEY_UP)
            speed = 4;
        else if (pad0 & KEY_DOWN)
            speed = 1;
        else
            speed = 2;

        tick += speed;
        phase = (tick >> 2) & (WAVE_PHASES - 1);

        waveHdma(phase);
        WaitForVBlank();
    }
    return 0;
}
