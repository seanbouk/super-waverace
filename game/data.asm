.include "hdr.asm"

; tiles (up to 16KB at the full 256-tile budget) get their own bank
.section ".rodata1" superfree

sea_patterns:
.incbin "sea.pc7"
sea_patterns_end:

.ends

; map (16KB) + palette in a second bank
.section ".rodata2" superfree

sea_map:
.incbin "sea.mp7"

sea_palette:
.incbin "sea.pal"

.ends
