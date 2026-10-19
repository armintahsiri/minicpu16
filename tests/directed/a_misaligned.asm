.text
.globl main
main:
    li   r1, 0x0100
    li   r2, 0xABCD
    sw   r2, 1(r1)
    lw   r3, 1(r1)
    lb   r4, 1(r1)
    lbu  r5, 0(r1)
    halt
