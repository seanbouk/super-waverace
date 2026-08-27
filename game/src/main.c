/*---------------------------------------------------------------------------------
    Super Waverace — jet ski on the rolling sea

    HDMA channels per frame:
      ch0 CGRAM   ($2121)  : sand distance-fade (entry 8 per band, baked
          ROM; the mode 1/7 switch rides a scanline IRQ - see camera.asm)
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

// per-course loaders (camera.asm): straight copy / RLE decode into WRAM
// bank $7F, source + destination via the globals below
extern void copyTo7F(void);
extern void mapTo7F(void);
dmaMemory cpSrc; // ROM address (addr+bank)
u16 cpDst;       // $7F offset
u16 cpLen;       // bytes (even), copyTo7F only
dmaMemory mapBuf; // the $7F8000 decode buffer, as a pointer for the lib
u8 courseSel;     // menu cursor; persists between menus
extern char ski_tiles, ski_pal, npc_pals;
extern char tall_tiles; // OBJ name table 2: the stacked tall racers
extern char lamp_pal;   // start-tree lamps: own OBJ palette (4) - the ski
                        // palette's slots are all rider roles now
extern void buildCamTables(void);
extern void collProbe(void); // camera.asm: reads the collision byte-map
extern void rowDepth(void);  // camera.asm: screen row for a view depth
extern void npcTrig(void);   // camera.asm: npcA (u8 heading) -> npcSin/npcCos
// camera.asm: project a world point onto the screen the way buoys render -
// view-space transform, surface-row lookup (rides the occluding crest, so a
// buoy tucked behind a wave rides up onto it), hardware-divider column.
// in: pjX, pjY (world units). out: pjOk, and when visible pjV (view depth:
// scale ladder), pjCol (screen centre x), rdRow (surface row).
extern void projectPoint(void);
// camera.asm ski-math leaves (tcc's 16-bit multiply is a ~100-cycle library
// call; these use the hardware multiplier, bit-exact to the old C). The
// decisions stay here: grip between split/merge, throttle keys, pivot test.
extern void skiSplit(void);   // vAlong/vSide from skiVX/VY along the heading
extern void skiMerge(void);   // skiVX/VY back from vAlong/vSide
extern void skiWorld(void);   // skiWX/WY = camP + (skiDist8 * trig) >> 7
extern void skiThrustF(void); // skiVX/VY += (thrF8 * trig) >> 6
extern void skiThrustR(void); // skiVX/VY -= (thrR8 * trig) >> 6
extern void camPivot(void);   // camP += (skiDist8 * (prev - cur trig)) >> 7
extern void npcAim(void); // aimT/aimP/aimBias + npcTrig -> wpdx/wpdy (bias-
                          // aimed), apc = cross, apd = dot
extern void npcVel(void); // apc/apd = ((bq >> 5) * npcSin/Cos) >> 2
extern void irqOn(void);  // camera.asm: cli, once the timer regs are set
extern void waveRawLoad(void); // camera.asm: decode wrSrc's delta-d stream
                               // + synthesise a into WRAM $7F

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
// constants handed to the asm ski helpers (the multiplier wants u8; all
// three fit today - see the init asserts-by-comment)
u8 skiDist8, thrF8, thrR8;

// build-time debug: drive itself (the emulator test runner has no input);
// also auto-confirms the course-select menu and the results screen
#define AUTOPILOT 1

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
#define REG_BG3HOFS (*(vuint8 *)0x2111)
#define REG_BG1HOFS (*(vuint8 *)0x210D)
#define REG_BG1VOFS (*(vuint8 *)0x210E)
#ifndef REG_COLDATA
#define REG_COLDATA (*(vuint8 *)0x2132)
#endif
#ifndef REG_TM
#define REG_TM (*(vuint8 *)0x212C)
#endif
#define REG_BGMODE (*(vuint8 *)0x2105)
#define REG_HTIMEL (*(vuint8 *)0x4207)
#define REG_HTIMEH (*(vuint8 *)0x4208)
#define REG_VTIMEL (*(vuint8 *)0x4209)
#define REG_VTIMEH (*(vuint8 *)0x420A)
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
u16 pjPfA, pjPfV, pjPfLines; // projection-block profile (DEBUG_UI only)
// collision
u16 collOfs;
u8 collVal, collHere;
u16 skiWX, skiWY;
s16 stepX, stepY;
// buoys
u16 rdV, rdRow, rdD;
u16 bq, dly;
u8 bi;
// race progress (phase 1: player only) + waypoint-chaser steering
u8 nextWp, lapCount;
u16 lapTicks, lastLap;
s16 wpdx, wpdy, apc, apd, apu;
// NPC racers (phase 2): kinematic waypoint followers, on the OAM sprites
// after the ski and the course buoys
#define NPC_COUNT 3
// OAM ORDER IS SPRITE-VS-SPRITE PRIORITY (lower id = in front; the
// priority field only orders against backgrounds). Layout: player pair
// (0 bottom, 1 top), buoys 2.., then the NPC PAIRS - assigned by depth
// each tick, nearest racer first, so passes stack correctly and a
// racer's two halves always sit in the same layer.
#define NPC_SPR (2 + WAVE_MAX_BUOYS) // first NPC id; 2 consecutive each
#define NPC_TURN 2 // binary degrees/loop = the player's full turn rate
u16 npcX[NPC_COUNT], npcY[NPC_COUNT]; // world units, wrap & 4095
s16 npcFX[NPC_COUNT], npcFY[NPC_COUNT]; // sub-unit accumulators (8.8)
u16 npcSpd[NPC_COUNT];                  // current speed, 8.8 world/loop
u8 npcTheta[NPC_COUNT], npcWp[NPC_COUNT];
s8 npcBias[NPC_COUNT]; // per-racer lateral aim offset: no shared line
u8 npcA; // npcTrig interface
u8 npcSinMag, npcSinNeg, npcCosMag, npcCosNeg; // sign-magnitude, for the asm
u16 aimTX, aimTY, aimPX, aimPY; // npcAim interface: target / position
s16 aimBias;                    // ...and the lateral line offset
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
// power (buoy chain): pass the armed buoy on its correct side -> +1 (cap
// 5), wrong side -> back to 0. thrTab rescales THRUST per level, so both
// acceleration and top speed ride the chain; [3] is the pre-power feel.
// RAM, filled at init: tcc cannot read far ROM const data.
u8 power, nextGate, gateNeg, gj, pwDrawn;
u8 thrTab[6];
s16 gRel, gAlong, gLat;
s16 rubDiff;
// race + lap clocks: REAL frames from snes_vblank_count (the loop rate
// varies, so loop ticks are not time); digits maintained incrementally
u8 rMin, rSecT, rSecU, rTick; // rTick = frame accum (also counts the countdown)
u16 lapFr;                    // frames this lap
u8 lastLapSec, lastLapTenth;
// HUD drawn-state (redraw only on change - uiPrint per tick is real cost):
// hBan = FINISH! banner over the rank/lap cells
u8 hudInit, hRank, hLapD, hSpd, hBan, finTk, hMinD, hSecU;
char pwBuf[6]; // power pip string, built on change
// start-light tree: 6 sprites after the spray block. Reds count the
// gun down one at a time, greens light together at GO, then the whole
// tree floats up and hides row by row as it reaches the HUD band.
#define LIGHT_SPR (SPRAY_SPR + 2 * SPRAY_ROWS)
u8 raceDone, startHeld; // exit-to-menu flow (START on the results)
u8 menuT;               // menu/results timers (autopilot auto-advance)
u8 ltState, ltT, ltRed; // 0 showing, 2 rising, 3 done
s16 ltY;
// per-NPC projection results, buffered so the OAM pairs can be assigned
// nearest-first (see the OAM-order note at NPC_SPR)
u16 npjV[NPC_COUNT], npjC[NPC_COUNT], npjR[NPC_COUNT];
u8 npjOk[NPC_COUNT], nord[NPC_COUNT], ns, nt;
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
#define SPRAY_ROWS 4                     // 1 static source + 3 scrolling
#define SPRAY_SPR (NPC_SPR + 2 * NPC_COUNT) // 2*SPRAY_ROWS sprites from here
#define SPRAY_CELL 16                  // cell height in scanlines
#define SPRAY_IMPACT_MIN 160 // vertical landing speed (8.8) for a burst
#define SPRAY_BURST_CELLS 1  // cells that carry the landing's peak intensity
// churn thresholds, in the same 8.8 world/loop units as vAlong (top ~4600):
// below WET_MIN the hull is not throwing water at all, and WET_SHIFT sets how
// much more speed each art level wants (1 << 10 = 1024 per level)
#define SPRAY_WET_MIN 1100
#define SPRAY_WET_SHIFT 10
// while anything is left on the ladder the scroll keeps ticking at least this
// fast, so stopping (or reversing, or taking off) lets the wake wash away
// instead of freezing on screen
#define SPRAY_DRAIN 400
u8 sprInt[SPRAY_ROWS]; // intensity per cell row, 0 = nothing drawn
s16 sprScroll;       // 8.8 pixels scrolled, signed: see the bob compensation
s16 sprOfs;          // whole-pixel scroll for this frame
u8 prevWater;        // last frame's waterline, to cancel the swell's bob
u16 sprWet;          // smoothed "churning water" = in-water forward speed
u8 sprBurst, sprLvl, sprKick;
s16 sprY, sprChurn, sprRate; // block-local scratch: this effect was
u8 sprAny;                   // unreadable borrowing apu/apd/bj

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
    for (i = SPRAY_ROWS - 1; i > 0; i--)
        sprInt[i] = sprInt[i - 1];
    if (sprBurst)
    {
        sprInt[0] = WAVE_SPRAY_LEVELS; // top level: a landing
        sprBurst--;
    }
    else if (sprWet < SPRAY_WET_MIN) // too slow, reversing, or airborne
        sprInt[0] = 0;
    else
    {
        // map the speed range ABOVE the threshold onto the art levels, so
        // the ramp spans what you actually drive rather than starting at zero
        sprLvl = (u8)((sprWet - SPRAY_WET_MIN) >> SPRAY_WET_SHIFT);
        if (sprLvl > WAVE_SPRAY_LEVELS - 2)
            sprLvl = WAVE_SPRAY_LEVELS - 2;
        sprInt[0] = sprLvl + 1; // 0 is reserved for "nothing"
    }
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
// racers live in OBJ name table 2 (VRAM 0x7000): OAM tile bit 8 is byte
// 3 bit 0 of the entry; oamSet takes the low 8 bits, so the table bit is
// OR'd in after every tall oamSet (idempotent if the lib ever sets it)
#define OAM_TALL(oid) (oamMemory[(oid) + 3] |= 1)

static void drawSki(u16 oid, u16 tid, u8 pal)
{
    // two stacked sprites per racer (bottom carries the waterline, top
    // rides above it), one shared projection - names: top n, bottom n+64
    // in the 32-wide slots, n / n+32 in the 16-wide ones
    if (pjV < SCALE_V1)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, 72, pal);
        oamSet(tid, pjCol - 16, rdRow - 63, 3, 0, 0, 8, pal);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
        oamSetEx(tid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < SCALE_V2)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, 76, pal);
        oamSet(tid, pjCol - 16, rdRow - 63, 3, 0, 0, 12, pal);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
        oamSetEx(tid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < SCALE_V3)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, 160, pal);
        oamSet(tid, pjCol - 8, rdRow - 31, 3, 0, 0, 128, pal);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
        oamSetEx(tid, OBJ_SMALL, OBJ_SHOW);
    }
    else if (pjV < SCALE_V4)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, 162, pal);
        oamSet(tid, pjCol - 8, rdRow - 31, 3, 0, 0, 130, pal);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
        oamSetEx(tid, OBJ_SMALL, OBJ_SHOW);
    }
    else
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, 132, pal);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
        oamSetVisible(tid, OBJ_HIDE); // smallest scale is one sprite
    }
    OAM_TALL(oid);
    OAM_TALL(tid);
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
// every vblank (lib NMI callback): frame top is mode 1 + BG3 priority;
// the scanline IRQ flips to mode 7 at the sky switch line
static void vblTop(void)
{
    REG_BGMODE = 0x09;
}

//---------------------------------------------------------------------------------
// courseLoad - everything that changes with the course: geometry + wave
// profile (courseGeom/waveProfLoad, generated by the bake), the wave d/a
// expansion, the packed collision copy to $7F7000, and the mode-7 map
// (RLE-decoded through $7F8000) + tiles + palette into VRAM. Needs force
// blank: VRAM/CGRAM uploads, and waveRawLoad borrows the PPU multiplier.
static void courseLoad(u8 c)
{
    setScreenOff();
    courseGeom(c);
    waveProfLoad(courseProf);
    waveRawLoad();
    // NB member-by-member: tcc copies only 16 BITS of a pointer-var to
    // pointer-var assignment (the bank byte never lands) - the (u8*)&sym
    // literal stores in the generated courseGeom are fine, variable
    // copies are not
    cpSrc.mem.c.addr = csColl.mem.c.addr; // packed coll -> collProbe home
    cpSrc.mem.c.bank = csColl.mem.c.bank;
    cpDst = 0x7000;
    cpLen = 4096;
    copyTo7F();
    cpSrc.mem.c.addr = csMap.mem.c.addr; // map -> decode buffer -> VRAM
    cpSrc.mem.c.bank = csMap.mem.c.bank;
    cpDst = 0x8000;
    mapTo7F();
    mapBuf.mem.p = (u8 *)0;
    mapBuf.mem.c.addr = 0x8000;
    mapBuf.mem.c.bank = 0x7F;
    // mode-7 VRAM planes by hand (NOT bgInitMapTileSet7: its 512-byte
    // palette load wipes the UI/sky/HUD/OBJ palettes, which load once at
    // boot - this bit as all-black sprites + a garbage HUD band):
    // tiles = high bytes ($2119, VMAIN $80), map = low bytes ($2118,
    // VMAIN $00), then just the palette entries the course owns
    dmaCopyVram7(csTiles.mem.p, 0x0000, csTilLen, 0x80, 0x1900);
    dmaCopyVram7(mapBuf.mem.p, 0x0000, 16384, 0x00, 0x1800);
    dmaCopyCGram(csPal1.mem.p, 1, 30);   // 1-15: water + course block
    dmaCopyCGram(csPal48.mem.p, 48, 8);  // 48-51: checker + teal
    setScreenOn();
}

//---------------------------------------------------------------------------------
// texture rotation: palette cycling from the per-course RAM table (the
// old baked-literal switch went when courses multiplied)
static void waveRotateStep(u8 o)
{
    u8 i, j;
    for (i = 0; i < wvRotCount; i++)
    {
        j = i + o;
        if (j >= wvRotCount)
            j -= wvRotCount;
        setPaletteColor(wvRotStart + i, rotCols[j]);
    }
}

//---------------------------------------------------------------------------------
// raceInit - EVERY race-scoped variable is set here, deliberately: BSS is
// not zero-initialised and the race runs more than once per boot now, so
// anything missed inherits the previous race's tail (or power-on garbage).
static void raceInit(void)
{
    REG_NMITIMEN = 0xB1; // NMI + V=V,H=H timer IRQ + auto-joypad (the
                         // course select parks the timer IRQ)
    oamSet(0, SKI_X, 140, 3, 0, 0, 64, 0);
    oamSetEx(0, OBJ_LARGE, OBJ_SHOW);
    OAM_TALL(0);
    oamSet(4, SKI_X, 108, 3, 0, 0, 0, 0); // player top: id 1, right
    oamSetEx(4, OBJ_LARGE, OBJ_SHOW);     // behind the bottom half
    OAM_TALL(4);
    for (bi = 2; bi < LIGHT_SPR + 6; bi++)
        oamSetVisible(bi << 2, OBJ_HIDE); // NB: ids are byte offsets (x4)

    tick = 0;
    phaseAcc = 0;
    // start pose comes from the bake (behind the racing line's waypoint 0,
    // facing along the opening segment); the camera hangs back so the SKI
    // sits on the exported grid slot
    camTheta = startTheta;
    camTheta16 = (u16)startTheta << 8;
    // pre-zero the bytes camera.asm's 16-bit u8 reads overrun (lda.l +
    // and #$00FF grabs the NEIGHBOUR byte too): masked, so harmless - but
    // seeding them keeps Mesen's uninitialised-read log clean, so a REAL
    // read-before-write stands out
    npcSinMag = 0;
    npcSinNeg = 0;
    npcCosMag = 0;
    npcCosNeg = 0;
    aimTX = 0;
    aimTY = 0;
    aimPX = 0;
    aimPY = 0;
    npcA = startTheta;
    npcTrig();
    camSinVal = npcSin;
    camCosVal = npcCos;
    // the mag/sign quads normally come from buildCamTables, but skiWorld
    // and skiSplit consume them BEFORE the first build - seed them here or
    // the first loop computes ski math from BSS garbage
    camSinMag = (u8)(npcSin < 0 ? -npcSin : npcSin);
    camSinNeg = npcSin < 0 ? 1 : 0;
    camCosMag = (u8)(npcCos < 0 ? -npcCos : npcCos);
    camCosNeg = npcCos < 0 ? 1 : 0;
    camPX = (u16)(startX - ((WAVE_SKI_DIST * npcSin) >> 7)) & 4095;
    camPY = (u16)(startY - ((WAVE_SKI_DIST * npcCos) >> 7)) & 4095;
    skiLean = 0; // BSS is not zero-initialised: garbage here reached
    skiFlip = 0; // oamSet as flip bits until the first steer input
    skiDist8 = WAVE_SKI_DIST; // 200 - must stay < 256 for the multiplier
    thrTab[0] = 96;     // power ladder: 67%..133% of the old fixed THRUST.
    thrTab[1] = 112;    // u8 (multiplier input), and top speed = t*32 keeps
    thrTab[2] = 128;    // even [5] inside the >>5/>>2 overflow envelope
    thrTab[3] = THRUST; // 144: power 3 = the pre-power feel
    thrTab[4] = 168;
    thrTab[5] = 192;
    power = 0; // you earn your speed: the chain starts empty
    nextGate = 0;
    gateNeg = 0;
    pwDrawn = 255; // force the bar's first draw
    hudInit = 0;   // ditto the HUD furniture + every value cell
    hRank = 255;
    hLapD = 255;
    hSpd = 255;
    hMinD = 255;
    hSecU = 255;
    hBan = 0;
    finTk = 0;
    ltState = 0;
    ltT = 0;
    ltRed = 0;
    ltY = 72;
    thrF8 = thrTab[0];
    thrR8 = thrF8 >> 1;
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
    skiWX = startX; // autopilot reads these before the first pass
    skiWY = startY;
    // NPC grid slots come from the bake too: just ahead of the player,
    // staggered in depth so no scanline drowns in sprites
    for (bi = 0; bi < NPC_COUNT; bi++)
    {
        npcTheta[bi] = startTheta;
        npcFX[bi] = 0;
        npcFY[bi] = 0;
        npcWp[bi] = 0;
        npcProg[bi] = 0;
        npcDist[bi] = 0;
    }
    npcBias[0] = -56; // green aims left of the line, purple right,
    npcBias[1] = 56;  // orange up the middle: three distinct lines
    npcBias[2] = 0;
    for (bi = 0; bi < NPC_COUNT; bi++)
    {
        npcX[bi] = npcGridX[bi];
        npcY[bi] = npcGridY[bi];
    }
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
    for (bi = 0; bi < SPRAY_ROWS; bi++)
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
    phase = 0;     // the physics reads waveSurfH/waveSkiRow[phase] and
    camBufOff = 0; // waveHdma follows waveTM[phase] BEFORE the camera block
                   // derives them on the first tick: garbage here reaches a
                   // garbage pointer (hardware WILL deliver garbage - zeroed
                   // emulator RAM hides it)
    vAlong = 0; // steering reads these before the first skiSplit
    vSide = 0;
    raceDone = 0;
    startHeld = 1; // the confirm press that started the race must not
    menuT = 0;     // instantly exit the results
    buildWinTab(200, 210);
}

static u16 menuPrev;
static u8 menuDirty;
static char menuBuf[16];

// the course list: cursor column + name (console font; names come from
// the bake's courseName). One 64-byte row DMA per course - vblank-safe.
static void menuDrawList(void)
{
    u8 i;
    for (i = 0; i < WAVE_COURSES; i++)
    {
        menuBuf[0] = i == courseSel ? '>' : ' ';
        menuBuf[1] = ' ';
        courseNameTo(i, menuBuf + 2); // writes the terminator too
        uiMenuRow(16 + i, 9, menuBuf);
    }
}

//---------------------------------------------------------------------------------
// course select: full-screen mode 1 - the scanline IRQ is parked and the
// wave HDMA is off, so every scanline renders the text band's BG1 + the
// sky + the BG3 clouds. The race HDMA leaves $210D holding some sea
// line's scroll and COLDATA holding some glow level: reset both or the
// menu shears/tints. Menu text lives in map rows the race never shows.
static void courseSelect(void)
{
    REG_NMITIMEN = 0x81; // NMI + auto-joypad; no timer IRQ during the menu
    REG_HDMAEN = 0;      // all eight streams off: TM/scroll/COLDATA are ours
    setScreenOff();
    for (bi = 0; bi < LIGHT_SPR + 6; bi++)
        oamSetVisible(bi << 2, OBJ_HIDE); // everything, player included
    REG_BG1HOFS = 0; // write-twice pairs, back-to-back (shared prev-latch)
    REG_BG1HOFS = 0;
    REG_BG1VOFS = 0;
    REG_BG1VOFS = 0;
    REG_COLDATA = 0xE0; // fixed-colour add: all channels to zero
    REG_TM = 0x15;      // BG1 + BG3 + OBJ (NEVER BG2 in mode 1: EXTBG jank)
    uiClear();
    uiFlush();
    uiMenuClearRows();
    uiMenuRow(14, 9, "SUPER WAVERACE");
    menuDrawList();
    uiMenuRow(17 + WAVE_COURSES, 10, "PRESS START");
    setScreenOn();
    startHeld = 1; // a confirm held over from the results must not re-fire
    menuT = 0;
    menuDirty = 0;     // BSS: read below before any set
    menuPrev = 0xFFFF; // treat every key as held until released once
    while (1)
    {
        WaitForVBlank();
        if (menuDirty) // row DMAs belong in vblank: right after the wait
        {
            menuDirty = 0;
            menuDrawList();
        }
        REG_BG3HOFS = (u8)(menuT >> 2); // idle cloud drift
        REG_BG3HOFS = 0;
        menuT++;
        pad0 = padsCurrent(0);
        if ((pad0 & KEY_UP) && !(menuPrev & KEY_UP) && courseSel)
        {
            courseSel--;
            menuDirty = 1;
        }
        if ((pad0 & KEY_DOWN) && !(menuPrev & KEY_DOWN)
            && courseSel < WAVE_COURSES - 1)
        {
            courseSel++;
            menuDirty = 1;
        }
        menuPrev = pad0;
        if (!(pad0 & KEY_START))
            startHeld = 0;
        else if (!startHeld)
            break;
#if AUTOPILOT
        if (menuT > 90)
            break;
#endif
    }
}

//---------------------------------------------------------------------------------
int main(void)
{
    camTabsInitHeaders();
    courseSel = 0; // BSS; the menu cursor survives between races after this

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
    dmaCopyCGram((u8 *)&lamp_pal, 192, 32); // start-tree lamps: palette 4
    // stacked tall racers: name table 2 right after the sheet
    dmaCopyVram((u8 *)&tall_tiles, 0x7000, WAVE_TALL_SHEET);
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

    // The mode-1 -> mode-7 switch rides a scanline IRQ (camera.asm
    // irqSwitch), freeing HDMA ch0 for the sand fade. The V+H timer
    // fires just before hblank on the line ABOVE the switch; the NMI
    // callback below restores mode 1 (+BG3 priority) at frame top.
    nmiSet(vblTop);
    REG_BGMODE = 0x09; // valid until the first IRQ fires
    REG_HTIMEL = 260 & 0xFF; // just before hblank; the handler spins
    REG_HTIMEH = 260 >> 8;   // on the hblank flag for the last few dots
    REG_VTIMEL = WAVE_SKY_SWITCH - 1;
    REG_VTIMEH = 0;
    REG_NMITIMEN = 0xB1; // NMI + V=V,H=H timer IRQ + auto-joypad
    irqOn();             // camera.asm: cli

    while (1) // game states: SELECT -> (raceInit) -> RACE/RESULTS -> repeat
    {
    courseSelect();
    courseLoad(courseSel);
    raceInit();

    while (!raceDone)
    {
        // the loop takes a variable 3-4 vblanks (see CLAUDE.md), so ALL
        // race timing accumulates real frames, never loop ticks
        loopFrames = snes_vblank_count - loopVbl;
        loopVbl = snes_vblank_count;
        pad0 = padsCurrent(0);
#if AUTOPILOT
#if WAVE_MAX_PATH > 0
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
            {
                pad0 &= ~(KEY_B | KEY_Y);
                // results: once the FINISH banner has run its ~6s, a
                // fresh START press hands back to the course select
                if (!(pad0 & KEY_START))
                    startHeld = 0;
                if (finTk >= 120)
                {
                    if ((pad0 & KEY_START) && !startHeld)
                        raceDone = 1;
#if AUTOPILOT
                    menuT++;
                    if (menuT > 90)
                        raceDone = 1;
#endif
                }
            }
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
                // gated on vAlong, not sprWet: forward speed survives a jump
                // (no water drag in the air) whereas the smoothed churn has
                // decayed to nothing by the time you land, so this keeps the
                // big landings loud while a bob at a standstill stays dry
                if (-skiVv >= SPRAY_IMPACT_MIN && vAlong >= SPRAY_WET_MIN)
                {
                    sprBurst = SPRAY_BURST_CELLS;
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
                skiThrustF();
            }
            if (pad0 & KEY_Y) // reverse: half thrust, backwards
            {
                skiThrustR();
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
        skiWorld();
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

#if WAVE_MAX_PATH > 0
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
            if (nextWp >= pathCount)
                nextWp = 0;
            pProg++;
        }
        lapTicks++;

#if WAVE_MAX_BUOYS > 0
        // ---- power gates: judge the armed buoy (racing-line order) when
        // the player crosses its perpendicular. The along-track dot product
        // flips sign on the crossing tick no matter how fast you move -
        // unlike a painted area, it cannot be tunnelled through. The buoy
        // is a LIMIT, not a target: any lateral distance counts, only the
        // side matters. The gate arms only near its own segment (gRel), so
        // the infinite perpendicular can never slice a distant course leg;
        // a gate left behind un-crossed (odd line) is judged where you are.
        for (gj = 0; gj < 2; gj++) // a tight pair can cross in one tick
        {
            gRel = (s16)nextWp - (s16)gateWp[nextGate];
            if (gRel < 0)
                gRel += pathCount;
            if (gRel > (pathCount >> 1))
                gRel -= pathCount;
            if (gRel < 0)
                break; // gate segments ahead: not in reach yet
            wpdx = (s16)((skiWX - gateX[nextGate]) & 4095);
            if (wpdx > 2048)
                wpdx -= 4096;
            wpdy = (s16)((skiWY - gateY[nextGate]) & 4095);
            if (wpdy > 2048)
                wpdy -= 4096;
            wpdx >>= 4; // +-128: the s8*s16 products below stay in s16
            wpdy >>= 4;
            if (gRel <= 2)
            {
                gAlong = wpdx * gateNx[nextGate] + wpdy * gateNy[nextGate];
                if (gAlong < 0)
                {
                    gateNeg = 1; // seen behind the line: armed
                    break;
                }
                if (!gateNeg)
                    break; // never seen behind: leave to the overdue path
            }
            // crossed (or overdue at gRel >= 3): judge the side, L wants
            // cross(dir, player - buoy) positive (bake-verified convention)
            gLat = gateNx[nextGate] * wpdy - gateNy[nextGate] * wpdx;
            if ((gLat >= 0) == (gateLeft[nextGate] != 0))
            {
                if (power < 5)
                    power++;
            }
            else
                power = 0;
            thrF8 = thrTab[power];
            thrR8 = thrF8 >> 1;
            gateNeg = 0;
            nextGate++;
            if (nextGate >= buoyCount)
                nextGate = 0;
        }
#endif

        // ---- NPC racers: kinematic waypoint followers (the autopilot's
        // steering brain), collision-probed so they cannot cross land ----
        posAcc = 1;
        if (raceState) // frozen on the grid until GO
            for (bi = 0; bi < NPC_COUNT; bi++)
            {
                aimTX = pathX[npcWp[bi]];
                aimTY = pathY[npcWp[bi]];
                aimPX = npcX[bi];
                aimPY = npcY[bi];
                aimBias = npcBias[bi]; // aims off the shared line (perp of
                                       // heading): followers never stack up
                npcA = npcTheta[bi];
                npcTrig();
                npcAim(); // wpdx/wpdy (bias-aimed), apc cross, apd dot
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
                // velocity along the (pre-turn) heading, 8.8 accums
                npcVel(); // apc/apd = ((bq >> 5) * trig) >> 2
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
                    if (npcWp[bi] >= pathCount)
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
        skiSplit();
        if (inWater)
        {
            // gravel grip: kill a chunk of the slip each loop, and let the
            // rudder convert some of it into forward drive (momentum keeps)
            vAlong += (vSide < 0 ? -vSide : vSide) >> 3;
            vSide -= vSide >> 3;
            skiMerge();
        }
        phaseAcc = (phaseAcc + WAVE_BASE_ROLL
                    + (((vAlong >> 4) * wvSteps) >> 4))
                   & wvMask;
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
            camPivot();
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
        // fires on that vblank, and waveHdma restores ch7 right after.
        // Two stacked sprites: the bottom keeps the old geometry (all the
        // waterline maths anchor to it), the rider's top half sits above
        oamSet(0, SKI_X, (u16)sprTop, 3, skiFlip, 0, skiLean ? 68 : 64, 0);
        OAM_TALL(0);
        oamSet(4, SKI_X, (u16)(sprTop - 32), 3, skiFlip, 0,
               skiLean ? 4 : 0, 0);
        OAM_TALL(4);

        // ---- buoys: project into view space, pick scale, ride the water ----
#if DEBUG_UI
        pjPfA = scanline(); // profile the projection block (buoys + NPCs)
        pjPfV = snes_vblank_count;
#endif
#if WAVE_MAX_BUOYS > 0
        for (bi = 0; bi < buoyCount; bi++)
        {
            pjX = buoyX[bi];
            pjY = buoyY[bi];
            projectPoint();
            if (pjOk)
                drawLadder((2 + bi) << 2, buoyType[bi]);
            else
                oamSetVisible((2 + bi) << 2, OBJ_HIDE);
        }
#endif
#if WAVE_MAX_PATH > 0
        // ---- start-light tree: reds count the gun down one at a time,
        // greens light together at GO, and a couple of seconds later the
        // whole tree floats up, each row hiding as it meets the HUD ----
        if (ltState < 3)
        {
            if (raceState == 0)
                ltRed = rTick >= 180 ? 3 : rTick >= 120 ? 2
                    : rTick >= 60 ? 1 : 0;
            else if (ltState == 0)
            {
                ltT++;
                if (ltT > 40) // ~2s of green before lift-off
                    ltState = 2;
            }
            if (ltState == 2)
                ltY -= 3;
            for (gj = 0; gj < 3; gj++)
            {
                ox = 104 + ((u16)gj << 4);
                if (ltY < 34) // row reaches the HUD band: gone
                    oamSetVisible((LIGHT_SPR + gj) << 2, OBJ_HIDE);
                else
                {
                    oy = raceState == 0 && gj < ltRed ? WAVE_LIGHT_CELL + 2
                                                      : WAVE_LIGHT_CELL;
                    oamSet((LIGHT_SPR + gj) << 2, ox, (u16)ltY, 3, 0, 0,
                           oy, 4);
                    oamSetEx((LIGHT_SPR + gj) << 2, OBJ_SMALL, OBJ_SHOW);
                }
                if (ltY + 16 < 34)
                {
                    oamSetVisible((LIGHT_SPR + 3 + gj) << 2, OBJ_HIDE);
                    ltState = 3; // bottom row gone too: tree done
                }
                else
                {
                    oy = raceState ? WAVE_LIGHT_CELL + 4 : WAVE_LIGHT_CELL;
                    oamSet((LIGHT_SPR + 3 + gj) << 2, ox, (u16)(ltY + 16),
                           3, 0, 0, oy, 4);
                    oamSetEx((LIGHT_SPR + 3 + gj) << 2, OBJ_SMALL, OBJ_SHOW);
                }
            }
        }

        // NPC racers ride the exact same pipeline: rear-view ski art,
        // one recolour palette per racer
        // project all three first, then hand out the OAM pairs NEAREST
        // FIRST: sprite-vs-sprite priority is OAM order, so a passing
        // racer must take the earlier pair or its halves layer wrongly
        for (bi = 0; bi < NPC_COUNT; bi++)
        {
            pjX = npcX[bi];
            pjY = npcY[bi];
            projectPoint();
            npjOk[bi] = pjOk;
            npjV[bi] = pjOk ? pjV : 0xFFFF; // culled: sorts last, hidden
            npjC[bi] = pjCol;
            npjR[bi] = rdRow;
            nord[bi] = bi;
        }
        if (npjV[nord[0]] > npjV[nord[1]]) // 3-element sort network
        {
            nt = nord[0];
            nord[0] = nord[1];
            nord[1] = nt;
        }
        if (npjV[nord[1]] > npjV[nord[2]])
        {
            nt = nord[1];
            nord[1] = nord[2];
            nord[2] = nt;
        }
        if (npjV[nord[0]] > npjV[nord[1]])
        {
            nt = nord[0];
            nord[0] = nord[1];
            nord[1] = nt;
        }
        for (ns = 0; ns < NPC_COUNT; ns++)
        {
            bi = nord[ns];
            if (npjOk[bi])
            {
                pjV = npjV[bi];
                pjCol = npjC[bi];
                rdRow = npjR[bi];
                drawSki((NPC_SPR + (ns << 1)) << 2,
                        (NPC_SPR + (ns << 1) + 1) << 2, (u8)(1 + bi));
            }
            else
            {
                oamSetVisible((NPC_SPR + (ns << 1)) << 2, OBJ_HIDE);
                oamSetVisible((NPC_SPR + (ns << 1) + 1) << 2, OBJ_HIDE);
            }
        }
#endif
#if DEBUG_UI
        pjPfLines = (snes_vblank_count - pjPfV) * 262 + scanline() - pjPfA;
#endif
        // ---- wake conveyor: scroll, then re-fill the top cell ----
        // Scroll rate is a chosen multiple of speed, not the true water
        // velocity (which crosses the whole band in a single loop and just
        // aliases into flicker). 1:1 with sprWet is ~18px/loop flat out.
        // Cost is irrelevant here - once per loop - so this can be any factor;
        // sums of shifts just keep it tidy: x>>1 half, x+(x>>1) 1.5x,
        // x-(x>>2) 0.75x, and so on.
        // churn: only water thrown FORWARD counts, so reverse and airborne
        // both decay to nothing
        sprChurn = inWater ? vAlong : 0;
        if (sprChurn < 0)
            sprChurn = 0;
        sprWet += (s16)(sprChurn - sprWet) >> 2; // ~4-loop smoothing
        sprAny = 0;
        for (bi = 0; bi < SPRAY_ROWS; bi++)
            sprAny |= sprInt[bi]; // anything still showing?
        sprRate = (s16)sprWet;
        if (sprAny && sprRate < SPRAY_DRAIN)
            sprRate = SPRAY_DRAIN; // wash the leftovers away after a stop
        sprScroll += sprRate;
        // Cancel the bob: the ladder hangs off waterRow, which rides up and
        // down with the swell, so without this the wake is dragged along with
        // the ski instead of staying planted in the water. Moving up the
        // screen (waterRow shrinking) pushes the scroll forward by the same
        // number of rows, which holds the trailing cells still.
        sprScroll += ((s16)prevWater - (s16)waterRow) << 8;
        prevWater = waterRow;
        // coming back up to speed from an empty band: seed it now rather than
        // waiting a whole cell's worth of scroll for the first inject
        if (!sprAny && sprWet >= SPRAY_WET_MIN)
            sprKick = 1;
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
        // whole pixels; the +8192 bias keeps the shift on a positive
        // value (tcc's signed >> is arithmetic - paceEma and sprWet rely
        // on it - the bias just makes this line's rounding self-evident)
        sprOfs = ((sprScroll + 8192) >> 8) - 32;
        for (bi = 0; bi < SPRAY_ROWS; bi++)
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
#if WAVE_MAX_PATH > 0
            // race progress: lap.waypoint, and the last lap's tick count
            uiPrint(23, 0, "L");
            uiPrintNum(24, 0, lapCount, 1);
            uiPrint(25, 0, ".");
            uiPrintNum(26, 0, nextWp, 2);
#if WAVE_MAX_BUOYS > 0
            uiPrint(28, 0, "G"); // power level + armed gate
            uiPrintNum(29, 0, power, 1);
            uiPrintNum(30, 0, nextGate, 2);
#endif
            uiPrint(21, 1, "P"); // projection block cost, scanlines
            uiPrintNum(22, 1, pjPfLines, 4);
#endif
            uiPrint(0, 1, "BUILD");
            uiPrintNum(5, 1, profLines, 4);
            uiPrint(9, 1, "LN PH");
            uiPrintNum(14, 1, phase, 2);
            uiPrint(17, 1, inWater ? "WET" : "AIR");
            uiPrint(27, 1, "F");
            uiPrintNum(28, 1, loopFrames, 1);
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
#if WAVE_MAX_PATH > 0
            // NPC 0 total progress vs the player's (debug)
            uiPrint(25, 2, "N");
            uiPrintNum(26, 2, npcProg[0], 3);
            uiPrintNum(29, 2, pProg, 3);
#endif
#else
            // ---- race HUD: gradient text (colour ramps live per pixel
            // row in the baked font), row 0 + columns 0/31 left clear for
            // CRT overscan, everything redrawn only on change ----
            if (!hudInit)
            {
                hudInit = 1;
                uiHudSmall(1, 1, HUD_PAL_TITLE, "TIME");
                uiHudSmall(9, 1, HUD_PAL_TITLE, "RANK");
                uiHudSmall(14, 1, HUD_PAL_TITLE, "LAP");
                uiHudSmall(18, 1, HUD_PAL_TITLE, "SPEED");
                uiHudSmall(25, 1, HUD_PAL_TITLE, "POWER");
                uiHudSmall(20, 3, HUD_PAL_BOT, "KM/H");
                uiHudBig(1, "0'00\"00");
                uiHudBig(15, "/3");
            }
            // race clock M'SS"hh: minutes/seconds on change, hundredths
            // (frames-in-second * 5 / 3) every tick; frozen off-race
            if (raceState == 1)
            {
                if (hMinD != rMin)
                {
                    hMinD = rMin;
                    uiHudBigDigit(1, rMin);
                }
                if (hSecU != rSecU)
                {
                    hSecU = rSecU;
                    uiHudBigDigit(3, rSecT);
                    uiHudBigDigit(4, rSecU);
                }
                bq = (((u16)rTick << 2) + rTick) / 3;
                dly = bq / 10;
                uiHudBigDigit(6, dly);
                uiHudBigDigit(7, bq - dly * 10);
            }
            // FINISH! banner over the rank/lap cells (the countdown and
            // GO live on the start-light tree now)
            bq = 0;
            if (raceState == 2 && finTk < 120)
            {
                bq = 5;
                finTk++; // ~6s of FINISH!, then the rank comes back
            }
            if (hBan != (u8)bq)
            {
                hBan = (u8)bq;
                uiHudBigClear(9, 8);
                if (hBan)
                    uiHudBig(9, "FINISH!");
                else
                {
                    uiHudBig(15, "/3"); // banner gone: restore + force
                    hRank = 255;
                    hLapD = 255;
                }
            }
            if (!hBan)
            {
                bq = raceState == 2 ? finPos : racePos;
                if (hRank != (u8)bq)
                {
                    hRank = (u8)bq;
                    uiHudBig(9, bq == 1 ? "1ST" : bq == 2 ? "2ND"
                             : bq == 3 ? "3RD" : "4TH");
                }
                // lapCount 255 = just before the rolling start: show lap 1
                dly = lapCount == 255 ? 1
                    : lapCount < RACE_LAPS ? (u16)lapCount + 1 : RACE_LAPS;
                if (hLapD != (u8)dly)
                {
                    hLapD = (u8)dly;
                    uiHudBigDigit(14, dly);
                }
            }
            // speed: vAlong>>6 reads ~48 km/h at power-0 top, 96 at full
            bq = vAlong > 0 ? (u16)vAlong >> 6 : 0;
            if (hSpd != (u8)bq)
            {
                hSpd = (u8)bq;
                dly = bq / 10;
                if (dly)
                    uiHudBigDigit(18, dly);
                else
                    uiHudBigClear(18, 1);
                uiHudBigDigit(19, bq - dly * 10);
            }
#if WAVE_MAX_BUOYS > 0
            // power pips under the POWER title: filled up to the chain
            if (pwDrawn != power)
            {
                pwDrawn = power;
                for (gj = 0; gj < 5; gj++)
                    pwBuf[gj] = gj < power ? '*' : '.';
                pwBuf[5] = 0;
                uiHudBig(25, pwBuf);
            }
#endif
#endif
        }

        WaitForVBlank();
        // cloud parallax: BG3 (the mode-1 sky overlay - BG2 belongs to
        // EXTBG, see ui.c) scrolls with the heading at 4px per binary
        // degree (from the 8.8 heading, so it stays smooth mid-turn) -
        // the 256px map wraps exactly 4 times per full turn, still a
        // perfect loop. Written in vblank, both bytes back-to-back (the
        // shared BGOFS prev-latch makes a split pair inherit garbage
        // from the HDMA's $210D stream)
        REG_BG3HOFS = (u8)(camTheta16 >> 6);
        REG_BG3HOFS = 0;
        uiFlush();
        waveHdma(phase, camBufOff);

        if (wvRotFrames)
        {
            rotTimer++;
            if (rotTimer >= (u8)(wvRotFrames >> 1))
            {
                rotTimer = 0;
                rotOfs++;
                if (rotOfs >= wvRotCount)
                    rotOfs = 0;
                waveRotateStep(rotOfs);
            }
        }
    }
    }
    return 0;
}
