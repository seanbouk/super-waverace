;----------------------------------------------------------------------------
; buildCamTables — build the five camera-dependent HDMA tables for one frame.
;
; For scanline i the renderer needs (theta = camera heading):
;   M7A_i  =  a_i * cos(theta)                      (8.8)
;   M7C_i  = -a_i * sin(theta)                      (8.8)
;   M7X_i  = (camPX + d_i * sin(theta)) & 1023
;   M7Y_i  = (camPY + d_i * cos(theta)) & 1023
;   HOFS_i = (M7X_i - 128) & 1023
; where d_i (hit distance) and a_i (horizontal scale) come from the baked
; ROM arrays wave_rawd / wave_rawa (32 phases x 224 words, stride 448,
; both in one bank so a single DBR covers the reads).
;
; Products use the S-CPU hardware multiplier ($4202/$4203 -> $4216), 16x8
; unsigned with sign flags handled separately. Trig is s0.7 from camSinTab.
;
; Register roles in the build loop:
;   Y = ROM word index (DBR-relative reads)
;   X = destination entry offset (long,X stores into camTabs)
;
; Inputs  (C globals): camTheta, camPX, camPY, camPhaseOff (phase*448),
;                      camBufOff (0 or 452)
; Outputs (C globals): camTabs[] HDMA tables, camSinVal/camCosVal (s16,
;                      +/-127 = 1.0) for C-side movement maths.
;
; Table layout inside camTabs (stride 904 per register, 452 per buffer):
;   +0 M7A | +904 M7C | +1808 M7X | +2712 M7Y | +3616 HOFS
; Each 452-byte table: $FF hdr, 127 word entries, $E1 hdr, 97 word entries,
; $00 terminator (headers pre-written once by C).
;----------------------------------------------------------------------------
.include "hdr.asm"

;----------------------------------------------------------------------------
; (a16 at \1) * (mag8 at \2) >> 7  ->  A (16-bit unsigned)
; Preserves X and Y. Uses camTmp. Hardware regs via long absolute.
.MACRO MUL16X8
    sep #$20
    lda.l \1
    sta.l $004202
    lda.l \2
    sta.l $004203        ; p1 = lo * mag (ready 8 master clocks later)
    lda.l \1 + 1
    sta.l $004202        ; operand for p2 (does not retrigger)
    rep #$20
    lda.l $004216        ; p1
    asl a
    sta.l camTmp         ; p1 << 1
    sep #$20
    lda.l \2
    sta.l $004203        ; p2 = hi * mag
    rep #$20
    lda.l camTmp
    xba
    and #$00FF           ; (p1 << 1) >> 8  ==  p1 >> 7
    sta.l camTmp
    lda.l $004216        ; p2 (delay covered by the ops above)
    asl a
    clc
    adc.l camTmp
.ENDM

; negate 16-bit A when byte flag at \1 is non-zero
.MACRO NEGIF
    sta.l camTmp2
    sep #$20
    lda.l \1
    rep #$20
    and #$00FF
    beq _pos\@
    lda.l camTmp2
    eor #$FFFF
    inc a
    bra _done\@
_pos\@:
    lda.l camTmp2
_done\@:
.ENDM

; one scanline: Y = ROM word index, X = table entry offset (both advance +2)
.MACRO LINEBODY
    rep #$20
    lda.w wave_rawa,y
    sta.l camA16
    lda.w wave_rawd,y
    sta.l camD16

    MUL16X8 camA16, camCosMag
    NEGIF camCosNeg
    sta.l camTabs + 0,x        ; M7A = a*cos

    MUL16X8 camA16, camSinMag
    NEGIF camSinNegInv
    sta.l camTabs + 904,x      ; M7C = -a*sin

    MUL16X8 camD16, camSinMag
    NEGIF camSinNeg
    clc
    adc.l camPX
    and #$03FF
    sta.l camTabs + 1808,x     ; M7X = (px + d*sin) & 1023
    sec
    sbc #128
    and #$03FF
    sta.l camTabs + 3616,x     ; HOFS = (M7X - 128) & 1023

    MUL16X8 camD16, camCosMag
    NEGIF camCosNeg
    clc
    adc.l camPY
    and #$03FF
    sta.l camTabs + 2712,x     ; M7Y = (py + d*cos) & 1023
.ENDM

;----------------------------------------------------------------------------
.SECTION ".camera_text" SUPERFREE

buildCamTables:
    php
    phb
    rep #$30
    phx
    phy

    ; ---- trig lookup: sin ----
    lda.l camTheta
    and #$00FF
    tax
    sep #$20
    lda.l camSinTab,x
    bpl _sin_pos
    eor #$FF
    inc a
    sta.l camSinMag
    lda #$01
    sta.l camSinNeg
    lda #$00
    sta.l camSinNegInv
    bra _sin_done
_sin_pos:
    sta.l camSinMag
    lda #$00
    sta.l camSinNeg
    lda #$01
    sta.l camSinNegInv
_sin_done:

    ; ---- trig lookup: cos = sin(theta + 64) ----
    rep #$20
    lda.l camTheta
    and #$00FF
    clc
    adc #64
    and #$00FF
    tax
    sep #$20
    lda.l camSinTab,x
    bpl _cos_pos
    eor #$FF
    inc a
    sta.l camCosMag
    lda #$01
    sta.l camCosNeg
    bra _cos_done
_cos_pos:
    sta.l camCosMag
    lda #$00
    sta.l camCosNeg
_cos_done:

    ; ---- signed 16-bit trig values for the C movement code ----
    rep #$20
    lda.l camSinMag
    and #$00FF
    sta.l camSinVal
    lda.l camSinNeg
    and #$00FF
    beq _sv_done
    lda.l camSinVal
    eor #$FFFF
    inc a
    sta.l camSinVal
_sv_done:
    lda.l camCosMag
    and #$00FF
    sta.l camCosVal
    lda.l camCosNeg
    and #$00FF
    beq _cv_done
    lda.l camCosVal
    eor #$FFFF
    inc a
    sta.l camCosVal
_cv_done:

    ; ---- DBR = bank of the raw ROM arrays for the ,y reads ----
    sep #$20
    lda #:wave_rawd
    pha
    plb
    rep #$20

    ; ---- build the 224 entries: block 1 = 127 lines, block 2 = 97 lines ----
    lda.l camPhaseOff
    tay                        ; Y = ROM word index
    lda.l camBufOff
    inc a
    tax                        ; X = dest offset (skip $FF header byte)

    lda #127
    sta.l camLineCt
_block1:
    LINEBODY
    iny
    iny
    inx
    inx
    rep #$20
    lda.l camLineCt
    dec a
    sta.l camLineCt
    beq _b1_done
    jmp _block1
_b1_done:

    inx                        ; skip the $E1 header byte

    lda #97
    sta.l camLineCt
_block2:
    LINEBODY
    iny
    iny
    inx
    inx
    rep #$20
    lda.l camLineCt
    dec a
    sta.l camLineCt
    beq _b2_done
    jmp _block2
_b2_done:

    rep #$30
    ply
    plx
    plb
    plp
    rtl

.ENDS
