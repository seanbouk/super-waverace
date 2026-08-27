;----------------------------------------------------------------------------
; buildCamTables — build the five camera-dependent HDMA tables for one frame.
;
; For scanline i the renderer needs (theta = camera heading):
;   M7A_i  =  a_i * cos(theta)                      (8.8)
;   M7C_i  = -a_i * sin(theta)                      (8.8)
;   M7X_i  = (camPX + d_i * sin(theta)) & 1023
;   M7Y_i  = (camPY + d_i * cos(theta)) & 1023
;   HOFS_i = (M7X_i - 128) as signed 13-bit
; d_i / a_i live in WRAM bank $7F (WRD/WRA below): waveRawLoad copies the
; baked ROM arrays there at load time (32 phases x 224 words, stride 448,
; one bank => one DBR covers all reads). WRAM so the per-course loader can
; swap/expand them - the hot loop reads WRAM at the same cycle cost as ROM.
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
camDP dsb 64 ; 0-15 build, 16-31 projectPoint, 32-39 ski, 40-51 npc
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

; npc steering helpers' frame
.DEFINE NA_DX  $28
.DEFINE NA_DY  $2A
.DEFINE NA_X4  $2C
.DEFINE NA_Y4  $2E
.DEFINE NA_T   $30
.DEFINE NA_T2  $32

; rowDepth binary search frame
.DEFINE RD_LO  $34
.DEFINE RD_HI  $36
.DEFINE RD_MID $38

; the expanded wave d/a arrays, WRAM bank $7F (filled by waveRawLoad)
.DEFINE WRD  $0000        ; d: 32 phases x 224 x u16 = 14336 bytes
.DEFINE WRA  $3800        ; a: ditto (synthesised from d at load)
.DEFINE WRD_L $7F0000     ; long-address forms (rowDepth runs with DBR=$00)
.DEFINE WRA_L $7F3800

; waveRawLoad's direct-page frame (shares camDP; nothing else runs at load)
.DEFINE WL_P2L $00
.DEFINE WL_P2H $02
.DEFINE WL_P1L $04
.DEFINE WL_P1H $06
.DEFINE WL_RLO $08
.DEFINE WL_RHI $0A
.DEFINE WL_PRV $0C

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
    PRODLO WRA, DP_COSM2
    .IF \1 == 1
    eor #$FFFF
    inc a
    .ENDIF
    sta.l camTabs + 0,x        ; M7A (M7B word stays 0)

    PRODHI WRA, DP_SINM2
    .IF \2 == 1
    eor #$FFFF
    inc a
    .ENDIF
    sta.l camTabs + 1800,x     ; M7C (M7D word stays 0)

    ; pair D (d_i): M7X/HOFS from d*sin, M7Y from d*cos
    PRODLO WRD, DP_SINM2
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

    PRODHI WRD, DP_COSM2
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

    ; ---- DBR = the WRAM bank holding the expanded d/a arrays ----
    sep #$20
    lda #$7F
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
    phd
    rep #$30
    phx
    pea camDP
    pld
    ; d[] is non-increasing down-screen, so "lowest row with d >= rdV" is
    ; the last-true of a monotone predicate: binary search, 8 probes flat
    ; instead of up to 223 linear steps. Single mode (16-bit) throughout.
    lda.l camPhaseOff
    clc
    adc #446
    tax
    lda.l WRD_L,x        ; P(223): bottom row already deep enough?
    cmp.l rdV
    bcc _rd_srch
    sta.l rdD
    lda #223
    sta.l rdRow
    bra _rd_done
_rd_srch:
    lda.l camPhaseOff
    tax
    lda.l WRD_L,x        ; P(0): anything at all in range?
    cmp.l rdV
    bcs _rd_bin
    lda #$FFFF
    sta.l rdRow
    bra _rd_done
_rd_bin:
    lda #0               ; invariant: P(lo) true, P(hi) false
    sta.b RD_LO
    lda #223
    sta.b RD_HI
_rd_iter:
    lda.b RD_LO
    clc
    adc.b RD_HI
    lsr a
    sta.b RD_MID
    asl a                ; row -> byte offset
    clc
    adc.l camPhaseOff
    tax
    lda.l WRD_L,x
    cmp.l rdV
    bcc _rd_hi
    lda.b RD_MID
    sta.b RD_LO
    bra _rd_step
_rd_hi:
    lda.b RD_MID
    sta.b RD_HI
_rd_step:
    lda.b RD_HI
    sec
    sbc.b RD_LO
    cmp #2
    bcs _rd_iter
    lda.b RD_LO
    sta.l rdRow
    asl a
    clc
    adc.l camPhaseOff
    tax
    lda.l WRD_L,x
    sta.l rdD
_rd_done:
    plx
    pld
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
    ; NOTE the mode discipline (mirrors buildCamTables): the branchy 8-bit
    ; sections contain no interior rep - WLA sizes immediates from the
    ; TEXTUALLY last sep/rep, so a rep inside one arm makes the assembler
    ; encode the other arm's lda #imm as 3 bytes while the CPU runs it in
    ; 8-bit mode... and the spare byte is $00 = BRK
    sep #$20
    lda.l camSinTab,x
    bpl _np_sp
    eor #$FF
    inc a
    sta.l npcSinMag
    lda #$01
    sta.l npcSinNeg
    bra _np_sd
_np_sp:
    sta.l npcSinMag
    lda #$00
    sta.l npcSinNeg
_np_sd:
    rep #$20
    lda.l npcA
    and #$00FF
    clc
    adc #64
    and #$00FF
    tax
    sep #$20
    lda.l camSinTab,x
    bpl _np_cp
    eor #$FF
    inc a
    sta.l npcCosMag
    lda #$01
    sta.l npcCosNeg
    bra _np_cd
_np_cp:
    sta.l npcCosMag
    lda #$00
    sta.l npcCosNeg
_np_cd:
    ; signed s16 values from the sign-magnitude form, one straight-line
    ; 16-bit tail
    rep #$20
    lda.l npcSinMag
    and #$00FF
    sta.l npcSin
    lda.l npcSinNeg
    and #$00FF
    beq _np_sv
    lda.l npcSin
    eor #$FFFF
    inc a
    sta.l npcSin
_np_sv:
    lda.l npcCosMag
    and #$00FF
    sta.l npcCos
    lda.l npcCosNeg
    and #$00FF
    beq _np_cv
    lda.l npcCos
    eor #$FFFF
    inc a
    sta.l npcCos
_np_cv:
    plx
    plp
    rtl

;----------------------------------------------------------------------------
; npcAim - the racer's steering geometry: wrapped deltas from position
; (aimPX/PY) to target (aimTX/TY), nudged sideways by aimBias along the
; heading's perpendicular, then cross (apc) and dot (apd) against the
; heading trig from npcTrig. Bit-exact port of the C. The steering
; DECISIONS made from apc/apd stay in C.
npcAim:
    php
    phd
    rep #$20
    pea camDP
    pld
    lda.l aimTX
    sec
    sbc.l aimPX
    and #$0FFF
    cmp #2049
    bcc _na_dx
    ora #$F000
_na_dx:
    sta.b NA_DX
    lda.l aimTY
    sec
    sbc.l aimPY
    and #$0FFF
    cmp #2049
    bcc _na_dy
    ora #$F000
_na_dy:
    sta.b NA_DY
    ; wpdx += (bias * cos) >> 7 ; wpdy -= (bias * sin) >> 7
    lda.l aimBias
    sta.b NA_T2
    SMUL NA_T2, npcCosMag, npcCosNeg
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    clc
    adc.b NA_DX
    sta.b NA_DX
    SMUL NA_T2, npcSinMag, npcSinNeg
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    ASR1
    sta.b NA_T
    lda.b NA_DY
    sec
    sbc.b NA_T
    sta.b NA_DY
    ; cross = (dy>>4)*sin - (dx>>4)*cos ; dot = (dx>>4)*sin + (dy>>4)*cos
    lda.b NA_DX
    ASR1
    ASR1
    ASR1
    ASR1
    sta.b NA_X4
    lda.b NA_DY
    ASR1
    ASR1
    ASR1
    ASR1
    sta.b NA_Y4
    SMUL NA_Y4, npcSinMag, npcSinNeg
    sta.b NA_T
    SMUL NA_X4, npcCosMag, npcCosNeg
    sta.b NA_T2
    lda.b NA_T
    sec
    sbc.b NA_T2
    sta.l apc
    SMUL NA_X4, npcSinMag, npcSinNeg
    sta.b NA_T
    SMUL NA_Y4, npcCosMag, npcCosNeg
    clc
    adc.b NA_T
    sta.l apd
    lda.b NA_DX
    sta.l wpdx
    lda.b NA_DY
    sta.l wpdy
    pld
    plp
    rtl

;----------------------------------------------------------------------------
; npcVel - velocity components along the heading: apc/apd = ((bq>>5) * trig)
; >> 2 from the racer speed in bq (always positive, so only the trig signs
; matter)
npcVel:
    php
    rep #$20
    lda.l bq
    lsr a
    lsr a
    lsr a
    lsr a
    lsr a
    sep #$20
    sta.l $004202
    lda.l npcSinMag
    sta.l $004203
    lda.l npcSinNeg
    lsr a
    rep #$20
    lda.l $004216
    bcc _nv_s
    eor #$FFFF
    inc a
_nv_s:
    ASR1
    ASR1
    sta.l apc
    lda.l bq
    lsr a
    lsr a
    lsr a
    lsr a
    lsr a
    sep #$20
    sta.l $004202
    lda.l npcCosMag
    sta.l $004203
    lda.l npcCosNeg
    lsr a
    rep #$20
    lda.l $004216
    bcc _nv_c
    eor #$FFFF
    inc a
_nv_c:
    ASR1
    ASR1
    sta.l apd
    plp
    rtl

;----------------------------------------------------------------------------
; collProbe - read the course collision map (C cannot do far ROM reads).
; Packed 2 bits per cell: byte collOfs>>2, bit (collOfs&3)*2.
; in: collOfs = cellY*128 + cellX; out: collVal = 0 water / 1 sand / 2 rope
collProbe:
    php
    rep #$30
    phx
    phy
    lda.l collOfs
    and #$0003
    asl a
    tay                  ; Y = unpack shift (0/2/4/6 lsr steps)
    lda.l collOfs
    lsr a
    lsr a
    tax
    sep #$20
    lda.l wave_coll,x    ; packed byte
    cpy #0
    beq _cp_mask
_cp_sh:
    lsr a
    dey
    bne _cp_sh
_cp_mask:
    and #$03
    sta.l collVal
    rep #$30
    ply
    plx
    plp
    rtl

;----------------------------------------------------------------------------
; waveRawLoad - expand the baked wave arrays into WRAM bank $7F (the
; buildCamTables/rowDepth source). Load-time only; two passes:
;  1. d: delta-decode the ROM stream (first word raw, then one signed byte
;     per word, $80 = escape + raw word) into WRD.
;  2. a: synthesised, not stored - a = max(1, (d*18919 + 32768) >> 16),
;     where 18919 = round(tan(fovH/2)/2 * 65536) = 74*256 - 25: two
;     positive factors for the PPU 16x8 multiplier (M7A=d, M7B=74 then
;     25, product at $2134-36). The bake asserts this decomposition; the
;     camera (fovH included) is global across courses BY DESIGN.
; MUST run with the screen blanked and HDMA off: mode 7 owns M7A/M7B once
; the frame runs. Both passes are straight-line single-mode regions (the
; WLA immediate-sizing gotcha).
waveRawLoad:
    php
    phb
    phd
    rep #$30
    phx
    phy
    pea camDP
    pld
    sep #$20
    lda #:wave_rawd
    pha
    plb
    rep #$20

    ; ---- pass 1: delta-decode d into $7F0000 ----
    ldy #2               ; ROM index: past the first raw word
    ldx #0               ; dest byte offset
    lda.w wave_rawd      ; first word raw
    bra _wr_store
_wr_next:
    lda.w wave_rawd,y    ; control byte (word read, low byte matters)
    iny
    and #$00FF
    cmp #$0080
    beq _wr_esc
    bit #$0080           ; sign-extend the delta
    beq _wr_pos
    ora #$FF00
_wr_pos:
    clc
    adc.b WL_PRV
    bra _wr_store
_wr_esc:
    lda.w wave_rawd,y    ; raw word follows the escape
    iny
    iny
_wr_store:
    sta.b WL_PRV
    sta.l WRD_L,x
    inx
    inx
    cpx #WRA
    bne _wr_next

    ; ---- pass 2: a[i] from d[i] via the PPU multiplier ----
    ldx #0
_wr_a:
    rep #$20
    lda.l WRD_L,x        ; d
    sep #$20
    sta.l $00211B        ; M7A lo
    xba
    sta.l $00211B        ; M7A hi
    lda #74
    sta.l $00211C        ; M7B: P2 = d * 74 (combinational, no wait)
    rep #$20
    lda.l $002134        ; P2 lo16
    sta.b WL_P2L
    lda.l $002135        ; $2136:$2135 - keep the hi byte
    xba
    and #$00FF
    sta.b WL_P2H
    sep #$20
    lda #25
    sta.l $00211C        ; M7B: P1 = d * 25
    rep #$20
    lda.l $002134
    sta.b WL_P1L
    lda.l $002135
    xba
    and #$00FF
    sta.b WL_P1H
    ; R = (P2 << 8) + $8000 - P1; a = R >> 16  (= (d*18919 + $8000) >> 16)
    lda.b WL_P2L
    xba
    and #$FF00           ; (p2 & $FF) << 8
    clc
    adc #$8000
    sta.b WL_RLO         ; carry C1 rides through to the Rhi add
    lda.b WL_P2H
    xba                  ; p2h << 8
    sta.b WL_RHI
    lda.b WL_P2L
    xba
    and #$00FF           ; p2 >> 8 (low half)
    adc.b WL_RHI         ; + C1: Rhi = (P2 >> 8) + carry
    sta.b WL_RHI
    lda.b WL_RLO
    sec
    sbc.b WL_P1L         ; borrow chains into the high word
    lda.b WL_RHI
    sbc.b WL_P1H         ; a = high word of R
    bne _wr_anz
    lda #1               ; floor: a >= 1 (d = 0 rows)
_wr_anz:
    sta.l WRA_L,x
    inx
    inx
    cpx #WRA
    bne _wr_a

    ply
    plx
    pld
    plb
    plp
    rtl

.ENDS

;----------------------------------------------------------------------------
; Scanline IRQ: the mode-1 -> mode-7 switch, moved off HDMA channel 0 so
; ch0 can stream the sand distance-fade CGRAM writes instead. The V+H
; timer fires just before hblank on the line ABOVE the switch; we ack
; ($4211 read is mandatory or the IRQ refires), wait for the hblank flag
; so the mode never flips mid-line, and write $2105. The NMI callback in
; main.c restores mode 1 (0x09) at the top of every frame.
; The vector stub must live in BANK 0 (the native IRQ vector is a 16-bit
; bank-0 address); the handler body is SUPERFREE and reached by jml.
; Straight-line single-mode sections (see the WLA immediate-sizing gotcha).
.SECTION ".irqstub" BANK 0 SLOT 0 FREE
irqStub:
    jml irqSwitch
.ENDS

.SECTION ".irqsw" SUPERFREE
irqSwitch:
    rep #$20
    pha                  ; 16-bit push: covers A whatever mode we landed in
    sep #$20
    lda.l $004211        ; TIMEUP: acknowledge the timer IRQ
_irq_wait:
    lda.l $004212        ; HVBJOY
    and #$40             ; hblank flag
    beq _irq_wait
    lda #$07
    sta.l $002105        ; sea rows: mode 7
    rep #$20
    pla
    rti

irqOn:                   ; called once from C after the timer regs are set
    cli
    rtl
.ENDS
