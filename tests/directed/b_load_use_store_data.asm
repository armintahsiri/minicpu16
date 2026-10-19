.text
.globl main
main:
    li   r3, 0x0020
    li   r1, 0x0100
    li   r5, 0x1234
    sw   r5, 0(r1)
    lw   r2, 0(r1)
    sw   r2, 0(r3)
    halt
