#include "ui.h"
#include "wavedata.h"

extern char sky_gfx, sky_pal2; // baked mode-1 sky band (wavetables.asm)
extern char cloud_gfx, cloud_map; // BG2 cloud overlay strip
extern char hud_gfx, hud_pal;     // gradient HUD font + its CGRAM ramps

#define REG_BG2VOFS (*(vuint8 *)0x2110)

// HDMA table for $2105: mode 1 from the top (text band + tiled sky) down
// to the baked switch line - just above the wave cycle's highest horizon -
// then mode 7 for the sea. The strip between the switch and the true
// horizon stays backdrop + COLDATA gradient (see bake_tables.py).
static const u8 uiModeTable[] = {
    WAVE_SKY_SWITCH, 0x01, // BG mode 1: text + sky tiles
    1, 0x07,               // then BG mode 7 for the sea
    0x00
};

// map words: font tile = ascii - 32, palette row 1 (bits 10-12)
#define UI_ATTR 0x0400

static dmaMemory dmaMode;
u16 uiMap[UI_COLS * UI_ROWS];

void uiInit(void)
{
    u16 i;

    // Font + palette via the library (both respect the address overrides;
    // only the console's own MAP path is $0800-hardcoded, which we bypass).
    // VRAM kept clear of mode 7's 0x0000-0x3FFF: font 0x5000, map 0x7000
    // (the OBJ sheet at 0x6000 runs to 0x6BFF since the NPC ski band).
    bgSetGfxPtr(0, 0x5000);
    bgSetMapPtr(0, 0x7000, SC_32x32);
    consoleSetTextGfxPtr(0x5000);
    consoleSetTextMapPtr(0x7000);
    consoleInitDefaultText(1); // palette row 1 = CGRAM 16-31 (see colour map)

    // mode-1 sky band: gradient tiles (chars WAVE_SKY_CHAR0+, VRAM above
    // the font) on palette row 2 (CGRAM 32-47), one char per map row from
    // tile row 3 down to the mode-switch line. Uses uiMap as scratch for
    // the row DMAs, then the text init below repaints it.
    dmaCopyVram((u8 *)&sky_gfx, 0x5000 + WAVE_SKY_CHAR0 * 16,
                WAVE_SKY_ROWS * 32);
    dmaCopyCGram((u8 *)&sky_pal2, 32, 32);
    // WAVE_SKY_ROWS includes the extra bottom row for the BG scroll
    // off-by-one (screen line N samples map line N+1); the bake authors
    // the tiles map-line indexed so every visible line lands exactly
    for (i = 0; i < WAVE_SKY_ROWS; i++)
    {
        u16 c;
        for (c = 0; c < 32; c++)
            uiMap[c] = 0x0800 | (WAVE_SKY_CHAR0 + i); // palette row 2
        dmaCopyVram((u8 *)uiMap, 0x7000 + (UI_ROWS + i) * 32, 64);
    }

    // BG2 cloud overlay (mode-1 sky rows only; the TM table keeps it off
    // the text band, and mode 7 ignores BG2's map/char bases entirely -
    // there BG2 is the EXTBG layer). Chars share BG1's 0x5000 base
    // (cloud tiles at WAVE_CLOUD_CHAR0, between the font and the sky
    // rows); map at 0x7400. Map words carry the priority bit: mode 1
    // draws BG2-high above BG1-low, so the clouds sit ON the gradient.
    bgSetGfxPtr(1, 0x5000);
    bgSetMapPtr(1, 0x7400, SC_32x32);
    dmaCopyVram((u8 *)&cloud_gfx, 0x5000 + WAVE_CLOUD_CHAR0 * 16,
                WAVE_CLOUD_CHARS * 32);
    for (i = 0; i < UI_COLS * UI_ROWS; i++)
        uiMap[i] = 0; // char 0 = font space: transparent
    for (i = 0; i < 16; i++) // clear the whole 32x32 map...
        dmaCopyVram((u8 *)uiMap, 0x7400 + i * 64, 128);
    dmaCopyVram((u8 *)&cloud_map, 0x7400 + WAVE_CLOUD_ROW0 * 32,
                WAVE_CLOUD_TROWS * 64); // ...then drop the strip in
    setPaletteColor(50, WAVE_CLOUD_SHADE); // row 3: 49 is the checker white

    // HUD gradient font: 3 blocks of glyphs (single height / double-height
    // tops / bottoms) above the BG2 map, and the three colour ramps
    dmaCopyVram((u8 *)&hud_gfx, 0x5000 + WAVE_HUD_CHAR0 * 16,
                WAVE_HUD_GLYPHS * 3 * 32);
    dmaCopyCGram((u8 *)&hud_pal, 64, 96);
    // BG scroll off-by-one (screen line N samples map line N+1): VOFS -1
    // makes screen row == map row exactly for the strip
    REG_BG2VOFS = 0xFF;
    REG_BG2VOFS = 0x03;

    for (i = 0; i < UI_COLS * UI_ROWS; i++)
        uiMap[i] = UI_ATTR; // tile 0 = space

    uiFlush();
}

void uiHdma(void)
{
    dmaMode.mem.p = (u8 *)uiModeTable;
    REG_DMAP0 = 0x00;
    REG_BBAD0 = 0x05; // $2105 BG mode
    REG_A1T0LH = dmaMode.mem.c.addr;
    REG_A1B0 = dmaMode.mem.c.bank;
}

// glyph order must match the bake's HUD_GLYPHS
static u16 hudIdx(char c)
{
    char *g = "0123456789'\"/!ADEFGHIKLMNOPRST";
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
        *p++ = *s == ' ' ? 0 : pal | (WAVE_HUD_CHAR0 + hudIdx(*s));
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
            *t++ = 0;
            *b++ = 0;
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
        *t++ = 0;
        *b++ = 0;
    }
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
    dmaCopyVram((u8 *)uiMap, 0x7000, UI_COLS * UI_ROWS * 2);
}
