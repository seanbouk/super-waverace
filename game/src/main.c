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

extern char sea_patterns, sea_map, sea_palette;
extern char ski_tiles, ski_pal, npc_pals;
extern void buildCamTables(void);
extern void collProbe(void); // camera.asm: reads the collision byte-map
extern void rowDepth(void);  // camera.asm: screen row for a view depth
extern void npcTrig(void);   // camera.asm: npcA (u8 heading) -> npcSin/npcCos

// ---- camera state shared with camera.asm (accessed via long addressing) ----
u8 camTheta;
u16 camTheta16; // 8.8 heading: turn rate scales smoothly with speed
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
#define REG_WRDIVL (*(vuint8 *)0x4204)
#define REG_WRDIVH (*(vuint8 *)0x4205)
#define REG_WRDIVB (*(vuint8 *)0x4206)
#define REG_RDDIV (*(vuint16 *)0x4214)
#ifndef REG_SETINI
#define REG_SETINI (*(vuint8 *)0x2133)
#endif
#define REG_TMW (*(vuint8 *)0x212E)

dmaMemory dmaTM, dmaG, dmaT;
u16 pad0;
u16 tick;
u16 phase;
u16 phaseAcc;
s16 vAlong, vSide;
s16 turnRate;
s16 surf88, diff88;
s16 sprTop;
u8 rotTimer, rotOfs;
u8 skip, waterRow, inWater, wasInWater;
s16 prevSin, prevCos;
u16 profStartLine, profLines, profFrames;
u16 vbl0, loopVbl, loopFrames;
// collision
u16 collOfs;
u8 collVal, collHere;
u16 skiWX, skiWY;
s16 stepX, stepY;
// buoys
u16 rdV, rdRow, rdD;
s16 bdx, bdy, bv, bu, bau, bh, bl;
u16 bq, winRow, dly;
u8 bi;
// race progress (phase 1: player only) + waypoint-chaser steering
u8 nextWp, lapCount;
u16 lapTicks, lastLap;
s16 wpdx, wpdy, apc, apd, apu;
// NPC racers (phase 2): kinematic waypoint followers,
// OAM sprites 5..7 (after the ski and the 4 course buoys)
#define NPC_COUNT 3
#define NPC_TURN 2 // binary degrees/loop = the player's full turn rate
u16 npcX[NPC_COUNT], npcY[NPC_COUNT]; // world units, wrap & 4095
s16 npcFX[NPC_COUNT], npcFY[NPC_COUNT]; // sub-unit accumulators (8.8)
u16 npcSpd[NPC_COUNT];                  // current speed, 8.8 world/loop
u8 npcTheta[NPC_COUNT], npcWp[NPC_COUNT], npcLap[NPC_COUNT];
u8 npcA; // npcTrig interface
s16 npcSin, npcCos;
// race flow (phase 4): countdown -> racing -> finished
#define RACE_LAPS 3
// schedule rubber-banding: four speed tiers picked from (should this racer
// still be ahead of the player?) x (how far ahead is it really?).
// Calibrated from position traces: the REAL player pace is ~120 world/s
// (thrust only bites in water, corners coast) which matches npcSpd ~1700,
// NOT the nominal top speed. HOLD is deliberately slower than player pace
// so a racer passed on schedule drifts back and never re-passes.
#define SPD_FAST 2000   // behind schedule: catch up (~140 world/s)
#define SPD_CRUISE 1700 // holding its scheduled place (~player pace)
#define SPD_SLOW 1200   // fade: let the player by decisively (~85)
#define SPD_HOLD 1500   // keep contact without racing (~105)
u8 raceState;           // 0 countdown, 1 racing, 2 finished
u8 racePos, finPos, posAcc, goTimer;
u8 npcFade[NPC_COUNT]; // player lap at which this racer starts fading
u16 npcDist[NPC_COUNT], pDist, spdTgt;
s16 rubDiff;
// race + lap clocks: REAL frames from snes_vblank_count (the loop rate
// varies, so loop ticks are not time); digits maintained incrementally
u8 rMin, rSecT, rSecU, rTick; // rTick = frame accum (also counts the countdown)
u16 lapFr;                    // frames this lap
u8 lastLapSec, lastLapTenth;
// set to 1 to restore the dev readouts (position/physics/profiler)
#define DEBUG_UI 0
// projectPoint i/o
u16 pjX, pjY, pjV, pjCol;
u8 pjOk;

//---------------------------------------------------------------------------------
static u16 scanline(void)
{
    u8 lo, hi;
    // assign the latch reads: tcc drops (void)-cast volatile reads, which
    // left OPVCT unlatched and this function returning garbage
    lo = REG_STAT78; // reset the latch-read flip-flop
    lo = REG_SLHV;   // latch H/V counters
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
// project a world point onto the screen the way buoys render: view-space
// transform, surface-row lookup (rides the occluding crest), hardware-divider
// column. in: pjX, pjY (world units). out: pjOk, and when visible pjV (view
// depth: scale ladder), pjCol (screen centre x), rdRow (surface row); pushes
// winRow down so the waterline window clips whatever gets drawn there.
static void projectPoint(void)
{
    pjOk = 0;
    bdx = (s16)((pjX - camPX) & 4095);
    if (bdx > 2048)
        bdx -= 4096;
    bdy = (s16)((pjY - camPY) & 4095);
    if (bdy > 2048)
        bdy -= 4096;
    if (bdx > 700 || bdx < -700 || bdy > 700 || bdy < -700)
        return;
    // full-precision view transform: split each delta so the 16-bit products
    // stay exact (a plain >>4 pre-shift quantised motion to 16-unit jumps)
    bh = bdx >> 4;
    bl = bdx & 15;
    bv = ((bh * camSinVal) >> 3) + ((bl * camSinVal) >> 7);
    bu = ((bh * camCosVal) >> 3) + ((bl * camCosVal) >> 7);
    bh = bdy >> 4;
    bl = bdy & 15;
    bv += ((bh * camCosVal) >> 3) + ((bl * camCosVal) >> 7);
    bu -= ((bh * camSinVal) >> 3) + ((bl * camSinVal) >> 7);
    if (bv < 176 || bv > 620)
        return;
    bau = bu < 0 ? -bu : bu;
    if (bau > 480)
        return;
    rdV = (u16)bv;
    rowDepth(); // surface row for this depth, and what's shown there
    if (rdRow == 0xFFFF)
        return;
    // never hide behind waves: rdRow is the occluding crest's row when the
    // point is tucked behind one, so it rides up onto the wave in front -
    // correct for waves half its height
    // screen column: px = u * 221 / v via the hardware divider
    bq = (u16)bau << 6;
    REG_WRDIVL = bq & 0xFF;
    REG_WRDIVH = bq >> 8;
    REG_WRDIVB = (u8)(((u16)bv * 74) >> 8);
    dly = tick + phase; // cover the 16-cycle divide latency
    dly += winRow;
    bq = REG_RDDIV;
    if (bq > 140)
        return;
    bq = bu < 0 ? 128 - bq : 128 + bq;
    if (bq < 12 || bq > 232)
        return;
    if (rdRow > winRow)
        winRow = (u16)rdRow;
    pjV = (u16)bv;
    pjCol = bq;
    pjOk = 1;
}

//---------------------------------------------------------------------------------
// draw OAM sprite `oid` (byte-offset id!) at the projected point: five scales,
// all bottom-anchored to the surface row so a scale change never reads as
// movement. `right` picks the red R art over the yellow L art.
static void drawLadder(u16 oid, u8 right)
{
    if (pjV < 192)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, right ? 12 : 8, 0);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < 268)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, right ? 68 : 64, 0);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < 382)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, right ? 74 : 72, 0);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
    else if (pjV < 534)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, right ? 78 : 76, 0);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
    else
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, right ? 106 : 104, 0);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
}

//---------------------------------------------------------------------------------
// NPC racer at the projected point: rear-view ski, same five distance bands
// as the buoys, recoloured per racer via OBJ palette (tiles shared). The art
// is cropped at its waterline, so the bottom-anchored slot sits ON the water.
static void drawSki(u16 oid, u8 pal)
{
    if (pjV < 192)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, 128, pal);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < 268)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, 132, pal);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < 382)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, 136, pal);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
    else if (pjV < 534)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, 138, pal);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
    else
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, 140, pal);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
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
                      WAVE_PC7_SIZE, 0x0000);

    setMode7(0);
    // EXTBG spike: BG2 duplicates the mode 7 image with pixel bit 7 as a
    // priority flag; course pixels (bit 7 set) render via BG2-high, above
    // BG1 and OUTSIDE its colour math - crest glow no longer touches sand
    REG_SETINI = 0x40;
    uiInit();

    // ski + buoy + NPC sheet: 192 tiles at VRAM 0x6000 (through 0x6BFF -
    // the UI map moved to 0x7000 to make room), OBJ palette 0 (CGRAM 128+)
    oamInitGfxSet(&ski_tiles, 6144, &ski_pal, 32, 0, 0x6000, OBJ_SIZE16_L32);
    // NPC racer recolours: OBJ palettes 1-3 (CGRAM 144-191), shared tiles
    dmaCopyCGram((u8 *)&npc_pals, 144, 96);
    oamSet(0, SKI_X, 140, 3, 0, 0, 0, 0);
    oamSetEx(0, OBJ_LARGE, OBJ_SHOW);
    for (bi = 1; bi < 12; bi++)
        oamSetVisible(bi << 2, OBJ_HIDE); // NB: OAM ids are byte offsets (x4)

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
    camTheta16 = 0;
    camPX = 2048; // world units: 1 texel = 4 world; map spans 4096
    camPY = 768;  // south of the demo island, ring ahead
    camSinVal = 0;
    camCosVal = 127;
    skiY = -1536; // spawn below any wave: wet from frame one, bobs up
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
    nextWp = 0; // BSS is not zero-initialised: clear all race state
    lapCount = 0;
    lapTicks = 0;
    lastLap = 0;
    skiWX = camPX; // autopilot reads these before the first physics pass
    skiWY = camPY + WAVE_SKI_DIST;
    // NPC grid: just ahead of the ski (world y: ski starts at 968), inside
    // the gate span, staggered in depth so no scanline drowns in sprites.
    // They open on waypoint 2 (gate 2) - waypoints 0/1 are behind them.
    for (bi = 0; bi < NPC_COUNT; bi++)
    {
        npcTheta[bi] = 0;
        npcFX[bi] = 0;
        npcFY[bi] = 0;
        npcWp[bi] = 2;
        npcLap[bi] = 0;
        npcDist[bi] = 0;
        npcY[bi] = 1020 + (bi << 6);
    }
    npcX[0] = 1950;
    npcX[1] = 2148;
    npcX[2] = 2050;
    // the race is scripted to unwind: green fades two laps in, purple one
    // lap in, orange from the gun - pass one racer per lap
    npcFade[0] = 2;
    npcFade[1] = 1;
    npcFade[2] = 0;
    npcSpd[0] = SPD_CRUISE;
    npcSpd[1] = SPD_CRUISE;
    npcSpd[2] = SPD_SLOW;
    raceState = 0;
    racePos = 4;
    finPos = 0;
    posAcc = 0;
    goTimer = 0;
    pDist = 0;
    rMin = 0;
    rSecT = 0;
    rSecU = 0;
    rTick = 0;
    lapFr = 0;
    lastLapSec = 0;
    lastLapTenth = 0;
    loopVbl = snes_vblank_count; // BSS garbage would poison the first
    loopFrames = 0;              // countdown accumulation
    buildWinTab(200);

// build-time debug: drive itself (the emulator test runner has no input)
#define AUTOPILOT 0

    while (1)
    {
        // the loop takes a variable 3-4 vblanks (see CLAUDE.md), so ALL
        // race timing accumulates real frames, never loop ticks
        loopFrames = snes_vblank_count - loopVbl;
        loopVbl = snes_vblank_count;
        pad0 = padsCurrent(0);
#if AUTOPILOT
#if WAVE_PATH_COUNT > 0
        // waypoint chaser — also the seed of the NPC racer brain.
        // apc = r*sin(heading - bearing): negative means the target is to
        // the right (theta must grow). Deadband ~7 deg (apd>>3 = tan-ish).
        wpdx = (s16)((pathX[nextWp] - skiWX) & 4095);
        if (wpdx > 2048)
            wpdx -= 4096;
        wpdy = (s16)((pathY[nextWp] - skiWY) & 4095);
        if (wpdy > 2048)
            wpdy -= 4096;
        apc = (wpdy >> 4) * camSinVal - (wpdx >> 4) * camCosVal;
        apd = (wpdx >> 4) * camSinVal + (wpdy >> 4) * camCosVal;
        if (apd < 0)
        {
            // target behind: commit to one side until it comes round
            if (apc <= 0)
                pad0 |= KEY_RIGHT;
            else
                pad0 |= KEY_LEFT;
        }
        else
        {
            if (apc < -(apd >> 3))
                pad0 |= KEY_RIGHT;
            if (apc > (apd >> 3))
                pad0 |= KEY_LEFT;
        }
        // full throttle on the straights; coast into corners sharper than
        // ~45 deg — but never stall (turn authority needs speed)
        apu = apc < 0 ? -apc : apc;
        if ((apd > 0 && apu < apd) || (vAlong < 300 && vAlong > -300))
            pad0 |= KEY_B;
#else
        pad0 |= KEY_B;
#endif
#endif

        // ---- race flow: countdown holds the engines, GO releases them,
        // the finish cuts them again (masks the autopilot too) ----
        if (raceState == 0)
        {
            pad0 &= ~(KEY_B | KEY_Y);
            rTick += (u8)loopFrames; // counting down in real frames
            if (rTick >= 240)        // 4 seconds: 3.. 2.. 1.. GO
            {
                raceState = 1;
                goTimer = 30;
                lapTicks = 0;
                rTick = 0;
                lapFr = 0;
            }
        }
        else
        {
            if (goTimer)
                goTimer--;
            if (raceState == 2)
                pad0 &= ~(KEY_B | KEY_Y);
            else
            {
                rTick += (u8)loopFrames;
                if (rTick >= 60)
                {
                    rTick -= 60;
                    rSecU++;
                    if (rSecU == 10)
                    {
                        rSecU = 0;
                        rSecT++;
                        if (rSecT == 6)
                        {
                            rSecT = 0;
                            rMin++;
                        }
                    }
                }
                lapFr += loopFrames;
            }
        }

        // ---- steering: turn authority scales with speed (no speed, no
        // rudder bite); full rate from ~20% of top speed upward ----
        turnRate = vAlong < 0 ? -vAlong : vAlong;
        if (turnRate > 512)
            turnRate = 512; // 512 in 8.8 heading units = the old full rate
        skiLean = 0;
        if (pad0 & KEY_LEFT)
        {
            camTheta16 -= turnRate;
            if (turnRate >= 32)
                skiLean = 1;
            skiFlip = 1;
        }
        if (pad0 & KEY_RIGHT)
        {
            camTheta16 += turnRate;
            if (turnRate >= 32)
                skiLean = 1;
            skiFlip = 0;
        }
        camTheta = camTheta16 >> 8;

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
            if ((pad0 & KEY_B) && tick > 20) // grace while the spawn settles
            {
                skiVX += (THRUST * camSinVal) >> 7;
                skiVY += (THRUST * camCosVal) >> 7;
            }
            if (pad0 & KEY_Y) // reverse: half thrust, backwards
            {
                skiVX -= ((THRUST / 2) * camSinVal) >> 7;
                skiVY -= ((THRUST / 2) * camCosVal) >> 7;
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

        // ---- move the world; sand and ropes block, sliding along ----
        // collide at the SKI's position (200 world units ahead of camera),
        // one axis at a time: the blocked axis stops, the other keeps its
        // momentum, so oblique hits scrape along instead of snagging
        skiWX = camPX + ((WAVE_SKI_DIST * camSinVal) >> 7);
        skiWY = camPY + ((WAVE_SKI_DIST * camCosVal) >> 7);
        collOfs = ((skiWY >> 5) & 127) * 128 + ((skiWX >> 5) & 127);
        collProbe();
        collHere = collVal;
        if (collHere)
        {
            // embedded (rounding creep while grinding a wall while turning):
            // push toward the nearest water neighbour and damp the drive -
            // recovers in a few loops instead of roaming the land
            skiVX -= skiVX >> 1;
            skiVY -= skiVY >> 1;
            collOfs = (((u16)(skiWY - 32) >> 5) & 127) * 128 + ((skiWX >> 5) & 127);
            collProbe();
            if (!collVal)
                camPY -= 4;
            else
            {
                collOfs = (((u16)(skiWY + 32) >> 5) & 127) * 128 + ((skiWX >> 5) & 127);
                collProbe();
                if (!collVal)
                    camPY += 4;
                else
                {
                    collOfs = ((skiWY >> 5) & 127) * 128 + (((u16)(skiWX - 32) >> 5) & 127);
                    collProbe();
                    if (!collVal)
                        camPX -= 4;
                    else
                        camPX += 4; // east, or keep pushing until a gap opens
                }
            }
        }

        fracX += skiVX;
        stepX = fracX >> 8;
        fracX &= 0x00FF;
        if (stepX)
        {
            collOfs = ((skiWY >> 5) & 127) * 128
                      + (((u16)(skiWX + stepX) >> 5) & 127);
            collProbe();
            if (collVal)
            {
                stepX = 0;
                skiVX = 0;
            }
        }
        camPX += stepX;

        fracY += skiVY;
        stepY = fracY >> 8;
        fracY &= 0x00FF;
        if (stepY)
        {
            collOfs = (((u16)(skiWY + stepY) >> 5) & 127) * 128
                      + (((u16)(skiWX + stepX) >> 5) & 127);
            collProbe();
            if (collVal)
            {
                stepY = 0;
                skiVY = 0;
            }
        }
        camPY += stepY;

#if WAVE_PATH_COUNT > 0
        // ---- race progress: next waypoint reached within ~1.5 cells
        // (Manhattan, world units; skiWX is one step stale — harmless) ----
        wpdx = (s16)((pathX[nextWp] - skiWX) & 4095);
        if (wpdx > 2048)
            wpdx -= 4096;
        if (wpdx < 0)
            wpdx = -wpdx;
        wpdy = (s16)((pathY[nextWp] - skiWY) & 4095);
        if (wpdy > 2048)
            wpdy -= 4096;
        if (wpdy < 0)
            wpdy = -wpdy;
        pDist = (u16)(wpdx + wpdy);
        if (pDist < 200)
        {
            nextWp++;
            if (nextWp >= WAVE_PATH_COUNT)
            {
                nextWp = 0;
                lapCount++;
                lastLap = lapTicks;
                lapTicks = 0;
                lastLapSec = (u8)(lapFr / 60); // division: once per lap only
                lastLapTenth = (u8)((lapFr % 60) / 6);
                lapFr = 0;
                if (raceState == 1 && lapCount >= RACE_LAPS)
                {
                    raceState = 2; // chequered flag
                    finPos = racePos;
                }
            }
        }
        lapTicks++;

        // ---- NPC racers: kinematic waypoint followers (the autopilot's
        // steering brain), collision-probed so they cannot cross land ----
        posAcc = 1;
        if (raceState) // frozen on the grid until GO
            for (bi = 0; bi < NPC_COUNT; bi++)
            {
                wpdx = (s16)((pathX[npcWp[bi]] - npcX[bi]) & 4095);
                if (wpdx > 2048)
                    wpdx -= 4096;
                wpdy = (s16)((pathY[npcWp[bi]] - npcY[bi]) & 4095);
                if (wpdy > 2048)
                    wpdy -= 4096;
                npcA = npcTheta[bi];
                npcTrig();
                apc = (wpdy >> 4) * npcSin - (wpdx >> 4) * npcCos;
                apd = (wpdx >> 4) * npcSin + (wpdy >> 4) * npcCos;
                if (apd < 0)
                {
                    // target behind: commit to one side
                    if (apc <= 0)
                        npcTheta[bi] += NPC_TURN;
                    else
                        npcTheta[bi] -= NPC_TURN;
                }
                else if (apc < -(apd >> 3))
                    npcTheta[bi] += NPC_TURN;
                else if (apc > (apd >> 3))
                    npcTheta[bi] -= NPC_TURN;
                // corner slowdown: half cruise past ~45 deg off the line
                apu = apc < 0 ? -apc : apc;
                bq = npcSpd[bi];
                if (apd < 0 || apu > apd)
                    bq >>= 1;
                // velocity along the (pre-turn) heading; >>4 before the
                // product keeps it 16-bit, then 8.8 accumulators as fracX
                apu = (s16)(bq >> 4);
                apc = (apu * npcSin) >> 3;
                apd = (apu * npcCos) >> 3;
                npcFX[bi] += apc;
                stepX = npcFX[bi] >> 8;
                npcFX[bi] &= 0x00FF;
                if (stepX)
                {
                    collOfs = ((npcY[bi] >> 5) & 127) * 128
                              + (((u16)(npcX[bi] + stepX) >> 5) & 127);
                    collProbe();
                    if (!collVal)
                        npcX[bi] = (npcX[bi] + stepX) & 4095;
                }
                npcFY[bi] += apd;
                stepY = npcFY[bi] >> 8;
                npcFY[bi] &= 0x00FF;
                if (stepY)
                {
                    collOfs = (((u16)(npcY[bi] + stepY) >> 5) & 127) * 128
                              + ((npcX[bi] >> 5) & 127);
                    collProbe();
                    if (!collVal)
                        npcY[bi] = (npcY[bi] + stepY) & 4095;
                }
                if (wpdx < 0)
                    wpdx = -wpdx;
                if (wpdy < 0)
                    wpdy = -wpdy;
                npcDist[bi] = (u16)(wpdx + wpdy);
                if (npcDist[bi] < 200)
                {
                    npcWp[bi]++;
                    if (npcWp[bi] >= WAVE_PATH_COUNT)
                    {
                        npcWp[bi] = 0;
                        npcLap[bi]++;
                    }
                }
                // schedule rubber-banding: is it ahead of the player?
                rubDiff = (s16)(npcLap[bi] * WAVE_PATH_COUNT + npcWp[bi])
                          - (s16)(lapCount * WAVE_PATH_COUNT + nextWp);
                if (rubDiff > 0)
                    apu = 1;
                else if (rubDiff < 0)
                    apu = 0;
                else // same waypoint: nearer to it = ahead
                    apu = npcDist[bi] < pDist ? 1 : 0;
                if (apu)
                    posAcc++;
                // ...and should it still be, per its fade schedule? Gap
                // caps keep the race close for players off autopilot pace:
                // a scheduled leader never runs away, a faded racer never
                // falls out of sight
                if (lapCount < npcFade[bi])
                {
                    if (!apu)
                        spdTgt = SPD_FAST; // behind schedule: catch up
                    else if (rubDiff > 3)
                        spdTgt = SPD_HOLD; // never run away from the player
                    else
                        spdTgt = SPD_CRUISE;
                }
                else
                {
                    // fading: keep SLOW until CLEARLY passed (a tie-boundary
                    // equilibrium otherwise pins it to the player's tail)
                    if (rubDiff >= -3)
                        spdTgt = SPD_SLOW;
                    else if (rubDiff < -10)
                        spdTgt = SPD_CRUISE; // far back: stay in sight
                    else
                        spdTgt = SPD_HOLD;
                }
                npcSpd[bi] += (s16)(spdTgt - npcSpd[bi]) >> 3;
            }
        if (raceState)
            racePos = posAcc;
#endif

        // split velocity into forward/side components along the heading
        vAlong = ((skiVX >> 4) * camSinVal + (skiVY >> 4) * camCosVal) >> 3;
        vSide = ((skiVX >> 4) * camCosVal - (skiVY >> 4) * camSinVal) >> 3;
        if (inWater)
        {
            // gravel grip: kill a chunk of the slip each loop, and let the
            // rudder convert some of it into forward drive (momentum keeps)
            vAlong += (vSide < 0 ? -vSide : vSide) >> 3;
            vSide -= vSide >> 3;
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
        // sprite updates BEFORE WaitForVBlank: the ISR's OAM DMA (ch7 regs)
        // fires on that vblank, and waveHdma restores ch7 right after
        oamSet(0, SKI_X, (u16)sprTop, 3, skiFlip, 0, skiLean ? 4 : 0, 0);

        // ---- buoys: project into view space, pick scale, ride the water ----
        winRow = waterRow;
#if WAVE_BUOY_COUNT > 0
        for (bi = 0; bi < WAVE_BUOY_COUNT; bi++)
        {
            pjX = buoyX[bi];
            pjY = buoyY[bi];
            projectPoint();
            if (pjOk)
                drawLadder((1 + bi) << 2, buoyType[bi]);
            else
                oamSetVisible((1 + bi) << 2, OBJ_HIDE);
        }
#endif
#if WAVE_PATH_COUNT > 0
        // NPC racers ride the exact same pipeline: rear-view ski art,
        // one recolour palette per racer
        for (bi = 0; bi < NPC_COUNT; bi++)
        {
            pjX = npcX[bi];
            pjY = npcY[bi];
            projectPoint();
            if (pjOk)
                drawSki((5 + bi) << 2, (u8)(1 + bi));
            else
                oamSetVisible((5 + bi) << 2, OBJ_HIDE);
        }
#endif
        buildWinTab((u8)winRow);

        if ((tick & 3) == 0)
        {
#if DEBUG_UI
            uiPrint(0, 0, "X");
            uiPrintNum(1, 0, camPX & 4095, 4);
            uiPrint(6, 0, "Y");
            uiPrintNum(7, 0, camPY & 4095, 4);
            uiPrint(12, 0, "H");
            uiPrintNum(13, 0, camTheta, 3);
            uiPrint(17, 0, "V");
            uiPrintNum(18, 0, vAlong >= 0 ? vAlong : -vAlong, 4);
#if WAVE_PATH_COUNT > 0
            // race progress: lap.waypoint, and the last lap's tick count
            uiPrint(23, 0, "L");
            uiPrintNum(24, 0, lapCount, 1);
            uiPrint(25, 0, ".");
            uiPrintNum(26, 0, nextWp, 2);
            uiPrint(21, 1, "T");
            uiPrintNum(22, 1, lastLap, 4);
#endif
            uiPrint(0, 1, "BUILD");
            uiPrintNum(5, 1, profLines, 4);
            uiPrint(9, 1, "LN PH");
            uiPrintNum(14, 1, phase, 2);
            uiPrint(17, 1, inWater ? "WET" : "AIR");
            uiPrint(27, 1, "F");
            uiPrintNum(28, 1, loopFrames, 1);
            // scanline costs: A = loop top to build start (logic),
            // B = build end to WaitForVBlank (sprites/buoys/UI)
            uiPrint(0, 0, "A");
            uiPrintNum(1, 0, secA, 4);
            uiPrint(6, 0, "B");
            uiPrintNum(7, 0, secB, 4);
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
#if WAVE_PATH_COUNT > 0
            // NPC 0 progress (debug): lap.waypoint
            uiPrint(26, 2, "N");
            uiPrintNum(27, 2, npcLap[0], 1);
            uiPrint(28, 2, ".");
            uiPrintNum(29, 2, npcWp[0], 2);
#endif
#else
            // ---- race HUD ----
            uiPrint(0, 0, "LAP ");
            uiPrintNum(4, 0, lapCount < RACE_LAPS ? lapCount + 1 : RACE_LAPS, 1);
            uiPrint(5, 0, "/3");
            uiPrint(11, 0, "POS ");
            bq = raceState == 2 ? finPos : racePos;
            uiPrintNum(15, 0, bq, 1);
            if (bq == 1)
                uiPrint(16, 0, "ST");
            else if (bq == 2)
                uiPrint(16, 0, "ND");
            else if (bq == 3)
                uiPrint(16, 0, "RD");
            else
                uiPrint(16, 0, "TH");
            uiPrint(23, 0, "T");
            uiPrintNum(24, 0, rMin, 1);
            uiPrint(25, 0, ":");
            uiPrintNum(26, 0, rSecT, 1);
            uiPrintNum(27, 0, rSecU, 1);
            // centre banner: countdown / GO! / FINISH!
            if (raceState == 0)
            {
                uiPrint(13, 1, "       ");
                if (rTick >= 180)
                    uiPrint(15, 1, "1");
                else if (rTick >= 120)
                    uiPrint(15, 1, "2");
                else if (rTick >= 60)
                    uiPrint(15, 1, "3");
            }
            else if (goTimer)
                uiPrint(14, 1, "GO!");
            else if (raceState == 2)
                uiPrint(13, 1, "FINISH!");
            else
                uiPrint(13, 1, "       ");
            if (lapCount)
            {
                uiPrint(0, 2, "LAST LAP ");
                uiPrintNum(9, 2, lastLapSec, 2);
                uiPrint(11, 2, ".");
                uiPrintNum(12, 2, lastLapTenth, 1);
            }
#endif
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
