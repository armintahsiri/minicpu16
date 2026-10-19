.text
.globl main
main:
    li   r1, 0xFF00
    li   r2, 0x0041
    sw   r2, 0(r1)
    li   r3, 0x0042
    sw   r3, 0(r1)
    halt
