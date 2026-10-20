.text
.globl main
main:
    li   r1, 1
    addi r1, r1, 1
    beq  r1, r1, target
    addi r2, r0, 1
target:
    addi r3, r0, 2
    halt
