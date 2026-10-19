.text
.globl main
main:
    li   r1, 1
    beq  r1, r1, target
    beq  r0, r0, 2
    addi r6, r0, 1
target:
    li   r7, 0x2222
    halt
