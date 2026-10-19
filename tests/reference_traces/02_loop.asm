# MiniCPU-16 crt0.
# Entry point at instruction index 0.  Sets SP to the top of RAM, zeroes .bss,
# calls main(argc=0, argv=NULL), and halts.  Reset does not reload .data and does
# not clear registers, so this is the only guaranteed initialisation.

.text
.globl _start
_start:
    li   sp, RAM_BYTES
    la   t0, __bss_start
    la   t1, __bss_end
.Lbss_loop:
    bgeu t0, t1, .Lbss_done
    sb   zero, 0(t0)
    addi t0, t0, 1
    j    .Lbss_loop
.Lbss_done:
    li   a0, 0
    li   a1, 0
    call main
    halt

.text
.globl main
main:
    addi sp, sp, -2
    sw lr, 0(sp)
    li s4, 4
Lloop_1:
    li t2, 0x071A
    lb t2, 0(t2)
    li t2, 0x0562
    lw a3, 0(t2)
    li t2, 0x0ED4
    li t3, 0x748D
    sb t3, 0(t2)
    li t2, 0x0398
    li t3, 0xADEA
    sb t3, 0(t2)
    srli t1, s0, 12
    addi s4, s4, -1
    bnez s4, Lloop_1
    li a0, 0
    lw lr, 0(sp)
    addi sp, sp, 2
    ret
