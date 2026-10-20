# TIMER edge: read TIMER once and store it.  The timing layer defines the
# read at MEM cycle c as c mod 2^16, so an off-by-one sample is visible.
.text
.globl main
main:
    li t0, TIMER
    lw t1, 0(t0)
    li t2, 0x0100
    sw t1, 0(t2)
    halt

