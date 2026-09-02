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
extern void tilesTo7F(void); // pool index at $7FC000 + tpSrc -> $7F8000
dmaMemory cpSrc; // ROM address (addr+bank)
u16 cpDst;       // $7F offset
u16 cpLen;       // bytes (even), copyTo7F only
dmaMemory mapBuf; // the $7F8000 decode buffer, as a pointer for the lib
u8 courseSel;     // menu cursor; persists between menus
extern char ski_tiles, ski_pal; // OBJ palettes 0-3 + 5 are per course
                                // (csObj/csBuoy, loaded by courseLoad)
extern char tall_tiles; // OBJ name table 2: the stacked tall racers
extern char lamp_pal;   // start-tree lamps: own OBJ palette (4) - the ski
extern char cloud_map;  // BG3 cloud-strip map (titleBg3 restores it)
extern char title_pal;  // WAVERACER's OBJ palette 6 (CGRAM 224)
extern char title_pal2; // Super's OBJ palette 7 (CGRAM 240)
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
// also auto-confirms the course-select menu (picking AUTOPILOT_COURSE)
// and the results screen
#define AUTOPILOT 0
#define AUTOPILOT_COURSE 0
// harness build: the chaser drives the CHAMPIONSHIP races too (finish,
// points and standings all live) and every championship page auto-
// advances, so the whole 6-race loop cycles hands-free under a Lua
// screenshot sweep in Mesen GUI mode. ALWAYS 0 for release builds.
#define CHAMP_AUTO 0

#define TURN_SPEED 2
// championship intro flyover: skiVX/VY are SET each tick (no thrust,
// drag or air), INTRO_PASSES applications of INTRO_THRUST along the
// heading = 252*127/64*5 ~= 2500 (8.8) - about half racing pace
#define INTRO_THRUST 252
#define INTRO_PASSES 5
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
#ifndef REG_MOSAIC
#define REG_MOSAIC (*(vuint8 *)0x2106)
#endif
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
u8 hudInit, hRank, hLapD, hSpd, hBan, hMinD, hSecU;
u16 finFr; // frames since the player's finish: the race ends itself at 300
char pwBuf[8]; // power pips / the TT best-lap cell, built on change
// start-light tree: 6 sprites after the spray block. Reds count the
// gun down one at a time, greens light together at GO, then the whole
// tree floats up and hides row by row as it reaches the HUD band.
#define LIGHT_SPR (SPRAY_SPR + 2 * SPRAY_ROWS)
#define TITLE_SPR (LIGHT_SPR + 6) // 7 logo sprites (attract only)
#define TITLE_Y 54      // WAVERACER band: lines 54-85, inside the sky
#define TITLE_SUP_UP 18 // Super sits this much higher (same absolute Y as
                        // before WAVERACER moved down 10)
#define TITLE_WR_X 64   // WAVERACER's resting left edge ((256 - 127) / 2)
#define TITLE_SUP_X (TITLE_WR_X - 18) // Super rests 18px left of it
s16 ttlWx, ttlSx; // the words' sliding left edges (title animation)
u8 ttlWait;       // loops before the slide starts (~3s of empty sky)
u8 raceDone, startHeld; // exit-to-menu flow (START on the results)
u8 menuT;               // menu/results timers (autopilot auto-advance)
// ---- game flow: the title screen IS the attract mode (a chaser-driven
// race on SUNNY ISLAND behind the overlays); the main menu rides the same
// race. Confirm = START or A, back = B, everywhere.
#define RM_RACE 0  // normal racing HUD
#define RM_TITLE 1 // title strip + flashing PRESS START (attract)
#define RM_MENU 2  // main menu overlay (attract)
#define RM_INTRO 3 // championship intro card over the racing-line cruise
u8 raceMode;
u8 attract;  // the waypoint chaser drives (runtime; AUTOPILOT builds force it)
u8 menuSel;  // 0 championship / 1 time trials / 2 arcade (single races;
             // later also the door to 2P: P2 presses START on rider select)
u8 menuGo;   // menu confirmed: leave the attract race and dispatch
u8 raceTT;   // time trial: solo, endless laps, BEST-lap HUD cell
u8 playerPal;      // the picked rider = OBJ palette 0-3 (palette-only)
u8 npcPalTab[3];   // the other three riders, in palette order
u8 bestSec, bestTenth; // TT best lap (255 = none yet this race)
u8 ovlInit, ovlFlash;
// ---- championship: every course in folder order, 9/6/3/1 points by
// finish order (the full order of all four riders is snapshotted on the
// player's finish tick - nobody waits for the CPU), standings after each
// race, final standings at the end. Riders are palette-only, so the
// points tables index by palette (= rider id).
u8 champOn;    // a championship is in progress (quit from pause ends it)
u8 champRace;  // course index of the current race
u8 champStage; // 0 intro pending, 1 intro running, 2 race running
u8 champPts[4], racePts[4]; // running totals / the latest race's points
u8 champOrd[4];             // standings order (rider ids), best first
u16 chPr[4], chDs[4];       // finish-order sort keys (progress, distance)
u8 chPl[4];                 // ...and their rider ids
u8 sbLen;                   // menuBuf string builder length
// ---- race finish: riders park past the line as they finish, and from
// the player's finish a live results table floats in the sky (BG3 text
// over the cloud rows): finished riders in finish order with a flag (and
// their points in a championship), the rest in live race order below.
// 5s on, PRESS START ends the race (the unfinished are placed where they
// stand); 3s after everyone has finished it ends by itself.
u8 npcLap[NPC_COUNT], npcDone[NPC_COUNT]; // laps (255 seed) / parked
u8 finList[4], finCount;  // riders in finish order
u8 ordTab[4], ordPrev[4]; // live table order / last composed order
u8 skyUp, skyDirty, skyGo; // table shown / rows need DMA / prompt shown
// the start/finish line as a TRUE crossing (along-track dot vs the
// opening heading, armed behind the line): startNx/Ny = sin/cos of
// startTheta (1.7), lineArm/npcArm = seen behind it, lapBase = progress
// at the last counted lap (a lap needs ~a full loop of waypoints since)
s16 startNx, startNy;
s16 lnDx, lnDy; // line-test deltas: NOT wpdx/wpdy - the NPC waypoint-reach
                // test reads those AFTER the line test (this bit once)
u8 lineArm, npcArm[NPC_COUNT];
u16 lapBase, npcLapBase[NPC_COUNT];
#define SKY_GO_ROW 10      // PRESS START: the BG3 row just over the horizon
static u16 skyRows[5][UI_COLS]; // 4 table rows + the prompt row (RAM)
u8 apFine;   // chaser: this correction is small - steer at quarter rate
u8 apStuck;  // chaser: loops spent barely moving (wedged on a rope/shore)
u16 ovlPrev; // last RAW pad, for overlay + pause edge detection
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
// movement. `right` picks the red R art over the yellow L art. Buoys draw
// with their own OBJ palette (WAVE_BUOY_PAL, CGRAM 208): they used to
// borrow the player's, which the per-course ambient now recolours.
static void drawLadder(u16 oid, u8 right)
{
    if (pjV < SCALE_V1)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, right ? 12 : 8,
               WAVE_BUOY_PAL);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < SCALE_V2)
    {
        oamSet(oid, pjCol - 16, rdRow - 31, 3, 0, 0, right ? 68 : 64,
               WAVE_BUOY_PAL);
        oamSetEx(oid, OBJ_LARGE, OBJ_SHOW);
    }
    else if (pjV < SCALE_V3)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, right ? 74 : 72,
               WAVE_BUOY_PAL);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
    else if (pjV < SCALE_V4)
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, right ? 78 : 76,
               WAVE_BUOY_PAL);
        oamSetEx(oid, OBJ_SMALL, OBJ_SHOW);
    }
    else
    {
        oamSet(oid, pjCol - 8, rdRow - 15, 3, 0, 0, right ? 106 : 104,
               WAVE_BUOY_PAL);
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
// the X sign bit lives in the OAM high table (2 bits per sprite: x8 +
// size); oamSetEx(OBJ_SHOW) CLEARS it, so a negative x must set it back
// AFTER - or the sprite wraps to the right edge (x = -24 shows at 232)
#define OAM_X8(oid)     (oamMemory[512 + ((oid) >> 4)] |= (u8)(1 << ((((oid) >> 2) & 3) << 1)))

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
    mapBuf.mem.p = (u8 *)0;
    mapBuf.mem.c.addr = 0x8000;
    mapBuf.mem.c.bank = 0x7F;
    // tiles: the course's 256 pool ids -> $7FC000, then tilesTo7F pulls
    // each 64-byte tile out of the shared pool into the $7F8000 buffer
    // (16K); DMA it to the tile plane BEFORE the map reuses the buffer
    cpSrc.mem.c.addr = csTiles.mem.c.addr;
    cpSrc.mem.c.bank = csTiles.mem.c.bank;
    cpDst = 0xC000;
    cpLen = 512;
    copyTo7F();
    tilesTo7F();
    dmaCopyVram7(mapBuf.mem.p, 0x0000, 16384, 0x80, 0x1900);
    cpSrc.mem.c.addr = csMapDef.mem.c.addr; // codec default block -> $7FC200
    cpSrc.mem.c.bank = csMapDef.mem.c.bank;
    cpDst = 0xC200;
    cpLen = 256;
    copyTo7F();
    cpSrc.mem.c.addr = csMap.mem.c.addr; // map -> decode buffer -> VRAM
    cpSrc.mem.c.bank = csMap.mem.c.bank;
    cpDst = 0x8000;
    mapTo7F();
    // mode-7 VRAM planes by hand (NOT bgInitMapTileSet7: its 512-byte
    // palette load wipes the UI/sky/HUD/OBJ palettes, which load once at
    // boot - this bit as all-black sprites + a garbage HUD band):
    // tiles = high bytes ($2119, VMAIN $80), map = low bytes ($2118,
    // VMAIN $00), then just the palette entries the course owns
    dmaCopyVram7(mapBuf.mem.p, 0x0000, 16384, 0x00, 0x1800);
    dmaCopyCGram(csPal1.mem.p, 1, 30);   // 1-15: water + course block
    dmaCopyCGram(csPal48.mem.p, 48, 8);  // 48-51: checker + teal
    // the course's ambient light is baked into its OBJ palettes too:
    // 0-3 riders (+ spray in 0), 5 buoys. 4 (lamps) and the HUD stay lit
    dmaCopyCGram(csObj.mem.p, 128, 128);
    dmaCopyCGram(csBuoy.mem.p, 208, 32);
    // the sky is per course too: CGRAM 31 (HUD backdrop = zenith, via the
    // solid blank tile) + the band anchors 33-47 (zenith -> horizon),
    // backdrop 0 = horizon minus the strip's COLDATA add (so the mode-7
    // safe strip lands on the horizon colour), and the ambient-lit BG3
    // cloud pair
    dmaCopyCGram(csSky.mem.p, 31, 34);
    dmaCopyCGram(csCloud.mem.p, 29, 4);
    setPaletteColor(0, csSky0);
    // HUD glyph backgrounds (font index 15 of ramp rows 4-6) = the zenith,
    // same as the solid blank tile: colour 0 would show the backdrop,
    // which is horizon - strip add, not the sky top
    setPaletteColor(79, csZen);
    setPaletteColor(95, csZen);
    setPaletteColor(111, csZen);
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
    REG_MOSAIC = 0;      // a transition may have left the sweep at 15
    oamSet(0, SKI_X, 140, 3, 0, 0, 64, playerPal);
    oamSetEx(0, OBJ_LARGE, OBJ_SHOW);
    OAM_TALL(0);
    oamSet(4, SKI_X, 108, 3, 0, 0, 0, playerPal); // player top: id 1, right
    oamSetEx(4, OBJ_LARGE, OBJ_SHOW);     // behind the bottom half
    OAM_TALL(4);
    for (bi = 2; bi < TITLE_SPR + 8; bi++)
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
    startNx = npcSin; // the start line's forward normal (1.7), for the
    startNy = npcCos; // lap-crossing tests
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
    apFine = 0;  // BSS
    apStuck = 0;
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
    bestSec = 255; // TT: no best lap yet
    bestTenth = 0;
    nt = 0; // the three riders the player did NOT pick drive the NPCs
    for (bi = 0; bi < 4; bi++)
        if (bi != playerPal && nt < NPC_COUNT)
            npcPalTab[nt++] = bi;
    hudInit = 0;   // ditto the HUD furniture + every value cell
    hRank = 255;
    hLapD = 255;
    hSpd = 255;
    hMinD = 255;
    hSecU = 255;
    hBan = 0;
    finFr = 0;
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
        npcLap[bi] = 255; // -1, like lapCount: the grid crossing -> 0
        npcDone[bi] = 0;
        npcArm[bi] = 0;
        npcLapBase[bi] = 0;
    }
    lineArm = 0;
    lapBase = 0;
    finCount = 0;
    skyUp = 0;
    skyDirty = 0;
    skyGo = 0;
    for (bi = 0; bi < 4; bi++)
        ordPrev[bi] = 255;
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

static u16 ttlBuf[UI_COLS]; // scratch map row (menus, minimap, BG3 rows)
static u16 menuPrev;
static u8 menuDirty;
static char menuBuf[24]; // "> " + up to 20 chars (MENU_NAME_MAX) + NUL
static u16 menuRows[WAVE_COURSES][UI_COLS]; // composed list rows (RAM)

// the course list: cursor column + name (console font; names come from
// the bake's courseName). Composed into menuRows HERE - mid-frame, it is
// only RAM - and pushed to VRAM by menuDmaList right after WaitForVBlank.
// Composing costs ~45 scanlines per row under tcc: doing both after the
// wait put the DMAs into the next frame's active display, where the PPU
// drops VRAM writes - the cursor never moved on screen (courseSel did).
static void menuComposeList(void)
{
    u8 i;
    for (i = 0; i < WAVE_COURSES; i++)
    {
        menuBuf[0] = i == courseSel ? '>' : ' ';
        menuBuf[1] = ' ';
        courseNameTo(i, menuBuf + 2); // writes the terminator too
        uiMenuCompose(menuRows[i], 9, menuBuf);
    }
}

static void menuDmaList(void) // vblank / force blank only
{
    u8 i;
    for (i = 0; i < WAVE_COURSES; i++)
        uiMenuRowDma(menuRows[i], 16 + i);
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
    for (bi = 0; bi < TITLE_SPR + 8; bi++)
        oamSetVisible(bi << 2, OBJ_HIDE); // everything, player included
    REG_BG1HOFS = 0; // write-twice pairs, back-to-back (shared prev-latch)
    REG_BG1HOFS = 0;
    REG_BG1VOFS = 0;
    REG_BG1VOFS = 0;
    REG_COLDATA = 0xE0; // fixed-colour add: all channels to zero
    REG_MOSAIC = 0;     // a transition may have left the sweep at 15
    REG_TM = 0x15;      // BG1 + BG3 + OBJ (NEVER BG2 in mode 1: EXTBG jank)
    uiClear();
    uiFlush();
    uiMenuClearRows();
    uiMenuRow(14, 9, "SUPER WAVERACE");
    menuComposeList();
    menuDmaList(); // force blank: safe
    uiMenuRow(17 + WAVE_COURSES, 10, "PRESS START");
    // minimap cells: rows 22-27, cols 24-29 - written ONCE (per-row
    // partial DMAs so no text row is touched); the tiles + palette are
    // per-course, re-DMA'd on every cursor move below
    for (oy = 0; oy < 6; oy++)
    {
        for (bi = 0; bi < 6; bi++)
            ttlBuf[bi] = 0x1C00 | (WAVE_MINI_CHAR0 + oy * 6 + bi);
        dmaCopyVram((u8 *)ttlBuf, (u16)(0x4000 + (22 + oy) * 32 + 24), 12);
    }
    setScreenOn();
    startHeld = 1; // a confirm held over from the results must not re-fire
    menuT = 0;
    menuDirty = 1;     // BSS; 1 = upload the initial minimap in the loop
    menuPrev = 0xFFFF; // treat every key as held until released once
    courseGeom(courseSel); // csMini for the initial minimap upload
    while (1)
    {
        WaitForVBlank();
        if (menuDirty) // only the DMA kicks here: rows are pre-composed
        {
            menuDirty = 0;
            menuDmaList();
            // the highlighted course's minimap: courseGeom pointed csMini
            // at it mid-frame; 1152 B + 32 B is well inside the budget
            dmaCopyVram(csMini.mem.p, 0x4000 + WAVE_MINI_CHAR0 * 16, 1152);
            dmaCopyCGram(csMiniPal.mem.p, 112, 32);
        }
        REG_BG3HOFS = (u8)(menuT >> 2); // idle cloud drift
        REG_BG3HOFS = 0;
        menuT++;
        pad0 = padsCurrent(0);
        if ((pad0 & KEY_UP) && !(menuPrev & KEY_UP) && courseSel)
        {
            courseSel--;
            menuComposeList();
            courseGeom(courseSel); // repoints csMini for the vblank DMA
            menuDirty = 1;
        }
        if ((pad0 & KEY_DOWN) && !(menuPrev & KEY_DOWN)
            && courseSel < WAVE_COURSES - 1)
        {
            courseSel++;
            menuComposeList();
            courseGeom(courseSel);
            menuDirty = 1;
        }
        menuPrev = pad0;
        if (!(pad0 & KEY_START))
            startHeld = 0;
        else if (!startHeld)
            break;
#if AUTOPILOT
        if (menuT > 90)
        {
            courseSel = AUTOPILOT_COURSE;
            break;
        }
#endif
    }
}

//---------------------------------------------------------------------------------
// game-flow helpers: mosaic transitions, the BG3 title strip, placeholder
// full-screen pages, and the attract-menu overlay (the RM_ states)
static void mosaicSweep(u8 dir, u8 live)
{
    u8 s, v;
    for (s = 0; s < 16; s++)
    {
        v = dir ? (u8)(15 - s) : s;
        WaitForVBlank();
        REG_MOSAIC = (u8)((v << 4) | 0x07); // BG1+2+3: sea, EXTBG, clouds
        if (live) // in-race: the ISR's OAM DMA clobbered ch7's registers
            waveHdma(phase, camBufOff);
    }
    if (dir)
        REG_MOSAIC = 0;
}

// show: the title strip replaces the second cloud-strip map row (the BG3
// scroll is frozen while an overlay is up, so it sits still); hide: put
// the baked cloud rows back. Force blank inside - call between states.
static void titleBg3(u8 show)
{
    u16 i;
    setScreenOff();
    if (show)
    {
        u16 r;
        // clouds are OFF in attract (the logo sprites own that sky space)
        for (i = 0; i < UI_COLS; i++)
            ttlBuf[i] = 0x2000 | 0x1C00 | WAVE_CLOUD_CHAR0; // blank char
        for (r = 0; r < WAVE_CLOUD_TROWS; r++)
            dmaCopyVram((u8 *)ttlBuf, 0x4400 + (WAVE_CLOUD_ROW0 + r) * 32,
                        64);
    }
    else
        dmaCopyVram((u8 *)&cloud_map, 0x4400 + WAVE_CLOUD_ROW0 * 32,
                    WAVE_CLOUD_TROWS * 64);
    setScreenOn();
}

// full-screen mode-1 placeholder page (courseSelect's screen recipe):
// used by 2P VS. (post-jam) and CHAMPIONSHIP until its phase lands
static void textScreen(char *name)
{
    u8 armed = 0;
    REG_NMITIMEN = 0x81; // NMI + auto-joypad; timer IRQ parked
    REG_HDMAEN = 0;
    setScreenOff();
    for (bi = 0; bi < TITLE_SPR + 8; bi++)
        oamSetVisible(bi << 2, OBJ_HIDE);
    REG_BG1HOFS = 0; // write-twice pairs (shared prev-latch)
    REG_BG1HOFS = 0;
    REG_BG1VOFS = 0;
    REG_BG1VOFS = 0;
    REG_COLDATA = 0xE0;
    REG_TM = 0x15;
    uiClear();
    uiFlush();
    uiMenuClearRows();
    uiMenuRow(15, 9, name);
    uiMenuRow(17, 9, "COMING SOON");
    uiMenuRow(19, 9, "B TO GO BACK");
    setScreenOn();
    mosaicSweep(1, 0); // reveal
    while (1)
    {
        WaitForVBlank();
        pad0 = padsCurrent(0);
        if (!(pad0 & (KEY_B | KEY_START | KEY_A)))
            armed = 1; // the press that opened this page must not close it
        else if (armed)
            break;
    }
    mosaicSweep(0, 0); // pixelate away; the next state snaps mosaic clear
}

// one 32x32 logo block; s16 x so the slide can start offscreen either
// side (OAM x is 9-bit signed - beyond that the block is simply hidden)
static void titleBlock(u16 oid, s16 x, u16 y, u16 gfx, u16 pal, u8 tall)
{
    if (x <= -32 || x > 255)
    {
        oamSetVisible(oid, OBJ_HIDE);
        return;
    }
    oamSet(oid, (u16)x, y, 3, 0, 0, gfx, pal);
    if (tall)
        OAM_TALL(oid);
    oamSetEx(oid, OBJ_LARGE, OBJ_SHOW); // clears x8...
    if (x < 0)
        OAM_X8(oid); // ...so restore it or the block wraps to the right
}

// Super (3 blocks, palette 7) draws OVER Waveracer (4 blocks, palette 6):
// sprite-vs-sprite priority is OAM order, so Super owns the lower ids
static void titleDraw(void)
{
    titleBlock((TITLE_SPR + 0) << 2, ttlSx, TITLE_Y - TITLE_SUP_UP, 136, 7, 0);
    titleBlock((TITLE_SPR + 1) << 2, (s16)(ttlSx + 32), TITLE_Y - TITLE_SUP_UP,
               140, 7, 0);
    titleBlock((TITLE_SPR + 2) << 2, (s16)(ttlSx + 64), TITLE_Y - TITLE_SUP_UP,
               136, 7, 1);
    titleBlock((TITLE_SPR + 3) << 2, ttlWx, TITLE_Y, 0, 6, 0);
    titleBlock((TITLE_SPR + 4) << 2, (s16)(ttlWx + 32), TITLE_Y, 4, 6, 0);
    titleBlock((TITLE_SPR + 5) << 2, (s16)(ttlWx + 64), TITLE_Y, 128, 6, 0);
    titleBlock((TITLE_SPR + 6) << 2, (s16)(ttlWx + 96), TITLE_Y, 132, 6, 0);
}

// rider select (ARCADE and TIME TRIALS): the four riders as tall stacked
// table-2 sprites on OBJ palettes 0-3 - riders are palette-only BY
// DESIGN. Left/right picks (the choice rises, P1 tags it), START/A
// confirms (returns 1), B backs out (returns 0). This screen is also
// where player 2 will join post-jam ("P2 press start" -> 2P VS.).
static u8 riderSelect(void)
{
    u8 armed = 0, sel, i, dirty;
    s16 rx;
    REG_NMITIMEN = 0x81; // NMI + auto-joypad; timer IRQ parked
    REG_HDMAEN = 0;
    setScreenOff();
    for (bi = 0; bi < TITLE_SPR + 8; bi++)
        oamSetVisible(bi << 2, OBJ_HIDE);
    REG_BG1HOFS = 0; // write-twice pairs (shared prev-latch)
    REG_BG1HOFS = 0;
    REG_BG1VOFS = 0;
    REG_BG1VOFS = 0;
    REG_COLDATA = 0xE0;
    REG_TM = 0x15;
    REG_MOSAIC = 0;
    uiClear();
    uiFlush();
    uiMenuClearRows();
    // TEXT ONLY IN ROWS >= 12: rows 4-11 are the sky band the RACE shows
    // (a row-8 title once burned itself into every following race's sky).
    // The riders sit above the text; sprites would win priority anyway.
    uiMenuRow(23, 9, "CHOOSE A RIDER");
    uiMenuRow(25, 10, "PRESS START");
    sel = playerPal;
    dirty = 1;
    menuPrev = 0xFFFF; // held keys must release before they count
    setScreenOn();
    while (1)
    {
        if (dirty) // compose mid-frame, DMA after the wait (the vblank
            uiMenuCompose(menuRows[0], (u16)(4 + 7 * sel), "P1");
        // the riders: two stacked 32x32 sprites each, the pick raised.
        // OAM writes are RAM-side; the ISR DMAs them in the vblank
        for (i = 0; i < 4; i++)
        {
            rx = 24 + 56 * i;
            oy = i == sel ? 128 : 136; // bottom sprite; the pick rides high
            oamSet((u16)((2 + 2 * i) << 2), (u16)rx, oy, 3, 0, 0, 64, i);
            OAM_TALL((2 + 2 * i) << 2);
            oamSetEx((u16)((2 + 2 * i) << 2), OBJ_LARGE, OBJ_SHOW);
            oamSet((u16)((3 + 2 * i) << 2), (u16)rx, (u16)(oy - 32), 3,
                   0, 0, 0, i);
            OAM_TALL((3 + 2 * i) << 2);
            oamSetEx((u16)((3 + 2 * i) << 2), OBJ_LARGE, OBJ_SHOW);
        }
        WaitForVBlank();
        if (dirty)
        {
            dirty = 0;
            uiMenuRowDma(menuRows[0], 21); // P1 tag, under the riders
        }
        pad0 = padsCurrent(0);
        if (!(pad0 & (KEY_START | KEY_A | KEY_B)))
            armed = 1;
        if ((pad0 & KEY_LEFT) && !(menuPrev & KEY_LEFT) && sel)
        {
            sel--;
            dirty = 1;
        }
        if ((pad0 & KEY_RIGHT) && !(menuPrev & KEY_RIGHT) && sel < 3)
        {
            sel++;
            dirty = 1;
        }
        menuPrev = pad0;
        if (armed && (pad0 & (KEY_START | KEY_A)))
        {
            playerPal = sel;
            return 1;
        }
        if (armed && (pad0 & KEY_B))
            return 0;
    }
}

// menuBuf string builder: no printf on tcc, and functions must not
// RETURN char pointers (the bank byte is lost - see CLAUDE.md), so the
// composers append into the global buffer and report the length
static void sbClear(void)
{
    sbLen = 0;
    menuBuf[0] = 0;
}

static void sbCat(char *s) // literal args keep their bank: fine
{
    while (*s && sbLen < 23)
        menuBuf[sbLen++] = *s++;
    menuBuf[sbLen] = 0;
}

static void sbNum(u8 v) // 0..99, no leading zero
{
    if (v >= 10)
        menuBuf[sbLen++] = (char)('0' + v / 10);
    menuBuf[sbLen++] = (char)('0' + v % 10);
    menuBuf[sbLen] = 0;
}

// rider names in palette order (the riders ARE their palettes)
static void sbRider(u8 r)
{
    if (r == 0)
        sbCat("MAGNUS");
    else if (r == 1)
        sbCat("CALLISTA");
    else if (r == 2)
        sbCat("MILO");
    else
        sbCat("DAFYDD");
}

// a rider crosses the line for the last time: next place in finList,
// and in a championship the place's points (9/6/3/1) - paid at once, so
// leaving early costs nobody anything
static void riderFinish(u8 r)
{
    finList[finCount] = r;
    if (champOn)
    {
        racePts[r] = finCount == 0 ? 9 : finCount == 1 ? 6
                     : finCount == 2 ? 3 : 1;
        champPts[r] += racePts[r];
    }
    finCount++;
    ordPrev[0] = 255; // force the table to recompose
}

// the live order: finished riders first (finish order), then the rest by
// race progress - the position counter's own comparison (waypoints
// passed, then nearer to the next one = ahead). Only called once the
// player has finished, so the unfinished are all NPCs.
static void liveOrder(void)
{
    u8 i, j, m, t;
    u16 tp;
    m = 0;
    for (i = 0; i < NPC_COUNT; i++)
        if (!npcDone[i])
        {
            chPr[m] = npcProg[i];
            chDs[m] = npcDist[i];
            chPl[m] = npcPalTab[i];
            m++;
        }
    for (i = 1; i < m; i++) // insertion sort, most advanced first
        for (j = i; j > 0; j--)
        {
            if ((s16)(chPr[j] - chPr[j - 1]) > 0
                || (chPr[j] == chPr[j - 1] && chDs[j] < chDs[j - 1]))
            {
                tp = chPr[j];
                chPr[j] = chPr[j - 1];
                chPr[j - 1] = tp;
                tp = chDs[j];
                chDs[j] = chDs[j - 1];
                chDs[j - 1] = tp;
                t = chPl[j];
                chPl[j] = chPl[j - 1];
                chPl[j - 1] = t;
            }
            else
                break;
        }
    for (i = 0; i < finCount; i++)
        ordTab[i] = finList[i];
    for (i = 0; i < m; i++)
        ordTab[finCount + i] = chPl[i];
}

// the race is left with riders still out there: place them where they
// stand (live order) so every rider has a result and, in a championship,
// their points
static void raceFinish(void)
{
    u8 k, n;
    liveOrder();
    n = finCount;
    for (k = n; k < 4; k++)
        riderFinish(ordTab[k]);
}

// the sky table: recompose (RAM) when the live order changes or the
// prompt appears; the vblank tail DMAs the rows when skyDirty
static void skyUpdate(void)
{
    u8 k, r, chg;
    liveOrder();
    chg = 0;
    for (k = 0; k < 4; k++)
        if (ordTab[k] != ordPrev[k])
            chg = 1;
    if (!chg)
        return;
    for (k = 0; k < 4; k++)
    {
        r = ordTab[k];
        ordPrev[k] = r;
        // "1ST CALLISTA #  9 27": place col 7, name 11, flag 20, race
        // points 22, championship total 25 (the table IS the standings)
        uiSkyCompose(skyRows[k], 7, k == 0 ? "1ST" : k == 1 ? "2ND"
                     : k == 2 ? "3RD" : "4TH");
        sbClear();
        sbRider(r);
        uiSkyAppend(skyRows[k], 11, menuBuf);
        if (k < finCount)
        {
            uiSkyAppend(skyRows[k], 20, "#");
            if (champOn)
            {
                sbClear();
                if (racePts[r] < 10)
                    sbCat(" ");
                sbNum(racePts[r]);
                uiSkyAppend(skyRows[k], 22, menuBuf);
                sbClear();
                if (champPts[r] < 10)
                    sbCat(" ");
                sbNum(champPts[r]);
                uiSkyAppend(skyRows[k], 25, menuBuf);
            }
        }
    }
    skyDirty = 1;
}

// the table leaves BG3 with the race: clouds back, prompt row blank.
// Force blank inside - call between states, before the next screen
static void skyRestore(void)
{
    if (!skyUp)
        return;
    skyUp = 0;
    setScreenOff();
    dmaCopyVram((u8 *)&cloud_map, 0x4400 + WAVE_CLOUD_ROW0 * 32,
                WAVE_CLOUD_TROWS * 64);
    uiSkyCompose(skyRows[4], 0, "");
    uiSkyRowDma(skyRows[4], SKY_GO_ROW);
    setScreenOn();
}

// the intro card's text, in the SKY FONT over the cloud rows (the band's
// console glyphs show the backdrop through their cells - a wrong-colour
// box on every chromatic-sky course): race number row 5, course name
// row 6, the overlay loop flashes PRESS START on row 8 (skyRows[3])
static void introDraw(void)
{
    sbClear();
    sbCat("RACE ");
    sbNum((u8)(champRace + 1));
    sbCat(" OF ");
    sbNum(WAVE_COURSES);
    uiSkyCompose(skyRows[0], (u16)((32 - sbLen) >> 1), menuBuf);
    courseNameTo(champRace, menuBuf);
    sbLen = 0;
    while (menuBuf[sbLen])
        sbLen++;
    uiSkyCompose(skyRows[1], (u16)((32 - sbLen) >> 1), menuBuf);
    uiSkyCompose(skyRows[2], 0, "");
    uiSkyCompose(skyRows[3], 0, "");
    uiSkyCompose(skyRows[4], 0, "");
    skyDirty = 1;
}

// championship standings page (full-screen mode 1, the rider-select
// recipe): the four riders as tall sprites in standings order, the
// leader raised, and a four-line table under them - place, name, this
// race's points, total, P1 tag (names run to 8 chars, so per-column
// text under the sprites collided). Ties break on the latest race, then
// rider order. TEXT ONLY IN ROWS >= 12. Shown ONCE, after the last race
// (PAGE_FINAL: the title names the champion) - every other result, arcade
// included, is the in-race sky table. PAGE_CHAMP (running standings, the
// race number as the title) is kept for the harness / a future use.
#define PAGE_CHAMP 1
#define PAGE_FINAL 2
static void champPage(u8 mode)
{
    u8 armed = 0, i, j, t, a, b;
    s16 rx;
    u16 wait = 0;
    champStage = 3; // "standings up" - only the Lua harness reads this
    for (i = 0; i < 4; i++)
        champOrd[i] = i;
    for (i = 1; i < 4; i++)
        for (j = i; j > 0; j--)
        {
            a = champOrd[j];
            b = champOrd[j - 1];
            if (champPts[a] > champPts[b]
                || (champPts[a] == champPts[b] && racePts[a] > racePts[b]))
            {
                champOrd[j] = b;
                champOrd[j - 1] = a;
            }
            else
                break;
        }
    REG_NMITIMEN = 0x81; // NMI + auto-joypad; timer IRQ parked
    REG_HDMAEN = 0;
    setScreenOff();
    for (bi = 0; bi < TITLE_SPR + 8; bi++)
        oamSetVisible(bi << 2, OBJ_HIDE);
    REG_BG1HOFS = 0; // write-twice pairs (shared prev-latch)
    REG_BG1HOFS = 0;
    REG_BG1VOFS = 0;
    REG_BG1VOFS = 0;
    REG_COLDATA = 0xE0;
    REG_TM = 0x15;
    uiClear();
    uiFlush();
    uiMenuClearRows();
    sbClear();
    if (mode == PAGE_FINAL)
    {
        sbRider(champOrd[0]);
        sbCat(" IS CHAMPION");
    }
    else
    {
        sbCat("RACE ");
        sbNum((u8)(champRace + 1));
        sbCat(" OF ");
        sbNum(WAVE_COURSES);
        sbCat(" RESULTS");
    }
    uiMenuRow(12, (u16)((32 - sbLen) >> 1), menuBuf);
    for (i = 0; i < 4; i++)
    {
        t = champOrd[i];
        rx = 24 + 56 * i;
        // bottom sprite; the leader rides high. Only 28 map rows are
        // visible (224 lines): sprites end at 168, table rows 21-24,
        // prompt row 26 - a row-28 prompt was simply off the bottom
        oy = i == 0 ? 128 : 136;
        oamSet((u16)((2 + 2 * i) << 2), (u16)rx, oy, 3, 0, 0, 64, t);
        OAM_TALL((2 + 2 * i) << 2);
        oamSetEx((u16)((2 + 2 * i) << 2), OBJ_LARGE, OBJ_SHOW);
        oamSet((u16)((3 + 2 * i) << 2), (u16)rx, (u16)(oy - 32), 3, 0, 0,
               0, t);
        OAM_TALL((3 + 2 * i) << 2);
        oamSetEx((u16)((3 + 2 * i) << 2), OBJ_LARGE, OBJ_SHOW);
        // table line: "1ST CALLISTA  +9   9 PTS P1" (cols 3..29)
        uiMenuCompose(menuRows[i], 3, i == 0 ? "1ST" : i == 1 ? "2ND"
                      : i == 2 ? "3RD" : "4TH");
        sbClear();
        sbRider(t);
        uiMenuAppend(menuRows[i], 7, menuBuf);
        sbClear();
        sbCat("+");
        sbNum(racePts[t]);
        uiMenuAppend(menuRows[i], 16, menuBuf);
        sbClear();
        if (champPts[t] < 10)
            sbCat(" "); // right-align the total
        sbNum(champPts[t]);
        sbCat(" PTS");
        uiMenuAppend(menuRows[i], 20, menuBuf);
        if (t == playerPal)
            uiMenuAppend(menuRows[i], 28, "P1");
    }
    for (i = 0; i < 4; i++)
        uiMenuRowDma(menuRows[i], (u16)(21 + i)); // force blank: safe
    uiMenuRow(26, 10, "PRESS START");
    setScreenOn();
    mosaicSweep(1, 0); // reveal
    while (1)
    {
        WaitForVBlank();
        pad0 = padsCurrent(0);
        if (!(pad0 & (KEY_START | KEY_A)))
            armed = 1; // the press that got here must not skip the page
        else if (armed)
            break;
        wait++;
#if CHAMP_AUTO
        if (wait > 240)
            break;
#endif
    }
    mosaicSweep(0, 0); // pixelate away; the next state snaps mosaic clear
}

static void ovlMenuDraw(void)
{
    uiPrint(9, 1, menuSel == 0 ? ">CHAMPIONSHIP" : " CHAMPIONSHIP");
    uiPrint(9, 2, menuSel == 1 ? ">TIME TRIALS" : " TIME TRIALS");
    uiPrint(9, 3, menuSel == 2 ? ">ARCADE" : " ARCADE");
}

//---------------------------------------------------------------------------------
int main(void)
{
    camTabsInitHeaders();
    courseSel = 0; // BSS; the menu cursor survives between races after this
    menuSel = 0;   // ditto every game-flow variable
    raceTT = 0;
    playerPal = 0;
    menuGo = 0;
    raceMode = RM_RACE;
    attract = 0;
    ovlPrev = 0;
    champOn = 0;
    raceState = 0; // BSS: a garbage 2 would show a results page at boot

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
    // (OBJ palettes 0-3 riders + 5 buoys come from courseLoad, per course)
    dmaCopyCGram((u8 *)&lamp_pal, 192, 32); // start-tree lamps: palette 4
    dmaCopyCGram((u8 *)&title_pal, 224, 32);  // WAVERACER: OBJ palette 6
    dmaCopyCGram((u8 *)&title_pal2, 240, 32); // Super:     OBJ palette 7
    // stacked tall racers: name table 2 right after the sheet
    dmaCopyVram((u8 *)&tall_tiles, 0x7000, WAVE_TALL_SHEET);
    setPaletteColor(0, RGB8(16, 60, 150)); // boot zenith; courseLoad owns it

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

    while (1) // game flow: TITLE/MENU (attract race behind) -> mode -> back
    {
#if AUTOPILOT
    // harness build: the pre-flow hands-free loop (tickshot/menuinput and
    // friends assume boot -> course select -> race with the chaser driving)
    courseSelect();
    courseLoad(courseSel);
    attract = 1;
    raceTT = 0;
    playerPal = 0;
    raceMode = RM_RACE;
    raceInit();
#else
    if (menuGo && menuSel == 0)
    {
        // CHAMPIONSHIP: rider select, then every course in order (B at
        // rider select backs out to the menu via the attract branch)
        menuGo = 0;
        mosaicSweep(0, 1);
        REG_HDMAEN = 0;
        titleBg3(0);
        raceTT = 0;
        if (!riderSelect())
            raceMode = RM_MENU;
        else
        {
            champOn = 1;
            champRace = 0;
            champStage = 0;
            for (bi = 0; bi < 4; bi++)
            {
                champPts[bi] = 0;
                racePts[bi] = 0;
            }
        }
    }
    if (champOn)
    {
        if (champStage == 1)
        {
            // the intro card is over: the same course again, for real.
            // courseLoad already ran for the cruise; raceInit owns every
            // race variable (replay verified bit-identical), so a second
            // init is a clean grid start
            mosaicSweep(0, 1);
            REG_HDMAEN = 0;
            skyRestore(); // the card's sky text: clouds back
            uiClear();    // (and the band, or the HUD inherits it)
            attract = 0;
            raceMode = RM_RACE;
            raceInit();
            champStage = 2;
        }
        else
        {
            if (champStage == 2) // a race just ended
            {
                if (raceState != 2)
                    champOn = 0; // quit from pause: abandon -> title
                else
                {
                    // the sky table carried this race's points and the
                    // totals; only the LAST race gets a page (champion)
                    mosaicSweep(0, 1);
                    REG_HDMAEN = 0;
                    skyRestore(); // the results table off BG3
                    if (champRace + 1 >= WAVE_COURSES)
                    {
                        champPage(PAGE_FINAL);
                        champOn = 0; // -> title
                    }
                    else
                        champRace++; // -> the next intro below
                }
            }
            if (champOn)
            {
                // intro card: the chaser cruises the racing line at a
                // constant speed (the attract machinery with no racers
                // drawn, clouds kept) under a band overlay - race number,
                // course name, flashing PRESS START; it advances after
                // one full lap, or on START
                REG_HDMAEN = 0;
                courseLoad(champRace);
                attract = 1;
                raceMode = RM_INTRO;
                ovlInit = 0;
                ovlFlash = 2;
                raceInit();
                skyUp = 1;     // the card's text rides the sky rows
                raceState = 1; // no countdown, no finish: it just cruises
                ltState = 3;
                ttlWait = 0; // park the title slide: nothing to draw
                ttlWx = TITLE_WR_X;
                ttlSx = TITLE_SUP_X;
                champStage = 1;
            }
        }
    }
    if (champOn)
        ; // a championship state is set up above
    else if (menuGo && menuSel != 0)
    {
        // ARCADE (a single race vs the NPCs) and TIME TRIALS (solo,
        // endless, best-lap) share the flow: rider select, course select,
        // race. B at rider select backs out to the menu (the continue
        // falls into the attract branch below).
        menuGo = 0;
        mosaicSweep(0, 1);
        REG_HDMAEN = 0; // BEFORE any loader: waveRawLoad borrows the PPU
                        // multiplier and ch0 keeps repainting CGRAM
        titleBg3(0);
        raceTT = menuSel == 1;
        if (!riderSelect())
        {
            raceMode = RM_MENU;
            continue;
        }
        attract = 0;
        raceMode = RM_RACE;
        courseSelect();
        courseLoad(courseSel);
        raceInit();
    }
    else
    {
        // the attract loop: SUNNY ISLAND forever, chaser driving, the
        // title or menu overlay in the band. Each finished race lands
        // back here (results auto-advance) and starts the next.
        attract = 1;
        raceTT = 0;
        playerPal = 0; // the demo rides rider 1
        if (raceMode == RM_RACE)
            raceMode = RM_TITLE; // first boot / back from a race
        REG_HDMAEN = 0; // a quit race's channels may still be streaming
        skyRestore();   // an ARCADE race's results table (its only page)
        courseLoad(0);
        titleBg3(1);
        ovlInit = 0;
        ovlFlash = 2; // neither 0 nor 1: force the first flash draw
        raceInit();
        raceState = 1; // attract: no countdown, no light tree - and no
        ltState = 3;   // finish either (see the lap check): it just runs
        // the title logo slides in: WAVERACER (4 blocks, palette 6) from
        // the right to centre, Super (3 blocks, palette 7, drawn over it)
        // from the left to 8px left of WAVERACER's edge. The overlay loop
        // eases both toward their marks and draws them (titleDraw)
        ttlWx = 400;  // virtual left edges, offscreen both sides
        ttlSx = -120;
        ttlWait = 55; // ~3s of open sea before the words come on
        titleDraw();
    }
#endif

    while (!raceDone)
    {
        // the loop takes a variable 3-4 vblanks (see CLAUDE.md), so ALL
        // race timing accumulates real frames, never loop ticks
        loopFrames = snes_vblank_count - loopVbl;
        loopVbl = snes_vblank_count;
        pad0 = padsCurrent(0);

        // ---- overlays: title / main menu over the attract race, pause
        // over a real one. These read the RAW pad; the chaser below only
        // ORs its own bits in for the attract driver ----
        if (raceMode != RM_RACE)
        {
            if (!ovlInit)
            {
                ovlInit = 1;
                uiClear();
                if (raceMode == RM_MENU)
                    ovlMenuDraw();
                if (raceMode == RM_INTRO)
                    introDraw();
            }
            // title slide-in, eased (fast entry, soft landing), after a
            // ~3s hold on the empty sky
            if (ttlWait)
                ttlWait--;
            else if (ttlWx > TITLE_WR_X)
            {
                ttlWx -= ((ttlWx - TITLE_WR_X) >> 3) + 2;
                if (ttlWx < TITLE_WR_X)
                    ttlWx = TITLE_WR_X;
                titleDraw();
            }
            if (!ttlWait && ttlSx < TITLE_SUP_X)
            {
                ttlSx += ((TITLE_SUP_X - ttlSx) >> 3) + 2;
                if (ttlSx > TITLE_SUP_X)
                    ttlSx = TITLE_SUP_X;
                titleDraw();
            }
            if (raceMode == RM_INTRO)
            {
                if (((u8)(snes_vblank_count >> 5) & 1) != ovlFlash)
                {
                    ovlFlash = (u8)(snes_vblank_count >> 5) & 1;
                    uiSkyCompose(skyRows[3], 10, ovlFlash ? "PRESS START" : "");
                    skyDirty = 1;
                }
                // lapCount seeds 255, 0 at the rolling start, 1 after
                // the lap - the flyover shows the whole course once
                if ((lapCount >= 1 && lapCount != 255)
                    || ((pad0 & (KEY_START | KEY_A))
                        && !(ovlPrev & (KEY_START | KEY_A))))
                    raceDone = 1; // -> the real race (main's flow)
            }
            else if (raceMode == RM_TITLE)
            {
                if (((u8)(snes_vblank_count >> 5) & 1) != ovlFlash)
                {
                    ovlFlash = (u8)(snes_vblank_count >> 5) & 1;
                    uiPrint(10, 2, ovlFlash ? "PRESS START" : "           ");
                }
                if ((pad0 & (KEY_START | KEY_A))
                    && !(ovlPrev & (KEY_START | KEY_A)))
                {
                    raceMode = RM_MENU;
                    uiClear();
                    ovlMenuDraw();
                }
            }
            else // RM_MENU
            {
                if ((pad0 & KEY_UP) && !(ovlPrev & KEY_UP) && menuSel)
                {
                    menuSel--;
                    ovlMenuDraw();
                }
                if ((pad0 & KEY_DOWN) && !(ovlPrev & KEY_DOWN)
                    && menuSel < 2)
                {
                    menuSel++;
                    ovlMenuDraw();
                }
                if ((pad0 & KEY_B) && !(ovlPrev & KEY_B))
                {
                    raceMode = RM_TITLE;
                    ovlFlash = 2;
                    uiClear();
                }
                if ((pad0 & (KEY_START | KEY_A))
                    && !(ovlPrev & (KEY_START | KEY_A)))
                {
                    menuGo = 1;
                    raceDone = 1;
                }
            }
        }
        else if (!attract && raceState == 1 && (pad0 & KEY_START)
                 && !(ovlPrev & KEY_START))
        {
            // ---- pause: physics/clock/wave phase all freeze because
            // this loop does. HDMA replays the last tables, but the
            // ISR's OAM DMA clobbers ch7's registers every frame, so
            // waveHdma is re-kicked per frame like the main loop's tail
            uiClear(); // the pause menu owns the band; HUD redraws after
            uiPrint(13, 1, "PAUSED");
            uiPrint(6, 3, "START RESUME     B QUIT");
            ovlPrev = pad0;
            while (1)
            {
                WaitForVBlank();
                uiFlush();
                waveHdma(phase, camBufOff);
                pad0 = padsCurrent(0);
                if ((pad0 & KEY_START) && !(ovlPrev & KEY_START))
                    break;
                if ((pad0 & KEY_B) && !(ovlPrev & KEY_B))
                {
                    raceDone = 1; // quit to the title
                    break;
                }
                ovlPrev = pad0;
            }
            uiClear();     // wipe the pause text...
            hudInit = 0;   // ...and force the HUD to redraw everything
            pwDrawn = 255;
            hRank = 255;
            hLapD = 255;
            hSpd = 255;
            hMinD = 255;
            hSecU = 255;
        }
        ovlPrev = pad0;

        if (attract || (CHAMP_AUTO && raceMode == RM_RACE))
        {
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
            wpdx = apc < 0 ? -apc : apc;
            if (wpdx > (apd >> 3))
            {
                if (apc < 0)
                    pad0 |= KEY_RIGHT;
                else
                    pad0 |= KEY_LEFT;
                // small corrections steer at QUARTER authority (apFine,
                // consumed by the steering block): one full-rate loop tick
                // is 2 binary degrees, which pans the far texture several
                // pixels in a single step - the attract "ground jump". A
                // human feathers; the chaser must too. Real corners
                // (error beyond half the forward component) keep full rate
                if (wpdx < (apd >> 1))
                    apFine = 1;
            }
        }
        // full throttle on the straights; coast into corners sharper than
        // ~45 deg — but never stall (turn authority needs speed)
        apu = apc < 0 ? -apc : apc;
        if ((apd > 0 && apu < apd) || (vAlong < 600 && vAlong > -600))
            pad0 |= KEY_B;
        // wedged on a rope/shore (deterministically reachable: the traces
        // ground at x=1407 forever): barely moving for ~2s while racing ->
        // let go of the throttle and REVERSE out for ~3s, still steering,
        // then drive on. A real pad never sets this.
        if (raceState == 1 && vAlong < 300 && vAlong > -300)
        {
            if (apStuck < 90)
                apStuck++;
        }
        else if (apStuck < 40)
            apStuck = 0;
        if (apStuck >= 40)
        {
            pad0 &= (u16)~KEY_B;
            pad0 |= KEY_Y;
            if (apStuck >= 90)
            {
                apStuck = 0;
#if CHAMP_AUTO
                // harness only: a reverse-out that did not free the ski
                // (Sunset Cove pockets it between two walls at x~1250/
                // 1407 and it cycles forever) - drop the camera pivot on
                // the next waypoint so the six-race sweep completes.
                // The ski sits skiDist ahead of the pivot; that is
                // inside the waypoint's 200-unit capture radius
                if (champOn && !attract)
                {
                    camPX = pathX[nextWp];
                    camPY = pathY[nextWp];
                }
#endif
            }
        }
#else
            pad0 |= KEY_B;
#endif
        }

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
                // the finish: the sky table tracks the field (riders
                // glide to rest as they cross); after 5s PRESS START is
                // offered and the race waits for it - START ends it with
                // the unfinished placed where they stand
                skyUpdate();
                finFr += loopFrames;
                if (finFr >= 300 && !skyGo)
                {
                    skyGo = 1;
                    uiSkyCompose(skyRows[4], 10, "PRESS START");
                    skyDirty = 1;
                }
                if (!(pad0 & KEY_START))
                    startHeld = 0;
                if ((skyGo && (pad0 & KEY_START) && !startHeld)
                    || (CHAMP_AUTO && finFr >= 300))
                {
                    raceFinish();
                    raceDone = 1;
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
        if (apFine) // the chaser's feathered correction (never set by a pad)
        {
            apFine = 0;
            turnRate >>= 2;
        }
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
        if (raceMode == RM_INTRO)
        {
            // flyover: a constant speed along the heading, set outright
            // every tick - no thrust ramp, no drag, no coasting, and no
            // dependence on being in the water (the ski is not drawn)
            skiVX = 0;
            skiVY = 0;
            thrF8 = INTRO_THRUST;
            for (bi = 0; bi < INTRO_PASSES; bi++)
                skiThrustF();
        }
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
            if (raceMode != RM_INTRO) // the flyover set its speed above
            {
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
                if (raceState == 2) // finished: pull up short of the field
                {                   // so later finishers roll past, in view
                    skiVX -= skiVX >> 2;
                    skiVY -= skiVY >> 2;
                }
            }
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
            nextWp++;
            if (nextWp >= pathCount)
                nextWp = 0;
            pProg++;
        }
        // ---- the start/finish line: laps count on a TRUE crossing of
        // the chequered strip at path[0] - the along-track dot (vs the
        // opening heading) flips sign, as the gates do - not on entering
        // waypoint 0's 200-unit radius, which read ~10 yards early. Armed
        // when seen behind the line within 400 units; a lap needs
        // (almost) a full loop of waypoints since the last one, so
        // backing over the line cannot farm laps. lapCount seeds 255
        // (-1): the grid sits behind the line, so the first crossing is
        // the rolling start (-> 0, "LAP 1/3").
        lnDx = (s16)((skiWX - pathX[0]) & 4095);
        if (lnDx > 2048)
            lnDx -= 4096;
        lnDy = (s16)((skiWY - pathY[0]) & 4095);
        if (lnDy > 2048)
            lnDy -= 4096;
        gLat = (lnDx < 0 ? -lnDx : lnDx) + (lnDy < 0 ? -lnDy : lnDy);
        if (gLat < 400)
        {
            gAlong = (lnDx >> 3) * startNx + (lnDy >> 3) * startNy;
            if (gAlong < 0)
                lineArm = 1;
            else if (lineArm)
            {
                lineArm = 0;
                if (lapCount == 255
                    || (u16)(pProg - lapBase) >= (u16)(pathCount - 1))
                {
                lapBase = pProg;
                lapCount++;
                lastLap = lapTicks;
                lapTicks = 0;
                lastLapSec = (u8)(lapFr / 60); // division: once per lap
                lastLapTenth = (u8)((lapFr % 60) / 6);
                lapFr = 0;
                if (raceTT && lapCount >= 1
                    && (lastLapSec < bestSec
                        || (lastLapSec == bestSec
                            && lastLapTenth < bestTenth)))
                {
                    bestSec = lastLapSec;
                    bestTenth = lastLapTenth;
                    hRank = (u8)(bestSec + 1); // force the BEST cell redraw
                }
                if (!attract && !raceTT
                    && raceState == 1 && lapCount >= RACE_LAPS)
                {
                    raceState = 2; // chequered flag
                    finPos = racePos;
                    riderFinish(playerPal);
                    skyUp = 1; // the results table rides the sky from here
                    uiSkyCompose(skyRows[4], 0, "");
                }
                }
            }
        }
        else
            lineArm = 0;
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
            // benefit of the doubt: buoys are not solid any more, so a
            // pass THROUGH one (within a collision cell, 32 world units:
            // |gLat| <= 128 at the normals' 64 scale) counts as correct
            if ((gLat >= -128 && gLat <= 128)
                || (gLat >= 0) == (gateLeft[nextGate] != 0))
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
        if (raceState && !raceTT && raceMode != RM_INTRO) // frozen on the
            // grid until GO; TT = solo; the flyover has no racers at all
            for (bi = 0; bi < NPC_COUNT; bi++)
            {
                if (npcDone[bi] && npcSpd[bi] < 64) // finished and glided
                {                                     // to rest: parked
                    posAcc++; // ahead of anyone still racing
                    continue;
                }
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
                // the start/finish line, as for the player: a TRUE
                // crossing books the lap (255 -> 0 at the grid); the last
                // one finishes the rider - it then glides to a stop
                if (!attract)
                {
                    lnDx = (s16)((npcX[bi] - pathX[0]) & 4095);
                    if (lnDx > 2048)
                        lnDx -= 4096;
                    lnDy = (s16)((npcY[bi] - pathY[0]) & 4095);
                    if (lnDy > 2048)
                        lnDy -= 4096;
                    gLat = (lnDx < 0 ? -lnDx : lnDx)
                           + (lnDy < 0 ? -lnDy : lnDy);
                    if (gLat < 400)
                    {
                        gAlong = (lnDx >> 3) * startNx + (lnDy >> 3) * startNy;
                        if (gAlong < 0)
                            npcArm[bi] = 1;
                        else if (npcArm[bi])
                        {
                            npcArm[bi] = 0;
                            if (npcLap[bi] == 255
                                || (u16)(npcProg[bi] - npcLapBase[bi])
                                       >= (u16)(pathCount - 1))
                            {
                                npcLapBase[bi] = npcProg[bi];
                                npcLap[bi]++;
                                if (npcLap[bi] >= RACE_LAPS
                                    && npcLap[bi] < 250 && !npcDone[bi])
                                {
                                    npcDone[bi] = 1;
                                    riderFinish(npcPalTab[bi]);
                                }
                            }
                        }
                    }
                    else
                        npcArm[bi] = 0;
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
                // attract: recycled traffic. A racer overtaken by more
                // than 3 waypoints respawns 4 segments ahead (beyond the
                // sprite draw distance, so no pop) with its progress
                // pinned ahead of the player: the rubber-band tiers then
                // slow it down and the demo overtakes it again, forever
                if (attract && (s16)(pProg - npcProg[bi]) > 3)
                {
                    apu = nextWp + 4;
                    if (apu >= pathCount)
                        apu -= pathCount;
                    npcX[bi] = pathX[apu];
                    npcY[bi] = pathY[apu];
                    npcWp[bi] = (u8)(apu + 1 >= pathCount ? 0 : apu + 1);
                    npcProg[bi] = pProg + 4;
                    npcFX[bi] = 0;
                    npcFY[bi] = 0;
                    npcTheta[bi] = startTheta; // the brain corrects it
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
                if (npcDone[bi])
                    spdTgt = 0; // finished: ease off and glide to rest
                else if (lapCount < npcFade[bi])
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
                if (npcDone[bi]) // a long glide (~220 units, the player
                    npcSpd[bi] -= npcSpd[bi] >> 4; // pulls up in ~50)
                else
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
                    if (npcDone[bi])
                        break; // parked riders are never shoved
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
        // the speed term MUST multiply signed: wvSteps is unsigned, and a
        // negative vAlong made tcc's product unsigned with a LOGICAL >> 4
        // (~ +4000 instead of -56) - the phase then leapt half a cycle
        // every tick in reverse: the "camera shakes when backing up" bug,
        // latent since the per-course wave profiles made wvSteps a variable
        phaseAcc = (phaseAcc + WAVE_BASE_ROLL
                    + (u16)((s16)((vAlong >> 4) * (s16)wvSteps) >> 4))
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
        oamSet(0, SKI_X, (u16)sprTop, 3, skiFlip, 0, skiLean ? 68 : 64,
               playerPal);
        OAM_TALL(0);
        oamSet(4, SKI_X, (u16)(sprTop - 32), 3, skiFlip, 0,
               skiLean ? 4 : 0, playerPal);
        OAM_TALL(4);
        if (raceMode == RM_INTRO) // flyover: the course alone, no racers
        {
            oamSetVisible(0, OBJ_HIDE);
            oamSetVisible(4, OBJ_HIDE);
        }

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

        if (!raceTT && raceMode != RM_INTRO) // TT / flyover: no NPCs at
                                             // all (hidden at raceInit)
        {
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
                        (NPC_SPR + (ns << 1) + 1) << 2, npcPalTab[bi]);
            }
            else
            {
                oamSetVisible((NPC_SPR + (ns << 1)) << 2, OBJ_HIDE);
                oamSetVisible((NPC_SPR + (ns << 1) + 1) << 2, OBJ_HIDE);
            }
        }
        } // !raceTT
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
            if (!sprInt[bi] || sprY > 223 || sprY < (s16)waterRow
                || raceMode == RM_INTRO) // no ski, no wake
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
            // CRT overscan, everything redrawn only on change. The
            // title/menu overlays own the band during attract ----
            if (raceMode == RM_RACE)
            {
            if (!hudInit)
            {
                hudInit = 1;
                uiHudSmall(1, 1, HUD_PAL_TITLE, "TIME");
                // TT: BEST lap (the B glyph took unused G's slot -
                // the font's VRAM window is exactly full) and NO lap
                // counter: laps are endless, the time is the point
                uiHudSmall(9, 1, HUD_PAL_TITLE, raceTT ? "BEST" : "RANK");
                if (!raceTT)
                    uiHudSmall(14, 1, HUD_PAL_TITLE, "LAP");
                uiHudSmall(18, 1, HUD_PAL_TITLE, "SPEED");
                uiHudSmall(25, 1, HUD_PAL_TITLE, "POWER");
                uiHudSmall(20, 3, HUD_PAL_BOT, "KM/H");
                uiHudBig(1, "0'00\"00");
                if (!raceTT)
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
            if (raceTT)
            {
                // BEST cell: M'SS"T, redrawn when a better lap lands
                // (hRank is its cache; 255 = nothing set yet)
                if (bestSec != 255 && hRank != bestSec)
                {
                    hRank = bestSec;
                    pwBuf[0] = (char)('0' + bestSec / 60);
                    pwBuf[1] = 0x27; /* apostrophe */
                    bq = bestSec % 60;
                    pwBuf[2] = (char)('0' + bq / 10);
                    pwBuf[3] = (char)('0' + bq % 10);
                    pwBuf[4] = '"';
                    pwBuf[5] = (char)('0' + bestTenth);
                    pwBuf[6] = 0;
                    uiHudBig(9, pwBuf);
                }
            }
            else
            {
            // FINISH! banner over the rank/lap cells (the countdown and
            // GO live on the start-light tree now)
            bq = 0;
            if (raceState == 2)
                bq = 5; // FINISH! for the 5s until the race ends itself
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
            } // !raceTT
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
            } // raceMode == RM_RACE
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
        if (raceMode == RM_RACE && !skyUp)
            REG_BG3HOFS = (u8)(camTheta16 >> 6);
        else
            REG_BG3HOFS = 0; // the BG3 title / results table sit still
        REG_BG3HOFS = 0;
        if (skyDirty) // the results table rows (composed mid-frame)
        {
            skyDirty = 0;
            for (bi = 0; bi < WAVE_CLOUD_TROWS; bi++)
                uiSkyRowDma(skyRows[bi], (u16)(WAVE_CLOUD_ROW0 + bi));
            uiSkyRowDma(skyRows[4], SKY_GO_ROW);
        }
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
