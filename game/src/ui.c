#include "ui.h"
#include "wavedata.h"

extern char sky_gfx, sky_pal2; // baked mode-1 sky band (wavetables.asm)
extern char cloud_gfx, cloud_map; // BG3 cloud overlay strip
extern char hud_gfx, hud_pal;     // gradient HUD font + its CGRAM ramps

#define REG_BG3VOFS (*(vuint8 *)0x2112)

// The mode-1 -> mode-7 switch rides a scanline IRQ now (camera.asm
// irqSwitch, vector in hdr.asm), which freed HDMA channel 0 for the sand
// distance-fade: a baked table of hold-run entries (mode 3 -> $2121)
// that rewrites CGRAM entry 8 per scanline band, ignoring wave phase.
// The table pointer (csFade) is per-course, set by courseGeom.

// map words: font tile = 256 + ascii - 32 (the font sits at 0x5000, ids
// 256+ from the 0x4000 BG1 char base), palette row 1 (bits 10-12)
#define UI_ATTR 0x0500

u16 uiMap[UI_COLS * UI_ROWS];

void uiInit(void)
{
    u16 i;

    // Font + palette via the library (both respect the address overrides;
    // only the console's own MAP path is $0800-hardcoded, which we bypass).
    // BG1/BG3 data lives in the 0x4000 bank (UI map 0x4000, BG3 cloud map
    // 0x4400, HUD font 0x4800, cloud chars after) so 0x7000-0x7FFF can be
    // OBJ name table 2 (the tall racers). Font stays at 0x5000 = ids 256+,
    // sky rows stay at 0x5C00 = ids 448+ (both from the 0x4000 char base).
    bgSetGfxPtr(0, 0x4000);
    bgSetMapPtr(0, 0x4000, SC_32x32);
    consoleSetTextGfxPtr(0x5000);
    consoleSetTextMapPtr(0x4000);
    consoleInitDefaultText(1); // palette row 1 = CGRAM 16-31 (see colour map)
    bgSetGfxPtr(0, 0x4000);    // re-point: the console init re-set the base
    // Tile 0 (the space) becomes SOLID index 15 = CGRAM 31, the per-course
    // "HUD backdrop" (= the sky zenith, set by courseLoad). Backdrop 0 is
    // no longer the zenith: it is horizon - strip add, so the mode-7 safe
    // strip continues a chromatic sky band exactly; every blank BG1 cell
    // (HUD band, menu) must therefore paint its own background.
    for (i = 0; i < 16; i++)
        uiMap[i] = 0xFFFF; // all four bitplanes set = colour 15
    dmaCopyVram((u8 *)uiMap, 0x5000, 32);

    // mode-1 sky band: gradient tiles (chars WAVE_SKY_CHAR0+, VRAM above
    // the font) on palette row 2 (CGRAM 32-47), one char per map row from
    // tile row 3 down to the mode-switch line. Uses uiMap as scratch for
    // the row DMAs, then the text init below repaints it.
    dmaCopyVram((u8 *)&sky_gfx, 0x4000 + WAVE_SKY_CHAR0 * 16,
                WAVE_SKY_ROWS * 32);
    dmaCopyCGram((u8 *)&sky_pal2, 31, 34); // 31 HUD backdrop + band anchors
    // WAVE_SKY_ROWS includes the extra bottom row for the BG scroll
    // off-by-one (screen line N samples map line N+1); the bake authors
    // the tiles map-line indexed so every visible line lands exactly
    for (i = 0; i < WAVE_SKY_ROWS; i++)
    {
        u16 c;
        for (c = 0; c < 32; c++)
            uiMap[c] = 0x0800 | (WAVE_SKY_CHAR0 + i); // palette row 2
        dmaCopyVram((u8 *)uiMap, 0x4000 + (UI_ROWS + i) * 32, 64);
    }

    // BG3 cloud overlay (mode-1 sky rows only; the TM table keeps it off
    // the text band). BG3, NOT BG2: EXTBG stays on all frame for the
    // sea's priority layer, and on REAL HARDWARE it mangles BG2's fetches
    // outside mode 7 - emulators only model EXTBG in mode 7, so they
    // cannot show the jank. 2bpp chars after the HUD font (ids from the
    // 0x4000 base), map at 0x4400, palette group 7 = CGRAM 28-31 (spare
    // entries of the UI text row). Map words carry the priority bit: the
    // band's mode byte is 0x09 (BG3 priority), so clouds draw over the
    // BG1 gradient.
    bgSetGfxPtr(2, 0x4000);
    bgSetMapPtr(2, 0x4400, SC_32x32);
    dmaCopyVram((u8 *)&cloud_gfx, 0x4000 + WAVE_CLOUD_CHAR0 * 8,
                WAVE_CLOUD_CHARS * 16);
    for (i = 0; i < UI_COLS * UI_ROWS; i++)
        uiMap[i] = 0x1C00 | WAVE_CLOUD_CHAR0; // the set's char 0 = blank
    for (i = 0; i < 16; i++) // clear the whole 32x32 map...
        dmaCopyVram((u8 *)uiMap, 0x4400 + i * 64, 128);
    dmaCopyVram((u8 *)&cloud_map, 0x4400 + WAVE_CLOUD_ROW0 * 32,
                WAVE_CLOUD_TROWS * 64); // ...then drop the strip in
    setPaletteColor(29, RGB8(250, 250, 250)); // cloud white
    setPaletteColor(30, WAVE_CLOUD_SHADE);    // cloud underside shade

    // HUD gradient font: 3 blocks of glyphs (single height / double-height
    // tops / bottoms) between the UI map and the cloud chars
    dmaCopyVram((u8 *)&hud_gfx, 0x4000 + WAVE_HUD_CHAR0 * 16,
                WAVE_HUD_GLYPHS * 3 * 32);
    dmaCopyCGram((u8 *)&hud_pal, 64, 96);
    // BG scroll off-by-one (screen line N samples map line N+1): VOFS -1
    // makes screen row == map row exactly for the strip
    REG_BG3VOFS = 0xFF;
    REG_BG3VOFS = 0x03;

    for (i = 0; i < UI_COLS * UI_ROWS; i++)
        uiMap[i] = UI_ATTR; // tile 0 = space

    uiFlush();
}

void uiHdma(void)
{
    REG_DMAP0 = 0x03; // mode 3: p,p,p+1,p+1 = CGADD x2 then CGDATA lo/hi
    REG_BBAD0 = 0x21; // $2121 CGADD: one palette entry per table entry
    REG_A1T0LH = csFade.mem.c.addr; // per-course table (courseGeom sets it)
    REG_A1B0 = csFade.mem.c.bank;
}

// glyph order must match the bake's HUD_GLYPHS
static u16 hudIdx(char c)
{
    char *g = "0123456789'\"/!ADEFBHIKLMNOPRSTW*."; // must match the bake
    u16 i = 0;
    while (g[i])
    {
        if (g[i] == c)
            return i;
        i++;
    }
    return 0;
}

void uiHudSmall(u16 x, u16 y, u16 pal, char *s)
{
    u16 *p = uiMap + y * UI_COLS + x;
    while (*s)
    {
        // blank = the FONT's space (UI_ATTR): map word 0 would be char 0
        // = the UI map itself read as tile data since the 0x4000 rebase
        *p++ = *s == ' ' ? UI_ATTR : pal | (WAVE_HUD_CHAR0 + hudIdx(*s));
        s++;
    }
}

void uiHudBig(u16 x, char *s)
{
    u16 *t = uiMap + 2 * UI_COLS + x;
    u16 *b = uiMap + 3 * UI_COLS + x;
    u16 i;
    while (*s)
    {
        if (*s == ' ')
        {
            *t++ = UI_ATTR; // font space, see uiHudSmall
            *b++ = UI_ATTR;
        }
        else
        {
            i = hudIdx(*s);
            *t++ = HUD_PAL_TOP | (WAVE_HUD_CHAR0 + WAVE_HUD_GLYPHS + i);
            *b++ = HUD_PAL_BOT | (WAVE_HUD_CHAR0 + 2 * WAVE_HUD_GLYPHS + i);
        }
        s++;
    }
}

void uiHudBigDigit(u16 x, u16 d) // digits are glyphs 0-9: no lookup
{
    uiMap[2 * UI_COLS + x] =
        HUD_PAL_TOP | (WAVE_HUD_CHAR0 + WAVE_HUD_GLYPHS + d);
    uiMap[3 * UI_COLS + x] =
        HUD_PAL_BOT | (WAVE_HUD_CHAR0 + 2 * WAVE_HUD_GLYPHS + d);
}

void uiHudBigClear(u16 x, u16 w)
{
    u16 *t = uiMap + 2 * UI_COLS + x;
    u16 *b = uiMap + 3 * UI_COLS + x;
    while (w--)
    {
        *t++ = UI_ATTR; // font space, see uiHudSmall
        *b++ = UI_ATTR;
    }
}

void uiClear(void) // every band cell -> the font space
{
    u16 i;
    for (i = 0; i < UI_COLS * UI_ROWS; i++)
        uiMap[i] = UI_ATTR;
}

// Menu text: console-font words straight into BG1 map rows BELOW the band
// and the sky tiles (rows UI_ROWS+WAVE_SKY_ROWS..31). The race never shows
// these rows - the scanline IRQ has switched to mode 7 by then - so the
// menu and the race share the map with zero cleanup. VRAM writes: call
// under force blank or in vblank.
static u16 menuRow[UI_COLS];

// compose one 32-entry map row into dst (any time - it is only RAM) ...
void uiMenuCompose(u16 *dst, u16 x, char *s)
{
    u16 i;
    for (i = 0; i < UI_COLS; i++)
        dst[i] = UI_ATTR;
    while (*s)
        dst[x++] = UI_ATTR | (u16)(*s++ - 32);
}

// ... add a second (third...) string to a composed row - the standings
// page puts four columns of text on one map row ...
void uiMenuAppend(u16 *dst, u16 x, char *s)
{
    while (*s)
        dst[x++] = UI_ATTR | (u16)(*s++ - 32);
}

// ... and push a composed row to VRAM: vblank or force blank ONLY. Kept
// separate because composing under tcc costs ~45 scanlines per row: the
// menu cursor redraw used to compose+DMA after WaitForVBlank and its DMAs
// landed at scanline 12/57 of the NEXT frame - silently dropped by the PPU.
void uiMenuRowDma(u16 *src, u16 row)
{
    dmaCopyVram((u8 *)src, 0x4000 + row * 32, 64);
}

void uiMenuRow(u16 row, u16 x, char *s)
{
    uiMenuCompose(menuRow, x, s);
    uiMenuRowDma(menuRow, row);
}

void uiMenuClearRows(void)
{
    u16 r;
    for (r = 0; r < UI_COLS; r++)
        menuRow[r] = UI_ATTR;
    for (r = UI_ROWS + WAVE_SKY_ROWS; r < 32; r++)
        dmaCopyVram((u8 *)menuRow, 0x4000 + r * 32, 64);
}

void uiPrint(u16 x, u16 y, char *s)
{
    u16 *p = uiMap + y * UI_COLS + x;
    while (*s)
        *p++ = UI_ATTR | (u16)(*s++ - 32);
}

void uiPrintNum(u16 x, u16 y, u16 val, u16 width)
{
    u16 *p = uiMap + y * UI_COLS + x + width;
    u16 digits = 0;
    while (digits < width)
    {
        *--p = UI_ATTR | (u16)('0' - 32 + (val % 10));
        val /= 10;
        digits++;
        if (val == 0)
            break;
    }
    while (digits < width)
    {
        *--p = UI_ATTR; // leading spaces
        digits++;
    }
}

void uiPrintS16(u16 x, u16 y, s16 val, u16 width)
{
    if (val < 0)
    {
        uiPrint(x, y, "-");
        uiPrintNum(x + 1, y, (u16)(-val), width);
    }
    else
    {
        uiPrint(x, y, "+");
        uiPrintNum(x + 1, y, (u16)val, width);
    }
}

void uiFlush(void)
{
    dmaCopyVram((u8 *)uiMap, 0x4000, UI_COLS * UI_ROWS * 2);
}
