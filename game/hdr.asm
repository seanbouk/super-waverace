;===================================================================================
; hdr.asm - HAND-MAINTAINED (AUTOHDR := 0 in the Makefile): identical to the
; generated header except the native IRQ vector, which points at irqStub
; (camera.asm) - the scanline timer IRQ that flips mode 1 -> mode 7 at the
; sky switch, freeing HDMA channel 0 for the sand distance-fade CGRAM writes.
;===================================================================================

; HIROM / FASTROM definitions



.ifdef HIROM                    ;   ==HiRom==

.MEMORYMAP                      ; Begin describing the system architecture.
  SLOTSIZE $10000               ; The slot is $10000 bytes in size. More details on slots later.
  DEFAULTSLOT 0                 ; There's only 1 slot in SNES, there are more in other consoles.
  SLOT 0 $0000                  ; Defines Slot 0's starting address.
  SLOT 1 $0 $2000               ; Used for low RAM allocation
  SLOT 2 $2000 $E000            ; Used for RAM allocation
  SLOT 3 $0 $10000              ; Used for global RAM allocation
  SLOT 4 $6000                  ; Used for SRAM storage.
.ENDME                          ; End MemoryMap definition

.ROMBANKSIZE $10000             ; Every ROM bank is 64 KBytes in size

.else                           ;   ==LoRom==

.MEMORYMAP                      ; Begin describing the system architecture.
  SLOTSIZE $8000                ; The slot is $8000 bytes in size. More details on slots later.
  DEFAULTSLOT 0                 ; There's only 1 slot in SNES, there are more in other consoles.
  SLOT 0 $8000                  ; Defines Slot 0's starting address.
  SLOT 1 $0 $2000               ; Used for low RAM allocation
  SLOT 2 $2000 $E000            ; Used for RAM allocation
  SLOT 3 $0 $10000              ; Used for global RAM allocation and data storage
.ENDME                          ; End MemoryMap definition

.ROMBANKSIZE $8000              ; Every ROM bank is 32 KBytes in size

.endif

.ROMBANKS 8

.SNESHEADER
  ID "SNES"
  NAME "SUPER WAVERACE"
  SLOWROM
  LOROM
  CARTRIDGETYPE $00
  ROMSIZE $08
  SRAMSIZE $00
  COUNTRY $01
  LICENSEECODE $00
  VERSION $00
.ENDSNES

.SNESNATIVEVECTOR               ; Define Native Mode interrupt vector table
  COP EmptyHandler
  BRK EmptyHandler
  ABORT EmptyHandler
  NMI VBlank
  IRQ irqStub
.ENDNATIVEVECTOR

.SNESEMUVECTOR                  ; Define Emulation Mode interrupt vector table
  COP EmptyHandler
  ABORT EmptyHandler
  NMI EmptyHandler
  RESET tcc__start
  IRQBRK EmptyHandler
.ENDEMUVECTOR


.ifdef FASTROM
.ifdef HIROM
    .BASE $C0
.else
    .BASE $80
.endif
.else
.ifdef HIROM
    .BASE $40
.endif
.endif