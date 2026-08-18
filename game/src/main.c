/*---------------------------------------------------------------------------------
    Super Waverace — jet ski on the rolling sea

    HDMA channels per frame:
      ch0 BG mode ($2105)  : mode 1 UI band on top, mode 7 below   (static ROM)
      ch1 TM      ($212C)  : UI band / sky / sea split             (baked ROM)
      ch2 COLDATA ($2132)  : crest glow                            (baked ROM)
      ch3 M7A+B, ch4 M7C+D, ch5 M7X+Y, ch6 HOFS+VOFS : paired-register
          mode-3 streams built each frame by camera.asm (B/D/VOFS ride along
          as pre-zeroed words)
      ch7 WH0+WH1 ($2126)  : window waterline — masks OBJ across the hull's
          submerged rows only (waterline..sprite bottom), so the hull sinks
          and bobs while the wake spray below the stern stays drawable
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
u8 winTab[16]; // room for the bounded mask's extra runs

// ---- jet ski state (8.8 fixed unless noted) ----
s16 skiY;         // height above mean sea level
s16 skiVv;        // vertical velocity
s16 skiVX, skiVY; // world-space velocity
s16 fracX, fracY; // sub-texel position accumulators
u8 skiLean;       // 0 straight, 1 leaning
u8 skiFlip;       // lean direction (hflip)

#define TURN_SPEED 2
#define THRUST 144 // applied at >>6: top speed = THRUST*32 (8.8 world/loop)
#define GRAV 36       // 8.8 texels/loop^2 — floaty hangtime at race pace
#define DIP 128       // rest waterline: 0.5 texel below surface
#define MAX_VV_UP 480   // launch clamp: proper air off the big rollers
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
u16 bq, dly;
u8 bi;
// race progress (phase 1: player only) + waypoint-chaser steering
u8 nextWp, lapCount;
u16 lapTicks, lastLap;
s16 wpdx, wpdy, apc, apd, apu;
// NPC racers (phase 2): kinematic waypoint followers, on the OAM sprites
// after the ski and the course buoys
#define NPC_COUNT 3
#define NPC_SPR (1 + WAVE_BUOY_COUNT) // first NPC sprite index
#define NPC_TURN 2 // binary degrees/loop = the player's full turn rate
u16 npcX[NPC_COUNT], npcY[NPC_COUNT]; // world units, wrap & 4095
s16 npcFX[NPC_COUNT], npcFY[NPC_COUNT]; // sub-unit accumulators (8.8)
u16 npcSpd[NPC_COUNT];                  // current speed, 8.8 world/loop
u8 npcTheta[NPC_COUNT], npcWp[NPC_COUNT];
s8 npcBias[NPC_COUNT]; // per-racer lateral aim offset: no shared line
u8 npcA; // npcTrig interface
s16 npcSin, npcCos;
u8 bj; // separation pass
u16 ox, oy;
// race flow (phase 4): countdown -> racing -> finished
#define RACE_LAPS 3
// schedule rubber-banding: four speed tiers picked from (should this racer
// still be ahead of the player?) x (how far ahead is it really?). The tiers
// are PERCENTAGES OF THE PLAYER'S MEASURED PACE (a slow EMA of forward
// speed), so they self-calibrate to any course and any driver - absolute
// numbers broke on every course redesign. HOLD stays below player pace so
// a racer passed on schedule drifts back and never re-passes.
u16 paceEma; // player pace, 8.8 world/loop, ~3s window
#define SPD_FAST (paceEma + (paceEma >> 2))                  // +25%: catch up
#define SPD_CRUISE (paceEma)                                 // match the player
#define SPD_HOLD (paceEma - (paceEma >> 3))                  // -12%: drift back
#define SPD_SLOW (paceEma - (paceEma >> 2) - (paceEma >> 3)) // -37%: fade
u8 raceState;           // 0 countdown, 1 racing, 2 finished
u8 racePos, finPos, posAcc, goTimer;
u8 npcFade[NPC_COUNT]; // player lap at which this racer starts fading
u16 npcDist[NPC_COUNT], pDist, spdTgt;
// monotone progress (total waypoints consumed): laps are counted at the
// START LINE (waypoint 0), so nextWp alone no longer orders the field
u16 pProg, npcProg[NPC_COUNT];
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
// ---- wake spray: a conveyor of dithered cells under the stern ----
// A one-shot splash cannot work here: only ~24 world units of water are
// visible behind the ski, so anything world-anchored crosses it in two loops
// and is never seen twice. Instead a fixed ladder of 16x16 cells (two columns
// spanning the hull) scrolls DOWN at a rate taken from speed; each time it
// advances a whole cell the intensities shift down the ladder and a new one
// is written at the top from the current state - zero out of the water, more
// with speed, a burst on landing. The band is always populated, so the low
// loop rate stops mattering: it reads as a continuous stream whose bands
// travel backwards.
#define SPR_ROWS 4                     // 1 static source + 3 scrolling
#define SPRAY_SPR (NPC_SPR + NPC_COUNT) // 2 * SPR_ROWS sprites from here
#define SPRAY_CELL 16                  // cell height in scanlines
#define SPRAY_MIN 160  // impact speed (8.8) below which a landing is dry
#define SPRAY_BURST 1  // cells that carry the landing's peak intensity
u8 sprInt[SPR_ROWS]; // intensity per cell row, 0 = nothing drawn
s16 sprScroll;       // 8.8 pixels scrolled, signed: see the bob compensation
s16 sprOfs;          // whole-pixel scroll for this frame
u8 prevWater;        // last frame's waterline, to cancel the swell's bob
u16 sprWet;          // smoothed "churning water" = in-water forward speed
u8 sprBurst, sprLvl, sprKick;
s16 sprY;

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
// HDMA line runs for the OBJ window; counts cap at 127 per entry
static u8 *winPut(u8 *w, u16 n, u8 l, u8 r)
{
    while (n > 127)
    {
        *w++ = 127;
        *w++ = l;
        *w++ = r;
        n -= 127;
    }
    if (n)
    {
        *w++ = (u8)n;
        *w++ = l;
        *w++ = r;
    }
    return w;
}

//---------------------------------------------------------------------------------
// The OBJ window masks ONLY the submerged rows of the hull (waterline `top` to
// the sprite's last row `bot`) - about 5-10 rows. It used to mask everything
// below the waterline, which made the whole lower screen a no-sprite zone and
// forced buoys to push the line down (weakening the hull sink). Bounded this
// way, the wake conveyor below the stern is drawable and nothing else has to
// compromise.
static void buildWinTab(u16 top, u16 bot)
{
    u8 *w = winTab;
    w = winPut(w, top, 0xFF, 0x00); // above: empty window (left > right)
    if (bot >= top)
        w = winPut(w, bot - top + 1, 0x00, 0xFF); // submerged hull: masked
    // clear again below; one line is enough, HDMA holds it for the rest
    w = winPut(w, 1, 0xFF, 0x00);
    *w = 0x00;
}

//---------------------------------------------------------------------------------
// Shift the cell ladder down and write a new intensity at the top. Intensity
// comes from sprWet, a smoothed in-water speed, NOT an instantaneous sample:
// the ski bounces, and one airborne moment sampled at the wrong instant used
// to blank the whole band for several loops.
static void sprayInject(void)
{
    u8 i;
    for (i = SPR_ROWS - 1; i > 0; i--)
        sprInt[i] = sprInt[i - 1];
    if (sprBurst)
    {
        sprInt[0] = WAVE_SPRAY_LEVELS; // top level: a landing
        sprBurst--;
    }
    else if (sprWet < 500) // stopped, or genuinely out of the water
        sprInt[0] = 0;
    else
    {
        sprLvl = (u8)(sprWet >> 11);
        if (sprLvl > WAVE_SPRAY_LEVELS - 2)
            sprLvl = WAVE_SPRAY_LEVELS - 2;
        sprInt[0] = sprLvl + 1; // 0 is reserved for "nothing"
    }
}

//---------------------------------------------------------------------------------
// project a world point onto the screen the way buoys render: view-space
// transform, surface-row lookup (rides the occluding crest), hardware-divider
// column. in: pjX, pjY (world units). out: pjOk, and when visible pjV (view
// depth: scale ladder), pjCol (screen centre x), rdRow (surface row).
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
    dly += rdRow;
    bq = REG_RDDIV;
    if (bq > 140)
        return;
    bq = bu < 0 ? 128 - bq : 128 + bq;
    if (bq < 12 || bq > 232)
        return;
    pjV = (u16)bv;
    pjCol = bq;
    pjOk = 1;
}

// Scale-ladder switch depths (view units), shared by every scaling object.
// Anchored to the PLAYER's ski: its 32px art sits at WAVE_SKI_DIST, so the
// correct on-screen size is 32 * WAVE_SKI_DIST / v, and each switch belongs
// where that crosses the midpoint between neighbouring art sizes
// (32/24 -> 28px, 24/16 -> 20, 16/12 -> 14, 12/8 -> 10). Hand-tuned values
// were a whole notch early: an NPC ten units past the player's own ski
// already shrank while level with him.
#define SCALE_V1 229 // 32px art nearer than this
#define SCALE_V2 320 // 24px
#define SCALE_V3 457 // 16px
#define SCALE_V4 640 // 12px, then 8px (beyond the 620 draw cutoff today)

//---------------------------------------------------------------------------------
// draw OAM sprite `oid` (byte-offset id!) at the projected point: five scales,
// all bottom-anchored to the surface row so a scale change never reads as
// movement. `right` picks the red R art over the yellow L art.
static void drawLadder(u16 oid, u8 right)
{
    if (pjV < SCALE_V1)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, right ? 12 : 8, 0);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < SCALE_V2)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, right ? 68 : 64, 0);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < SCALE_V3)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, right ? 74 : 72, 0);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
    else if (pjV < SCALE_V4)
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
    if (pjV < SCALE_V1)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, 128, pal);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < SCALE_V2)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, 132, pal);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < SCALE_V3)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, 136, pal);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
    else if (pjV < SCALE_V4)
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
    oamInitGfxSet(&ski_tiles, WAVE_SKI_SHEET, &ski_pal, 32, 0, 0x6000,
                  OBJ_SIZE16_L32);
    // NPC racer recolours: OBJ palettes 1-3 (CGRAM 144-191), shared tiles
    dmaCopyCGram((u8 *)&npc_pals, 144, 96);
    oamSet(0, SKI_X, 140, 3, 0, 0, 0, 0);
    oamSetEx(0, OBJ_LARGE, OBJ_SHOW);
    for (bi = 1; bi < SPRAY_SPR + 2 * SPR_ROWS; bi++)
        oamSetVisible(bi << 2, OBJ_HIDE); // NB: OAM ids are byte offsets (x4)

    setPaletteColor(0, RGB8(16, 60, 150)); // deep azure zenith

    // Additive colour math with the fixed colour: BG1 = crest glow, and
    // the backdrop too (bit 5) - the baked COLDATA table ramps white into
    // the sky lines, so the azure pales toward the horizon for free
    REG_CGWSEL = 0x00;
    REG_CGADSUB = 0x21;

    // Window 1 masks OBJ on the main screen; HDMA moves the window edges so
    // the region below the waterline swallows the sprite
    REG_WOBJSEL = 0x02;
    REG_TMW = 0x10;

    setScreenOn();

    tick = 0;
    phaseAcc = 0;
    // start pose comes from the bake (behind the racing line's waypoint 0,
    // facing along the opening segment); the camera hangs back so the SKI
    // sits on the exported grid slot
    camTheta = WAVE_START_THETA;
    camTheta16 = (u16)WAVE_START_THETA << 8;
    npcA = WAVE_START_THETA;
    npcTrig();
    camSinVal = npcSin;
    camCosVal = npcCos;
    camPX = (u16)(WAVE_START_X - ((WAVE_SKI_DIST * npcSin) >> 7)) & 4095;
    camPY = (u16)(WAVE_START_Y - ((WAVE_SKI_DIST * npcCos) >> 7)) & 4095;
    skiY = -1536; // spawn below any wave: wet from frame one, bobs up
    skiVv = 0;
    skiVX = 0;
    skiVY = 0;
    fracX = 0;
    fracY = 0;
    wasInWater = 1;
    prevSin = npcSin; // matches the start heading: no spurious first pivot
    prevCos = npcCos;
    rotTimer = 0;
    rotOfs = 0;
    nextWp = 0; // BSS is not zero-initialised: clear all race state
    lapCount = 255; // -1: the rolling start's first line-crossing makes 0
    pProg = 0;
    lapTicks = 0;
    lastLap = 0;
    skiWX = WAVE_START_X; // autopilot reads these before the first pass
    skiWY = WAVE_START_Y;
    // NPC grid slots come from the bake too: just ahead of the player,
    // staggered in depth so no scanline drowns in sprites
    for (bi = 0; bi < NPC_COUNT; bi++)
    {
        npcTheta[bi] = WAVE_START_THETA;
        npcFX[bi] = 0;
        npcFY[bi] = 0;
        npcWp[bi] = 0;
        npcProg[bi] = 0;
        npcDist[bi] = 0;
    }
    npcBias[0] = -56; // green aims left of the line, purple right,
    npcBias[1] = 56;  // orange up the middle: three distinct lines
    npcBias[2] = 0;
    npcX[0] = WAVE_NPC_X0;
    npcY[0] = WAVE_NPC_Y0;
    npcX[1] = WAVE_NPC_X1;
    npcY[1] = WAVE_NPC_Y1;
    npcX[2] = WAVE_NPC_X2;
    npcY[2] = WAVE_NPC_Y2;
    // the race is scripted to unwind: green fades two laps in, purple one
    // lap in, orange from the gun - pass one racer per lap
    npcFade[0] = 2;
    npcFade[1] = 1;
    npcFade[2] = 0;
    sprScroll = 0; // BSS is not zero-initialised
    sprBurst = 0;
    sprKick = 0;
    sprWet = 0;
    prevWater = waveSkiRow[0];
    for (bi = 0; bi < SPR_ROWS; bi++)
        sprInt[bi] = 0;
    paceEma = 3000; // seeded near typical pace; the EMA takes over at GO
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
    buildWinTab(200, 210);

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
        if ((apd > 0 && apu < apd) || (vAlong < 600 && vAlong > -600))
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
                // player pace EMA (~3s window) drives the NPC speed tiers;
                // floored so a parked player still gets a beatable field
                apu = vAlong < 0 ? 0 : vAlong;
                paceEma += (s16)(apu - paceEma) >> 6;
                if (paceEma < 1500)
                    paceEma = 1500;
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
            {
                // ...and kicks the wake: the next few cells injected at the
                // top of the ladder carry the peak intensity, so the burst
                // is visibly thrown and then travels back down the band
                if (-skiVv >= SPRAY_MIN)
                {
                    sprBurst = SPRAY_BURST;
                    sprKick = 1; // inject at once: the burst belongs at the
                                 // stern on the frame you actually land
                }
                skiVv >>= 2;
            }
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
            // (>>6 not >>7: doubled thrust and top speed)
            if ((pad0 & KEY_B) && tick > 20) // grace while the spawn settles
            {
                skiVX += (THRUST * camSinVal) >> 6;
                skiVY += (THRUST * camCosVal) >> 6;
            }
            if (pad0 & KEY_Y) // reverse: half thrust, backwards
            {
                skiVX -= ((THRUST / 2) * camSinVal) >> 6;
                skiVY -= ((THRUST / 2) * camCosVal) >> 6;
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
            // laps count when CROSSING THE START LINE (waypoint 0), where
            // the chequered strip is painted - not at the last waypoint.
            // lapCount seeds at 255 (-1): the grid spawns inside waypoint
            // 0's radius, so the immediate first crossing is the rolling
            // start (-> 0, "LAP 1/3") and lap 1 runs the extra grid gap.
            if (nextWp == 0)
            {
                lapCount++;
                lastLap = lapTicks;
                lapTicks = 0;
                lastLapSec = (u8)(lapFr / 60); // division: once per lap
                lastLapTenth = (u8)((lapFr % 60) / 6);
                lapFr = 0;
                if (raceState == 1 && lapCount >= RACE_LAPS)
                {
                    raceState = 2; // chequered flag
                    finPos = racePos;
                }
            }
            nextWp++;
            if (nextWp >= WAVE_PATH_COUNT)
                nextWp = 0;
            pProg++;
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
                // each racer aims a little off the shared line (perp of
                // its heading) so three followers never stack up on it
                wpdx += (npcBias[bi] * npcCos) >> 7;
                wpdy -= (npcBias[bi] * npcSin) >> 7;
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
                // velocity along the (pre-turn) heading; >>5 before the
                // product keeps doubled speeds 16-bit, 8.8 accums as fracX
                apu = (s16)(bq >> 5);
                apc = (apu * npcSin) >> 2;
                apd = (apu * npcCos) >> 2;
                npcFX[bi] += apc;
                stepX = npcFX[bi] >> 8;
                npcFX[bi] &= 0x00FF;
                // NPCs pass straight through buoy cells (3): the lateral
                // bias lines cross them and snagging there looks broken.
                // Sand (1) and rope (2) still block.
                if (stepX)
                {
                    collOfs = ((npcY[bi] >> 5) & 127) * 128
                              + (((u16)(npcX[bi] + stepX) >> 5) & 127);
                    collProbe();
                    if (!collVal || collVal == 3)
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
                    if (!collVal || collVal == 3)
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
                        npcWp[bi] = 0;
                    npcProg[bi]++;
                }
                // schedule rubber-banding: is it ahead of the player?
                rubDiff = (s16)npcProg[bi] - (s16)pProg;
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
        {
            racePos = posAcc;
            // unstack the racers: when two are within ~28 texels the NPC
            // shoves off along the dominant axis (land-checked). Cheap
            // pairwise nudges, deliberately not a flocking system.
            for (bi = 0; bi < NPC_COUNT; bi++)
                for (bj = bi + 1; bj <= NPC_COUNT; bj++)
                {
                    if (bj < NPC_COUNT)
                    {
                        ox = npcX[bj];
                        oy = npcY[bj];
                    }
                    else // the player: NPCs yield, the player never moves
                    {
                        ox = skiWX;
                        oy = skiWY;
                    }
                    wpdx = (s16)((npcX[bi] - ox) & 4095);
                    if (wpdx > 2048)
                        wpdx -= 4096;
                    wpdy = (s16)((npcY[bi] - oy) & 4095);
                    if (wpdy > 2048)
                        wpdy -= 4096;
                    if (wpdx > 112 || wpdx < -112 || wpdy > 112 || wpdy < -112)
                        continue;
                    apu = wpdx < 0 ? -wpdx : wpdx;
                    apd = wpdy < 0 ? -wpdy : wpdy;
                    if (apu >= apd)
                    {
                        apc = wpdx >= 0 ? 6 : -6;
                        collOfs = ((npcY[bi] >> 5) & 127) * 128
                                  + (((u16)(npcX[bi] + apc) >> 5) & 127);
                        collProbe();
                        if (!collVal || collVal == 3) // buoys don't block NPCs
                            npcX[bi] = (npcX[bi] + apc) & 4095;
                    }
                    else
                    {
                        apc = wpdy >= 0 ? 6 : -6;
                        collOfs = (((u16)(npcY[bi] + apc) >> 5) & 127) * 128
                                  + ((npcX[bi] >> 5) & 127);
                        collProbe();
                        if (!collVal || collVal == 3)
                            npcY[bi] = (npcY[bi] + apc) & 4095;
                    }
                }
        }
#endif

        // split velocity into forward/side components along the heading
        // (>>5 pre-shift: doubled speeds would overflow the old >>4 * 127)
        vAlong = ((skiVX >> 5) * camSinVal + (skiVY >> 5) * camCosVal) >> 2;
        vSide = ((skiVX >> 5) * camCosVal - (skiVY >> 5) * camSinVal) >> 2;
        if (inWater)
        {
            // gravel grip: kill a chunk of the slip each loop, and let the
            // rudder convert some of it into forward drive (momentum keeps)
            vAlong += (vSide < 0 ? -vSide : vSide) >> 3;
            vSide -= vSide >> 3;
            skiVX = ((vAlong >> 5) * camSinVal + (vSide >> 5) * camCosVal) >> 2;
            skiVY = ((vAlong >> 5) * camCosVal - (vSide >> 5) * camSinVal) >> 2;
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
                drawSki((NPC_SPR + bi) << 2, (u8)(1 + bi));
            else
                oamSetVisible((NPC_SPR + bi) << 2, OBJ_HIDE);
        }
#endif
        // ---- wake conveyor: scroll, then re-fill the top cell ----
        // Scroll rate is a chosen multiple of speed, not the true water
        // velocity (which crosses the whole band in a single loop and just
        // aliases into flicker). 1:1 with sprWet is ~18px/loop flat out.
        // Cost is irrelevant here - once per loop - so this can be any factor;
        // sums of shifts just keep it tidy: x>>1 half, x+(x>>1) 1.5x,
        // x-(x>>2) 0.75x, and so on.
        apu = inWater ? vAlong : 0; // churn: only water throws spray
        if (apu < 0)
            apu = 0;
        sprWet += (s16)(apu - sprWet) >> 2; // ~4-loop smoothing
        sprScroll += (s16)sprWet;
        // Cancel the bob: the ladder hangs off waterRow, which rides up and
        // down with the swell, so without this the wake is dragged along with
        // the ski instead of staying planted in the water. Moving up the
        // screen (waterRow shrinking) pushes the scroll forward by the same
        // number of rows, which holds the trailing cells still.
        sprScroll += ((s16)prevWater - (s16)waterRow) << 8;
        prevWater = waterRow;
        if (sprKick) // a landing: put the burst at the stern immediately
        {
            sprKick = 0;
            sprScroll = 0;
            sprayInject();
        }
        while (sprScroll >= (SPRAY_CELL << 8))
        {
            sprScroll -= SPRAY_CELL << 8;
            sprayInject();
        }
        // a descent can push the scroll negative (the ski settling onto its
        // own wake); one cell's worth is allowed, and cells that would land
        // above the waterline are skipped below rather than dragged down
        if (sprScroll < -(SPRAY_CELL << 8))
            sprScroll = -(SPRAY_CELL << 8);
        // whole pixels, biased so the shift stays on a positive value
        sprOfs = ((sprScroll + 8192) >> 8) - 32;
        for (bi = 0; bi < SPR_ROWS; bi++)
        {
            // cells emerge from under the stern and march down; the top of
            // cell 0 sits inside the window-masked band, so a new cell fades
            // in from behind the hull instead of popping into view
            // Cell 0 is a STATIC source pinned at the waterline; cells 1+ are
            // the conveyor proper, sliding out from under it. Without the
            // static cell the scroll offset left a growing bare gap between
            // the stern and the top of the band. Nothing is ever drawn above
            // waterRow, so spray can't appear beside the rider.
            sprY = bi ? (s16)waterRow + sprOfs + (bi - 1) * SPRAY_CELL
                      : (s16)waterRow;
            // cells may hang off the bottom - the screen edge clips them for
            // free, which is what keeps the short band looking full - but
            // never above the waterline, where they'd show beside the rider
            if (!sprInt[bi] || sprY > 223 || sprY < (s16)waterRow)
            {
                oamSetVisible((SPRAY_SPR + (bi << 1)) << 2, OBJ_HIDE);
                oamSetVisible((SPRAY_SPR + (bi << 1) + 1) << 2, OBJ_HIDE);
                continue;
            }
            bq = WAVE_SPRAY_CELL + ((sprInt[bi] - 1) << 1);
            oamSet((SPRAY_SPR + (bi << 1)) << 2, SKI_X, (u16)sprY, 3, 0, 0,
                   bq, 0);
            oamSetEx((SPRAY_SPR + (bi << 1)) << 2, OBJ_SMALL, OBJ_SHOW);
            // right column hflipped so the two halves are not twins
            oamSet((SPRAY_SPR + (bi << 1) + 1) << 2, SKI_X + 16, (u16)sprY, 3,
                   1, 0, bq, 0);
            oamSetEx((SPRAY_SPR + (bi << 1) + 1) << 2, OBJ_SMALL, OBJ_SHOW);
        }

        // mask only the hull's submerged rows (see buildWinTab)
        sprY = sprTop + 31;
        if (sprY > 223)
            sprY = 223;
        buildWinTab(waterRow, (u16)sprY);

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
            // NPC 0 total progress vs the player's (debug)
            uiPrint(25, 2, "N");
            uiPrintNum(26, 2, npcProg[0], 3);
            uiPrintNum(29, 2, pProg, 3);
#endif
#else
            // ---- race HUD ----
            uiPrint(0, 0, "LAP ");
            // lapCount 255 = the instant before the rolling start crosses
            // the line; show lap 1
            dly = lapCount == 255 ? 1
                : lapCount < RACE_LAPS ? (u16)lapCount + 1 : RACE_LAPS;
            uiPrintNum(4, 0, dly, 1);
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
