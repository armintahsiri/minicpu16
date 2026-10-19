.text
.globl main
main:
    li   r1, 0xFF04
    li   r2, 0x1234
    sw   r2, 0(r1)
    lw   r3, 0(r1)
    li   r4, 0xFF05
    li   r5, 0x00AB
    sb   r5, 0(r4)
    lw   r6, 0(r1)
    li   r7, 0xFF00
    li   r8, 0x0041
    sw   r8, 0(r7)
    li   r9, 0xFF0A
    sw   r8, 0(r9)
    lw   r10, 0(r9)
    lb   r11, 0(r9)
    halt
