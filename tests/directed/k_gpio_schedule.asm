.text
.globl main
main:
    li   t0, GPIO_IN
    lw   t1, 0(t0)
    nop
    lw   t2, 0(t0)
    nop
    nop
    lw   t3, 0(t0)
    halt
