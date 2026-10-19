.text
.globl main
main:
    la   r5, target
    jalr r15, r5, 0
    jal  r15, 0
target:
    addi r6, r0, 1
    halt
