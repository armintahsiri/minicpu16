.text
.globl main
main:
    li   r1, 0x0100
    li   r2, 0x0007
    sw   r2, 0(r1)
    lw   r3, 0(r1)
    li   r5, 0x0001
    add  r4, r5, r3
    halt
