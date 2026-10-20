.text
.globl main
main:
    jal  r15, .+1
    add  r1, r15, r15
    halt
