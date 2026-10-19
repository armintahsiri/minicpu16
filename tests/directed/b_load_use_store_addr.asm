.text
.globl main
main:
    li   r1, 0x0100
    li   r2, 0x0007
    sw   r2, 0(r1)
    lw   r3, 0(r1)
    li   r4, 0x0055
    sw   r4, 0(r3)
    lw   r5, 0(r1)
    halt
