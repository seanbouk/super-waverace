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
camDP dsb 48 ; 0-15 buildCamTables, 16-31 projectPoint, 32-47 ski helpers
.ENDS

; projectPoint's direct-page frame (shares camDP; the build owns 0-15)
.DEFINE PJ_DX  $10
.DEFINE PJ_DY  $12
.DEFINE PJ_V   $14
.DEFINE PJ_U   $16
.DEFINE PJ_AU  $18
.DEFINE PJ_T1  $1A

; ski math helpers' frame
.DEFINE SK_A   $20
.DEFINE SK_B   $22
.DEFINE SK_T   $24
.DEFINE SK_T2  $26

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
; one arithmetic shift right (the 65816 only has logical LSR)
.MACRO ASR1
    cmp #$8000
    ror a
.ENDM

; one projection term: dest +/-= (bdelta * trig) >> 7, computed EXACTLY as
; the old C did - bh = d >> 4 (floor), bl = d & 15, (bh*S)>>3 + (bl*S)>>7
; with floor shifts throughout - so the port is bit-identical, not merely
; close. \1 = trig magnitude global, \2 = trig sign global, \3 = delta dp
; offset, \4 = dest dp offset, \5 = 1 to subtract the term (bu -= dy*sin)
.MACRO PJTERM
    ; high part: (bh * S) >> 3
    rep #$20
    lda.b \3
    ASR1
    ASR1
    ASR1
    ASR1                 ; bh = d >> 4, floor
    sta.b DP_TMP         ; keep signed bh (its sign bit drives the negate)
    bpl _pos\@
    eor #$FFFF
    inc a
_pos\@:
    sep #$20
    sta.l $004202        ; |bh| (<= 44 after the +/-700 cull)
    lda.l \1
    sta.l $004203        ; * mag
    lda.b DP_TMP + 1     ; sign(bh) from the high byte...
    asl a                ; ...into carry
    lda.l \2             ; trig sign (0/1)
    adc #0               ; A = trigNeg + sign(bh): bit 0 = XOR
    lsr a                ; effective sign -> carry (also pads the mul wait)
    rep #$20
    lda.l $004216        ; |bh| * mag
    bcc _hp\@
    eor #$FFFF
    inc a
_hp\@:
    ASR1
    ASR1
    ASR1                 ; (bh * S) >> 3, floor
    sta.b PJ_T1
    ; low part: (bl * S) >> 7
    lda.b \3
    and #$000F           ; bl (always positive)
    sep #$20
    sta.l $004202
    lda.l \1
    sta.l $004203        ; * mag
    lda.l \2
    lsr a                ; trig sign -> carry (pads the mul wait)
    rep #$20
    lda.l $004216        ; bl * mag
    bcc _lp\@
    eor #$FFFF
    inc a
_lp\@:
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1                 ; (bl * S) >> 7, floor
    clc
    adc.b PJ_T1          ; the full term
    .IF \5 == 0
    clc
    adc.b \4
    sta.b \4
    .ELSE
    sta.b PJ_T1
    lda.b \4
    sec
    sbc.b PJ_T1
    sta.b \4
    .ENDIF
.ENDM

;----------------------------------------------------------------------------
; projectPoint - project a world point into the buoy/NPC view (exact port of
; the former C routine; identical floor semantics, so OAM output is
; bit-identical). Culls: wrapped delta beyond +/-700, view depth outside
; 176..620, |u| > 480, no surface row, column off screen.
; in:  pjX, pjY (world), camPX/Y, camSinMag/Neg, camCosMag/Neg, camPhaseOff
; out: pjOk; when 1: pjV (view depth), pjCol (screen centre x), rdRow
projectPoint:
    php
    phd
    rep #$20
    pea camDP
    pld
    sep #$20
    lda #0
    sta.l pjOk
    rep #$20
    ; dx = wrap(pjX - camPX), reject beyond +/-700
    lda.l pjX
    sec
    sbc.l camPX
    and #$0FFF
    cmp #2049
    bcc _pj_dx
    ora #$F000           ; 12-bit value: | $F000 == - 4096
_pj_dx:
    sta.b PJ_DX
    clc
    adc #700
    cmp #1401
    bcc _pj_dxok
    jmp _pj_out          ; too far for a short branch past the term macros
_pj_dxok:
    ; dy likewise
    lda.l pjY
    sec
    sbc.l camPY
    and #$0FFF
    cmp #2049
    bcc _pj_dy
    ora #$F000
_pj_dy:
    sta.b PJ_DY
    clc
    adc #700
    cmp #1401
    bcc _pj_dyok
    jmp _pj_out
_pj_dyok:
    ; view transform: v = (dx*sin + dy*cos) >> 7, u = (dx*cos - dy*sin) >> 7
    stz.b PJ_V
    stz.b PJ_U
    PJTERM camSinMag, camSinNeg, PJ_DX, PJ_V, 0
    PJTERM camCosMag, camCosNeg, PJ_DX, PJ_U, 0
    PJTERM camCosMag, camCosNeg, PJ_DY, PJ_V, 0
    PJTERM camSinMag, camSinNeg, PJ_DY, PJ_U, 1
    ; depth cull: 176 <= v <= 620
    lda.b PJ_V
    sec
    sbc #176
    cmp #445
    bcs _pj_far
    ; lateral cull: |u| <= 480
    lda.b PJ_U
    bpl _pj_au
    eor #$FFFF
    inc a
_pj_au:
    sta.b PJ_AU
    cmp #481
    bcs _pj_far
    ; surface row for this depth
    lda.b PJ_V
    sta.l rdV
    jsl rowDepth
    lda.l rdRow
    cmp #$FFFF
    bne _pj_row
_pj_far:
    jmp _pj_out          ; culls past the divider block: out of short range
_pj_row:
    ; column = |u|*64 / ((v*74) >> 8) via the hardware divider
    lda.b PJ_AU
    asl a
    asl a
    asl a
    asl a
    asl a
    asl a
    sta.l $004204        ; WRDIVL/H
    lda.b PJ_V
    sep #$20
    sta.l $004202        ; lo(v)
    lda #74
    sta.l $004203
    rep #$20             ; (rep + loads pad the mul wait)
    lda.b PJ_V
    xba
    and #$00FF           ; hi(v): 0..2
    sta.b DP_TMP
    lda.l $004216        ; lo(v) * 74
    xba
    and #$00FF           ; >> 8
    sta.b PJ_T1
    lda.b DP_TMP
    beq _pj_div
    lda.b PJ_T1
    clc
    adc #74              ; + hi * 74 (hi is 1 or 2)
    sta.b PJ_T1
    lda.b DP_TMP
    cmp #2
    bne _pj_div
    lda.b PJ_T1
    clc
    adc #74
    sta.b PJ_T1
_pj_div:
    sep #$20
    lda.b PJ_T1
    sta.l $004206        ; divisor byte: division starts, 16-cycle latency
    rep #$20             ; 3
    nop                  ; +14 = 17 cycles: latency covered
    nop
    nop
    nop
    nop
    nop
    nop
    lda.l $004214        ; quotient
    cmp #141
    bcs _pj_out
    sta.b DP_TMP
    ; column = u < 0 ? 128 - q : 128 + q
    lda.b PJ_U
    bmi _pj_neg
    lda.b DP_TMP
    clc
    adc #128
    bra _pj_col
_pj_neg:
    lda #128
    sec
    sbc.b DP_TMP
_pj_col:
    ; edge cull: 12 <= column <= 232 (unsigned wrap rejects negatives)
    sta.b DP_TMP
    sec
    sbc #12
    cmp #221
    bcs _pj_out
    lda.b DP_TMP
    sta.l pjCol
    lda.b PJ_V
    sta.l pjV
    sep #$20
    lda #1
    sta.l pjOk
_pj_out:
    pld
    plp
    rtl

;----------------------------------------------------------------------------
; one signed product: A := (s16 at dp \1, |x| <= 255) * (trig \2 with sign
; \3), exactly as tcc computes it (magnitudes stay under 2^15, so the
; sign-and-magnitude trick is bit-identical to a signed multiply)
.MACRO SMUL
    rep #$20
    lda.b \1
    sta.b DP_TMP         ; sign source
    bpl _sp\@
    eor #$FFFF
    inc a
_sp\@:
    sep #$20
    sta.l $004202        ; |x|
    lda.l \2
    sta.l $004203        ; * mag
    lda.b DP_TMP + 1     ; sign(x) -> carry
    asl a
    lda.l \3
    adc #0               ; bit 0 = sign(x) XOR trig sign
    lsr a                ; -> carry (also pads the mul wait)
    rep #$20
    lda.l $004216
    bcc _sq\@
    eor #$FFFF
    inc a
_sq\@:
.ENDM

; the ski's heading transform, one direction: given inputs \1 \2 (s16
; globals), \3 := ((\1>>5)*sin + (\2>>5)*cos) >> 2 and
; \4 := ((\1>>5)*cos - (\2>>5)*sin) >> 2 - the exact shape (and floor
; semantics) of the old C split/merge, which are the same formula with
; the operand pairs swapped: split(vx,vy)->(along,side),
; merge(along,side)->(vx,vy)
.MACRO SKICORE
    rep #$20
    lda.l \1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    sta.b SK_A
    lda.l \2
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    sta.b SK_B
    SMUL SK_A, camSinMag, camSinNeg
    sta.b SK_T
    SMUL SK_B, camCosMag, camCosNeg
    clc
    adc.b SK_T
    ASR1
    ASR1
    sta.l \3
    SMUL SK_A, camCosMag, camCosNeg
    sta.b SK_T
    SMUL SK_B, camSinMag, camSinNeg
    sta.b SK_T2
    lda.b SK_T
    sec
    sbc.b SK_T2
    ASR1
    ASR1
    sta.l \4
.ENDM

; A := (u8 magnitude at \1 * trig \2 with sign \3) >> \4, floor semantics
.MACRO TRIGK
    sep #$20
    lda.l \1
    sta.l $004202
    lda.l \2
    sta.l $004203
    lda.l \3
    lsr a                ; trig sign -> carry (pads the mul wait)
    rep #$20
    lda.l $004216
    bcc _tk\@
    eor #$FFFF
    inc a
_tk\@:
    .REPT \4
    ASR1
    .ENDR
.ENDM

;----------------------------------------------------------------------------
; skiSplit - vAlong/vSide from skiVX/skiVY along the camera heading
; skiMerge - skiVX/skiVY back from vAlong/vSide (the C keeps the grip
; conditional between the two calls)
skiSplit:
    php
    phd
    rep #$20
    pea camDP
    pld
    SKICORE skiVX, skiVY, vAlong, vSide
    pld
    plp
    rtl

skiMerge:
    php
    phd
    rep #$20
    pea camDP
    pld
    SKICORE vAlong, vSide, skiVX, skiVY
    pld
    plp
    rtl

;----------------------------------------------------------------------------
; skiWorld - skiWX/Y = camPX/Y + (skiDist8 * sin/cos) >> 7
skiWorld:
    php
    TRIGK skiDist8, camSinMag, camSinNeg, 7
    clc
    adc.l camPX
    sta.l skiWX
    TRIGK skiDist8, camCosMag, camCosNeg, 7
    clc
    adc.l camPY
    sta.l skiWY
    plp
    rtl

;----------------------------------------------------------------------------
; skiThrustF - skiVX/Y += (thrF8 * sin/cos) >> 6   (full throttle)
; skiThrustR - skiVX/Y -= (thrR8 * sin/cos) >> 6   (reverse, half power)
skiThrustF:
    php
    TRIGK thrF8, camSinMag, camSinNeg, 6
    clc
    adc.l skiVX
    sta.l skiVX
    TRIGK thrF8, camCosMag, camCosNeg, 6
    clc
    adc.l skiVY
    sta.l skiVY
    plp
    rtl

skiThrustR:
    php
    TRIGK thrR8, camSinMag, camSinNeg, 6
    pha
    lda.l skiVX
    sec
    sbc 1,s
    sta.l skiVX
    pla
    TRIGK thrR8, camCosMag, camCosNeg, 6
    pha
    lda.l skiVY
    sec
    sbc 1,s
    sta.l skiVY
    pla
    plp
    rtl

;----------------------------------------------------------------------------
; camPivot - orbit the camera so the ski stays fixed through a heading
; change: camPX/Y += (skiDist8 * (prevSin/Cos - camSin/CosVal)) >> 7.
; The 16-bit wrap of the product matches tcc's s16 multiply bit-for-bit.
camPivot:
    php
    rep #$20
    lda.l prevSin
    sec
    sbc.l camSinVal
    pha                  ; signed diff (|diff| <= 254 fits the multiplier)
    bpl _cv_sp
    eor #$FFFF
    inc a
_cv_sp:
    sep #$20
    sta.l $004202
    lda.l skiDist8
    sta.l $004203
    rep #$20             ; 3
    pla                  ; 5: mul wait padded; A = signed diff
    bpl _cv_pp
    lda.l $004216
    eor #$FFFF
    inc a
    bra _cv_pq
_cv_pp:
    lda.l $004216
_cv_pq:
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    clc
    adc.l camPX
    sta.l camPX
    lda.l prevCos
    sec
    sbc.l camCosVal
    pha
    bpl _cv_sq
    eor #$FFFF
    inc a
_cv_sq:
    sep #$20
    sta.l $004202
    lda.l skiDist8
    sta.l $004203
    rep #$20
    pla
    bpl _cv_pr
    lda.l $004216
    eor #$FFFF
    inc a
    bra _cv_ps
_cv_pr:
    lda.l $004216
_cv_ps:
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    clc
    adc.l camPY
    sta.l camPY
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
