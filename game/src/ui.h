/* Text UI band: the top WAVE_UI_LINES scanlines run in BG mode 1 (switched
   back to mode 7 below via HDMA channel 0). Four tile rows: row 0 blank
   (CRT overscan crops it), row 1 HUD titles, rows 2-3 the double-height
   values. Columns 0 and 31 are avoided too - CRTs shave them.

   The PVSnesLib console's map upload is hardcoded to VRAM $0800 (inside the
   Mode 7 region), so this module owns its own tiny map buffer and DMAs it
   itself. The library is still used for its font + palette load. */
#ifndef UI_H
#define UI_H

#include <snes.h>

#define UI_COLS 32
#define UI_ROWS 4

// gradient-text palette rows (map word bits 10-12): the baked ramps at
// CGRAM 64-111 - titles yellow->green, value tops green->yellow, value
// bottoms yellow->red. The colours live per PIXEL ROW in the glyphs, so
// the "colour changes every scanline" effect costs nothing at runtime.
#define HUD_PAL_TITLE 0x1000
#define HUD_PAL_TOP 0x1400
#define HUD_PAL_BOT 0x1800

void uiInit(void);

// HUD gradient text. Big = double height across rows 2+3; Small = single
// height with the palette row of your choice; Digit avoids the glyph
// lookup for per-tick counters. ' ' clears the cell(s).
void uiHudSmall(u16 x, u16 y, u16 pal, char *s);
void uiHudBig(u16 x, char *s);
void uiHudBigDigit(u16 x, u16 d);
void uiHudBigClear(u16 x, u16 w);

// program HDMA channel 0 (the sand distance-fade CGRAM stream; the BG
// mode switch rides a scanline IRQ); call every frame with the others
void uiHdma(void);

// course-select helpers: uiClear blanks the band buffer (uiFlush pushes
// it); uiMenuRow writes a console-font line into a map row BELOW the band
// (rows >= UI_ROWS + WAVE_SKY_ROWS only - the race's mode switch hides
// them); uiMenuClearRows blanks all of those rows. VRAM writers: call
// under force blank or in vblank.
void uiClear(void);
void uiMenuRow(u16 row, u16 x, char *s);
void uiMenuClearRows(void);

// write text / a right-aligned decimal into the band (rows 0..UI_ROWS-1)
void uiPrint(u16 x, u16 y, char *s);
void uiPrintNum(u16 x, u16 y, u16 val, u16 width);
void uiPrintS16(u16 x, u16 y, s16 val, u16 width); // sign char + number

// push the buffer to VRAM — call in vblank, BEFORE waveHdma (the DMA uses
// channel 0, whose registers waveHdma reprograms for HDMA afterwards)
void uiFlush(void);

#endif
