.text
.globl main
main:
    li   r1, 0x0100
    la   r2, target
    sw   r2, 0(r1)
    lw   r3, 0(r1)
    jalr r0, r3, 0
    li   r6, 0x1111
target:
    li   r7, 0x2222
    halt
