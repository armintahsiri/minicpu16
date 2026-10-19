.text
.globl main
main:
    li   r1, 0x0100
    li   r2, 0x1234
    sw   r2, 0(r1)
    nop
    nop
    lw   r3, 0(r1)
    halt
