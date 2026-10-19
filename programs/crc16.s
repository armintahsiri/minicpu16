# Bitwise CRC-16/XMODEM over the ASCII string "123456789".
# poly 0x1021, init 0x0000, no reflection; the check value is 0x31C3.
.data
.align 1
msg:  .asciz "123456789"
crc:  .word 0

.text
.globl main
main:
    la   t0, msg
    li   t1, 0
.Lbyte:
    lbu  t2, 0(t0)
    beqz t2, .Lcrc_done
    slli t2, t2, 8
    xor  t1, t1, t2
    li   t3, 8
.Lbit:
    slli t2, t1, 1
    li   a2, 0x8000
    and  a2, t1, a2
    beqz a2, .Lno_xor
    li   a2, 0x1021
    xor  t2, t2, a2
.Lno_xor:
    mv   t1, t2
    addi t3, t3, -1
    bnez t3, .Lbit
    addi t0, t0, 1
    j    .Lbyte
.Lcrc_done:
    la   t0, crc
    sw   t1, 0(t0)
    li   a0, 0
    ret
