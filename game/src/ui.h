/* Text UI band: the top WAVE_UI_LINES scanlines run in BG mode 1 (switched
   back to mode 7 below via HDMA channel 0), giving 3 tiled text rows that
   will grow into the game UI.

   The PVSnesLib console's map upload is hardcoded to VRAM $0800 (inside the
   Mode 7 region), so this module owns its own tiny map buffer and DMAs it to
   0x6800 itself. The library is still used for its font + palette load. */
#ifndef UI_H
#define UI_H

#include <snes.h>

#define UI_COLS 32
#define UI_ROWS 3

void uiInit(void);

// program HDMA channel 0 (the per-scanline BG mode switch); call every frame
// alongside the other channel setup
void uiHdma(void);

// write text / a right-aligned decimal into the band (rows 0..UI_ROWS-1)
void uiPrint(u16 x, u16 y, char *s);
void uiPrintNum(u16 x, u16 y, u16 val, u16 width);

// push the buffer to VRAM — call in vblank, BEFORE waveHdma (the DMA uses
// channel 0, whose registers waveHdma reprograms for HDMA afterwards)
void uiFlush(void);

#endif
