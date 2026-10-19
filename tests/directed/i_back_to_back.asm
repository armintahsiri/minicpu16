.text
.globl main
main:
    jal  r15, target
    jalr r15, r5, 0
    addi r6, r0, 1
target:
    addi r7, r0, 1
    halt
