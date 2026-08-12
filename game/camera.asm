;----------------------------------------------------------------------------
; buildCamTables — build the five camera-dependent HDMA tables for one frame.
;
; For scanline i the renderer needs (theta = camera heading):
;   M7A_i  =  a_i * cos(theta)                      (8.8)
;   M7C_i  = -a_i * sin(theta)                      (8.8)
;   M7X_i  = (camPX + d_i * sin(theta)) & 1023
;   M7Y_i  = (camPY + d_i * cos(theta)) & 1023
;   HOFS_i = (M7X_i - 128) as signed 13-bit
; d_i / a_i come from the baked ROM arrays wave_rawd / wave_rawa (32 phases
; x 224 words, stride 448, one bank => one DBR covers all reads).
;
; Optimised path (vs the straightforward first version):
;  - four sign-specialised loop bodies (sin/cos sign combos), chosen once per
;    call: no branches at all inside the per-line code
;  - trig magnitudes pre-doubled to s0.8 so (v*mag)>>7 becomes
;    high_byte(lo*mag2) + hi*mag2 — no shifts in the hot path
;  - the multiplier's operand register keeps the hi byte between the two
;    products that share a source word
;  - scratch, position and magnitudes live in a dedicated direct page
;    (the VBlank ISR sets its own D, so this is interrupt-safe)
;
; Register roles in the loop:  Y = ROM word index (DBR-relative reads)
;                              X = dest entry offset (long,X stores)
;
; Inputs  (C globals): camTheta, camPX, camPY, camSrcOff, camDstOff,
;                      camBlk1Ct (sky lines are skipped; C precomputes)
; Outputs (C globals): camTabs[] tables, camSinVal/camCosVal (s16, +/-127=1.0)
;
; camTabs layout: +0 M7A | +904 M7C | +1808 M7X | +2712 M7Y | +3616 HOFS,
; 452 bytes per double-buffer half: $FF hdr, 127 words, $E1 hdr, 97 words, $00.
;----------------------------------------------------------------------------
.include "hdr.asm"

; direct-page frame (page-aligned; offsets fixed by construction)
.DEFINE DP_TMP    $00
.DEFINE DP_SINM2  $02
.DEFINE DP_COSM2  $03
.DEFINE DP_PX     $04
.DEFINE DP_PY     $06
.DEFINE DP_CT     $08

.RAMSECTION ".camdp" BANK 0 SLOT 1 ALIGN 256
camDP dsb 16
.ENDS

;----------------------------------------------------------------------------
; one product: (16-bit word at wave_raw?,y) * (s0.8 mag at dp) >> 8
; PRODLO: full sequence loading lo+hi; leaves hi byte in $4202 for PRODHI
; \1 = source label, \2 = dp mag offset
.MACRO PRODLO
    sep #$20
    lda.w \1,y
    sta.l $004202
    lda.b \2
    sta.l $004203        ; lo * mag2
    lda.w \1 + 1,y
    sta.l $004202        ; hi -> operand (no retrigger; also covers the wait)
    rep #$20
    lda.l $004216        ; p1
    xba
    and #$00FF           ; p1 >> 8
    sta.b DP_TMP
    sep #$20
    lda.b \2
    sta.l $004203        ; hi * mag2
    rep #$20
    lda.l $004216        ; (rep + fetch cover the 8 master-clock delay)
    clc
    adc.b DP_TMP         ; (word * mag2) >> 8  ==  (word * mag) >> 7
.ENDM

; second product of the pair: hi byte still in $4202
.MACRO PRODHI
    sep #$20
    lda.b \2
    sta.l $004203        ; hi * mag2
    rep #$20
    lda.l $004216
    sta.b DP_TMP
    sep #$20
    lda.w \1,y
    sta.l $004202
    lda.b \2
    sta.l $004203        ; lo * mag2
    rep #$20
    lda.l $004216
    xba
    and #$00FF
    clc
    adc.b DP_TMP
.ENDM

; one scanline; args \1..\4 = negate A / C / dx / dy (0 or 1)
.MACRO OPTLINE
    ; pair A (a_i): M7A = a*cos, M7C = -a*sin
    ; table entries are 4 bytes (paired-register HDMA mode 3); the second
    ; word of the A, C and HOFS entries is B / D / VOFS = 0, pre-zeroed
    PRODLO wave_rawa, DP_COSM2
    .IF \1 == 1
    eor #$FFFF
    inc a
    .ENDIF
    sta.l camTabs + 0,x        ; M7A (M7B word stays 0)

    PRODHI wave_rawa, DP_SINM2
    .IF \2 == 1
    eor #$FFFF
    inc a
    .ENDIF
    sta.l camTabs + 1800,x     ; M7C (M7D word stays 0)

    ; pair D (d_i): M7X/HOFS from d*sin, M7Y from d*cos
    PRODLO wave_rawd, DP_SINM2
    .IF \3 == 1
    eor #$FFFF
    inc a
    .ENDIF
    clc
    adc.b DP_PX
    and #$0FFF                 ; world wraps every 4096 units...
    lsr a
    lsr a                      ; ...and one texture texel = 4 world units
    sta.l camTabs + 3600,x     ; M7X
    sec
    sbc #128
    and #$1FFF                 ; signed 13-bit: (HOFS - M7X) must stay -128
    sta.l camTabs + 5400,x     ; HOFS (VOFS word stays 0)

    PRODHI wave_rawd, DP_COSM2
    .IF \4 == 1
    eor #$FFFF
    inc a
    .ENDIF
    clc
    adc.b DP_PY
    and #$0FFF
    lsr a
    lsr a
    sta.l camTabs + 3602,x     ; M7Y (second word of the X entry)
.ENDM

; full dual-block build loop for one sign combination
.MACRO BUILDBODY
_l1\@:
    OPTLINE \1, \2, \3, \4
    iny
    iny
    inx
    inx
    inx
    inx
    dec.b DP_CT
    beq _b1\@
    jmp _l1\@
_b1\@:
    inx                        ; skip the $E1 header byte
    lda #97
    sta.b DP_CT
_l2\@:
    OPTLINE \1, \2, \3, \4
    iny
    iny
    inx
    inx
    inx
    inx
    dec.b DP_CT
    beq _b2\@
    jmp _l2\@
_b2\@:
    jmp buildDone
.ENDM

;----------------------------------------------------------------------------
.SECTION ".camera_text" SUPERFREE

buildCamTables:
    php
    phb
    phd
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
    bra _sin_done
_sin_pos:
    sta.l camSinMag
    lda #$00
    sta.l camSinNeg
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

    ; ---- direct page -> camDP; hot values into it ----
    pea camDP
    pld
    sep #$20
    lda.l camSinMag
    asl a
    sta.b DP_SINM2             ; doubled: s0.7 -> s0.8
    lda.l camCosMag
    asl a
    sta.b DP_COSM2
    rep #$20
    lda.l camPX
    sta.b DP_PX
    lda.l camPY
    sta.b DP_PY
    lda.l camBlk1Ct
    sta.b DP_CT

    ; ---- DBR = bank of the raw ROM arrays ----
    sep #$20
    lda #:wave_rawd
    pha
    plb

    ; ---- indices ----
    rep #$20
    lda.l camSrcOff
    tay
    lda.l camDstOff
    tax

    ; ---- dispatch to the sign-specialised body ----
    sep #$20
    lda.l camSinNeg
    bne _sneg
    lda.l camCosNeg
    bne _v01
    rep #$20
    jmp _variant00             ; sin+ cos+ : negate C only
_v01:
    rep #$20
    jmp _variant01             ; sin+ cos- : negate A, C, dy
_sneg:
    lda.l camCosNeg
    bne _v11
    rep #$20
    jmp _variant10             ; sin- cos+ : negate dx only
_v11:
    rep #$20
    jmp _variant11             ; sin- cos- : negate A, dx, dy

_variant00:
    BUILDBODY 0, 1, 0, 0
_variant01:
    BUILDBODY 1, 1, 0, 1
_variant10:
    BUILDBODY 0, 0, 1, 0
_variant11:
    BUILDBODY 1, 0, 1, 1

buildDone:
    rep #$30
    ply
    plx
    pld
    plb
    plp
    rtl

;----------------------------------------------------------------------------
;----------------------------------------------------------------------------
; rowDepth � find the lowest screen row whose surface distance >= rdV
; (d is non-increasing downward). in: rdV, camPhaseOff; out: rdRow (0xFFFF
; if none), rdD = distance actually shown at that row (occlusion check).
rowDepth:
    php
    rep #$30
    phx
    lda.l camPhaseOff
    clc
    adc #446
    tax
    lda #223
    sta.l rdRow
_rd_loop:
    lda.l wave_rawd,x
    cmp.l rdV
    bcs _rd_found
    dex
    dex
    lda.l rdRow
    dec a
    sta.l rdRow
    bpl _rd_loop
    lda #$FFFF
    sta.l rdRow
    bra _rd_done
_rd_found:
    lda.l wave_rawd,x
    sta.l rdD
_rd_done:
    plx
    plp
    rtl

;----------------------------------------------------------------------------
; npcTrig - signed sin/cos of an NPC heading from the camera's sine table
; (C cannot do far ROM reads). in: npcA (u8, 256 binary degrees);
; out: npcSin, npcCos (s16, +/-127 = 1.0) - same convention as camSin/CosVal
npcTrig:
    php
    rep #$30
    phx
    lda.l npcA
    and #$00FF
    tax
    sep #$20
    lda.l camSinTab,x
    rep #$20
    and #$00FF
    bit #$0080
    beq _np_sin_pos
    ora #$FF00
_np_sin_pos:
    sta.l npcSin
    lda.l npcA
    and #$00FF
    clc
    adc #64
    and #$00FF
    tax
    sep #$20
    lda.l camSinTab,x
    rep #$20
    and #$00FF
    bit #$0080
    beq _np_cos_pos
    ora #$FF00
_np_cos_pos:
    sta.l npcCos
    plx
    plp
    rtl

;----------------------------------------------------------------------------
; collProbe � read the course collision byte-map (C cannot do far ROM reads)
; in: collOfs = cellY*128 + cellX; out: collVal = 0 water / 1 sand / 2 rope
collProbe:
    php
    rep #$30
    phx
    lda.l collOfs
    tax
    sep #$20
    lda.l wave_coll,x
    sta.l collVal
    rep #$30
    plx
    plp
    rtl

.ENDS
