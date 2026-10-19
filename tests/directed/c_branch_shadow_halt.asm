.text
.globl main
main:
    beq  r0, r0, target
    halt
    addi r6, r0, 1
target:
    addi r7, r0, 1
    halt
