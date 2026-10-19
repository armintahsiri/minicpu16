.text
.globl main
main:
    li   r1, 0x0100
    li   r5, 0x1234
    sw   r5, 0(r1)
    lw   r2, 0(r1)
    beq  r2, r5, target
    li   r6, 0x1111
target:
    li   r7, 0x2222
    halt
