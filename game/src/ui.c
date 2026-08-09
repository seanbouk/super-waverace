#include "ui.h"
#include "wavedata.h"

// HDMA table for $2105: mode 1 for the UI band, mode 7 for the rest
static const u8 uiModeTable[] = {
    WAVE_UI_LINES, 0x01, // BG mode 1 (text on BG1, 16 colours)
    1, 0x07,             // then BG mode 7 for the remaining lines
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
    // VRAM kept clear of mode 7's 0x0000-0x3FFF: font 0x5000, map 0x6800.
    bgSetGfxPtr(0, 0x5000);
    bgSetMapPtr(0, 0x6800, SC_32x32);
    consoleSetTextGfxPtr(0x5000);
    consoleSetTextMapPtr(0x6800);
    consoleInitDefaultText(1); // palette row 1 = CGRAM 16-31 (see colour map)

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

void uiFlush(void)
{
    dmaCopyVram((u8 *)uiMap, 0x6800, UI_COLS * UI_ROWS * 2);
}
