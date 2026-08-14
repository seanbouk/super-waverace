#include "ui.h"
#include "wavedata.h"

extern char sky_gfx, sky_pal2; // baked mode-1 sky band (wavetables.asm)

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
    // one EXTRA row repeating the last char: BG scroll is off by one
    // (screen line N samples map line N+1 at VOFS 0), so the band's last
    // screen line reads the row below - unwritten, it showed as a dark
    // backdrop seam at the mode switch
    for (i = 0; i <= WAVE_SKY_ROWS; i++)
    {
        u16 c;
        u16 ch = WAVE_SKY_CHAR0
                 + (i < WAVE_SKY_ROWS ? i : WAVE_SKY_ROWS - 1);
        for (c = 0; c < 32; c++)
            uiMap[c] = 0x0800 | ch; // palette row 2
        dmaCopyVram((u8 *)uiMap, 0x7000 + (UI_ROWS + i) * 32, 64);
    }

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
