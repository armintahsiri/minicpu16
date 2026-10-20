.text
.globl main
main:
    li   r1, 0x0100
    li   r2, 0x0080
    sb   r2, 0(r1)
    lb   r3, 0(r1)
    add  r4, r3, r0
    halt
