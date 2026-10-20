.text
.globl main
main:
    li   r1, 0x0100
    li   r2, 0x0102
    sw   r2, 0(r1)
    li   r2, 0x0055
    sw   r2, 2(r1)
    lw   r3, 0(r1)
    lw   r4, 0(r3)
    halt
