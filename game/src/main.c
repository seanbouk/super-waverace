/*---------------------------------------------------------------------------------
    Super Waverace — jet ski on the rolling sea

    HDMA channels per frame:
      ch0 BG mode ($2105)  : mode 1 UI band on top, mode 7 below   (static ROM)
      ch1 TM      ($212C)  : UI band / sky / sea split             (baked ROM)
      ch2 COLDATA ($2132)  : crest glow                            (baked ROM)
      ch3 M7A+B, ch4 M7C+D, ch5 M7X+Y, ch6 HOFS+VOFS : paired-register
          mode-3 streams built each frame by camera.asm (B/D/VOFS ride along
          as pre-zeroed words)
      ch7 WH0+WH1 ($2126)  : window waterline — clips the ski sprite below
          the water surface row, so the hull visibly sinks and bobs
          (note: the vblank ISR's OAM DMA uses ch7's registers; waveHdma
          reprograms them right after WaitForVBlank, before render starts)

    Jet ski physics: buoyancy spring toward (surface - dip) while in water,
    gravity when airborne. Thrust (B) only bites in the water; the heading
    can always change. Wave phase advances with time and with forward
    speed — driving fast skips across crests.
---------------------------------------------------------------------------------*/
#include <snes.h>
#include "wavedata.h"
#include "ui.h"

extern char sea_patterns, sea_patterns_end, sea_map, sea_palette;
extern char ski_tiles, ski_pal;
extern void buildCamTables(void);

// ---- camera state shared with camera.asm (accessed via long addressing) ----
u8 camTheta;
u16 camPX, camPY;
u16 camPhaseOff, camBufOff;
u16 camSrcOff, camDstOff, camBlk1Ct;
s16 camSinVal, camCosVal;
// asm internals
u8 camSinMag, camCosMag, camSinNeg, camCosNeg;
// four double-buffered paired-register HDMA tables, 4 bytes per scanline:
// stride 1800 per table (2 x 900), bases 0 / 1800 / 3600 / 5400
u8 camTabs[7200];

// ---- window waterline table (built each loop, tiny) ----
u8 winTab[10];

// ---- jet ski state (8.8 fixed unless noted) ----
s16 skiY;         // height above mean sea level
s16 skiVv;        // vertical velocity
s16 skiVX, skiVY; // world-space velocity
s16 fracX, fracY; // sub-texel position accumulators
u8 skiLean;       // 0 straight, 1 leaning
u8 skiFlip;       // lean direction (hflip)

#define TURN_SPEED 2
#define THRUST 144
#define GRAV 48       // 8.8 texels/loop^2 — snappy hops, not moon gravity
#define DIP 128       // rest waterline: 0.5 texel below surface
#define MAX_VV_UP 320   // launch clamp: small crisp hops
#define MAX_VV_DOWN 768 // falls can be faster than launches
#define MAX_DEPTH 768 // the water is thick: hard floor 3 texels under
#define SKI_X 112

#ifndef REG_SLHV
#define REG_SLHV (*(vuint8 *)0x2137)
#endif
#ifndef REG_OPVCT
#define REG_OPVCT (*(vuint8 *)0x213D)
#endif
#define REG_WOBJSEL (*(vuint8 *)0x2125)
#define REG_TMW (*(vuint8 *)0x212E)

dmaMemory dmaTM, dmaG, dmaT;
u16 pad0;
u16 tick;
u16 phase;
u16 phaseAcc;
s16 vAlong, vSide;
s16 surf88, diff88;
s16 sprTop;
u8 rotTimer, rotOfs;
u8 skip, waterRow, inWater, wasInWater;
s16 prevSin, prevCos;
u16 profStartLine, profLines, profFrames;
u16 vbl0;

//---------------------------------------------------------------------------------
static u16 scanline(void)
{
    u8 lo, hi;
    (void)REG_STAT78;
    (void)REG_SLHV;
    lo = REG_OPVCT;
    hi = REG_OPVCT & 1;
    return ((u16)hi << 8) | lo;
}

//---------------------------------------------------------------------------------
static void camTabsInitHeaders(void)
{
    u16 t, b, o, i;
    // zero everything: BSS is not cleared, the skipped sky/UI lines must be
    // benign, and the B/D/VOFS words of the paired entries must stay 0
    for (i = 0; i < sizeof(camTabs); i++)
        camTabs[i] = 0;
    for (t = 0; t < 4; t++)
        for (b = 0; b < 2; b++)
        {
            o = t * 1800 + b * 900;
            camTabs[o] = 0xFF;       // 127 four-byte entries
            camTabs[o + 509] = 0xE1; // 97 four-byte entries
            camTabs[o + 898] = 0x00; // end
        }
}

//---------------------------------------------------------------------------------
static void buildWinTab(u8 row)
{
    u8 *w = winTab;
    if (row > 127)
    {
        *w++ = 127;
        *w++ = 0xFF; // empty window (left > right): sprite visible
        *w++ = 0x00;
        row -= 127;
    }
    *w++ = row;
    *w++ = 0xFF;
    *w++ = 0x00;
    *w++ = 1; // from the waterline down: full-width window = sprite clipped
    *w++ = 0x00;
    *w++ = 0xFF;
    *w = 0x00;
}

//---------------------------------------------------------------------------------
static void waveHdma(u16 ph, u16 bufOff)
{
    dmaTM.mem.p = waveTM[ph];
    dmaG.mem.p = waveG[ph];

    REG_HDMAEN = 0;

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

    // ch3-6: paired-register streams (mode 3: p,p,p+1,p+1)
    dmaT.mem.p = camTabs + bufOff;
    REG_DMAP3 = 0x03;
    REG_BBAD3 = 0x1B; // M7A + M7B
    REG_A1T3LH = dmaT.mem.c.addr;
    REG_A1B3 = dmaT.mem.c.bank;

    dmaT.mem.p = camTabs + 1800 + bufOff;
    REG_DMAP4 = 0x03;
    REG_BBAD4 = 0x1D; // M7C + M7D
    REG_A1T4LH = dmaT.mem.c.addr;
    REG_A1B4 = dmaT.mem.c.bank;

    dmaT.mem.p = camTabs + 3600 + bufOff;
    REG_DMAP5 = 0x03;
    REG_BBAD5 = 0x1F; // M7X + M7Y
    REG_A1T5LH = dmaT.mem.c.addr;
    REG_A1B5 = dmaT.mem.c.bank;

    dmaT.mem.p = camTabs + 5400 + bufOff;
    REG_DMAP6 = 0x03;
    REG_BBAD6 = 0x0D; // M7HOFS + M7VOFS
    REG_A1T6LH = dmaT.mem.c.addr;
    REG_A1B6 = dmaT.mem.c.bank;

    // ch7: window waterline (mode 1: WH0 then WH1)
    dmaT.mem.p = winTab;
    REG_DMAP7 = 0x01;
    REG_BBAD7 = 0x26;
    REG_A1T7LH = dmaT.mem.c.addr;
    REG_A1B7 = dmaT.mem.c.bank;

    REG_HDMAEN = 0xFF;
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

    // jet ski sprite: 64 tiles at VRAM 0x6000, OBJ palette 0 (CGRAM 128+)
    oamInitGfxSet(&ski_tiles, 2048, &ski_pal, 32, 0, 0x6000, OBJ_SIZE16_L32);
    oamSet(0, SKI_X, 140, 3, 0, 0, 0, 0);
    oamSetEx(0, OBJ_LARGE, OBJ_SHOW);

    setPaletteColor(0, RGB8(248, 168, 96));

    // Additive colour math on BG1 with the fixed colour = crest glow
    REG_CGWSEL = 0x00;
    REG_CGADSUB = 0x01;

    // Window 1 masks OBJ on the main screen; HDMA moves the window edges so
    // the region below the waterline swallows the sprite
    REG_WOBJSEL = 0x02;
    REG_TMW = 0x10;

    setScreenOn();

    tick = 0;
    phaseAcc = 0;
    camTheta = 0;
    camPX = 512;
    camPY = 0;
    camSinVal = 0;
    camCosVal = 127;
    skiY = (((s16)waveSurfH[0]) << 8) - DIP; // start settled on the water
    skiVv = 0;
    skiVX = 0;
    skiVY = 0;
    fracX = 0;
    fracY = 0;
    wasInWater = 1;
    prevSin = 0;
    prevCos = 127;
    rotTimer = 0;
    rotOfs = 0;
    buildWinTab(200);

// build-time debug: drive itself (the emulator test runner has no input)
#define AUTOPILOT 0

    while (1)
    {
        pad0 = padsCurrent(0);
#if AUTOPILOT
        pad0 |= KEY_B;
#endif

        // ---- steering: heading always turns; lean is cosmetic feedback ----
        skiLean = 0;
        if (pad0 & KEY_LEFT)
        {
            camTheta -= TURN_SPEED;
            skiLean = 1;
            skiFlip = 1;
        }
        if (pad0 & KEY_RIGHT)
        {
            camTheta += TURN_SPEED;
            skiLean = 1;
            skiFlip = 0;
        }

        // ---- buoyancy / flight ----
        surf88 = ((s16)waveSurfH[phase]) << 8;
        inWater = (skiY <= surf88);
        if (inWater)
        {
            // splash: hitting the water kills most vertical speed — this is
            // what stops the buoyancy spring from pogo-ing off every wave
            if (!wasInWater)
                skiVv >>= 2;
            // gentle spring toward floating a dip under, heavily water-damped
            skiVv += (surf88 - DIP - skiY) >> 4;
            skiVv -= skiVv >> 1;
            // the water is thick: hard depth floor
            if (skiY < surf88 - MAX_DEPTH)
            {
                skiY = surf88 - MAX_DEPTH;
                if (skiVv < 0)
                    skiVv = 0;
            }
            // hovercraft scrabble: thrust only bites in the water
            if (pad0 & KEY_B)
            {
                skiVX += (THRUST * camSinVal) >> 7;
                skiVY += (THRUST * camCosVal) >> 7;
            }
            // water drag
            skiVX -= skiVX >> 4;
            skiVY -= skiVY >> 4;
        }
        else
        {
            skiVv -= GRAV; // airborne: ballistic, thrust spins the fan in vain
        }
        if (skiVv > MAX_VV_UP)
            skiVv = MAX_VV_UP;
        if (skiVv < -MAX_VV_DOWN)
            skiVv = -MAX_VV_DOWN;
        skiY += skiVv;
        wasInWater = inWater;

        // ---- move the world ----
        fracX += skiVX;
        camPX += fracX >> 8;
        fracX &= 0x00FF;
        fracY += skiVY;
        camPY += fracY >> 8;
        fracY &= 0x00FF;

        // split velocity into forward/side components along the heading
        vAlong = ((skiVX >> 4) * camSinVal + (skiVY >> 4) * camCosVal) >> 3;
        vSide = ((skiVX >> 4) * camCosVal - (skiVY >> 4) * camSinVal) >> 3;
        if (inWater)
        {
            // gravel grip: kill a chunk of the slip each loop, and let the
            // rudder convert some of it into forward drive (momentum keeps)
            vAlong += (vSide < 0 ? -vSide : vSide) >> 3;
            vSide -= vSide >> 2;
            skiVX = ((vAlong >> 4) * camSinVal + (vSide >> 4) * camCosVal) >> 3;
            skiVY = ((vAlong >> 4) * camCosVal - (vSide >> 4) * camSinVal) >> 3;
        }
        phaseAcc = (phaseAcc + WAVE_BASE_ROLL
                    + (((vAlong >> 4) * WAVE_STEPS_PER_TEXEL) >> 4))
                   & WAVE_PHASE_MASK;
        phase = phaseAcc >> 8;

        camPhaseOff = phase * WAVE_RAW_STRIDE;
        camBufOff = (tick & 1) ? 900 : 0;
        tick++;

        // skip building the sky lines — their table entries are never shown
        skip = waveSky[phase];
        if (skip > 126)
            skip = 126;
        camBlk1Ct = 127 - skip;
        camSrcOff = camPhaseOff + 2 * skip;
        camDstOff = camBufOff + 1 + 4 * skip;

        vbl0 = snes_vblank_count;
        profStartLine = scanline();
        buildCamTables();
        profFrames = snes_vblank_count - vbl0;
        profLines = profFrames * 262 + scanline() - profStartLine;

        // pivot around the SKI, not the camera: when the heading changed,
        // orbit the camera so the ski's world position stays fixed
        if (camSinVal != prevSin || camCosVal != prevCos)
        {
            camPX += (WAVE_SKI_DIST * (prevSin - camSinVal)) >> 7;
            camPY += (WAVE_SKI_DIST * (prevCos - camCosVal)) >> 7;
        }
        prevSin = camSinVal;
        prevCos = camCosVal;

        // ---- place the ski sprite + waterline window ----
        waterRow = waveSkiRow[phase];
        diff88 = skiY - surf88; // positive = above the surface
        sprTop = (s16)waterRow - WAVE_SKI_REST_ROW
                 - (((diff88 >> 4) * WAVE_SKI_PPT_Q4) >> 8);
        if (sprTop < 24)
            sprTop = 24;
        if (sprTop > 190)
            sprTop = 190;
        buildWinTab(waterRow);
        // sprite updates BEFORE WaitForVBlank: the ISR's OAM DMA (ch7 regs)
        // fires on that vblank, and waveHdma restores ch7 right after
        oamSet(0, SKI_X, (u16)sprTop, 3, skiFlip, 0, skiLean ? 4 : 0, 0);

        if ((tick & 3) == 0)
        {
            uiPrint(0, 0, "X");
            uiPrintNum(1, 0, camPX & 1023, 4);
            uiPrint(6, 0, "Y");
            uiPrintNum(7, 0, camPY & 1023, 4);
            uiPrint(12, 0, "H");
            uiPrintNum(13, 0, camTheta, 3);
            uiPrint(17, 0, "V");
            uiPrintNum(18, 0, vAlong >= 0 ? vAlong : -vAlong, 4);
            uiPrint(0, 1, "BUILD");
            uiPrintNum(5, 1, profLines, 4);
            uiPrint(9, 1, "LN PH");
            uiPrintNum(14, 1, phase, 2);
            uiPrint(17, 1, inWater ? "WET" : "AIR");
            // physics state, in 16ths of a texel: K = ski height,
            // S = water surface, V = vertical speed (8.8 raw)
            uiPrint(0, 2, "K");
            uiPrintS16(1, 2, skiY >> 4, 4);
            uiPrint(7, 2, "S");
            uiPrintS16(8, 2, surf88 >> 4, 4);
            uiPrint(14, 2, "V");
            uiPrintS16(15, 2, skiVv, 4);
            uiPrint(21, 2, "W");
            uiPrintNum(22, 2, waterRow, 3);
        }

        WaitForVBlank();
        uiFlush();
        waveHdma(phase, camBufOff);

#if WAVE_ROT_FRAMES > 0
        rotTimer++;
        if (rotTimer >= WAVE_ROT_FRAMES / 2)
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
