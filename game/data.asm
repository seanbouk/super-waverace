.include "hdr.asm"

.section ".rodata1" superfree

sea_patterns:
.incbin "sea.pc7"
sea_patterns_end:

sea_map:
.incbin "sea.mp7"

sea_palette:
.incbin "sea.pal"

.ends
