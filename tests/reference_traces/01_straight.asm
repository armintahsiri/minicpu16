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
    li t2, 0x0920
    li t3, 0x613A
    sb a3, 0(t2)
    slli t1, a3, 4
    add s0, s3, s2
    li t2, 0x0B76
    li t3, 0x50A3
    sb t3, 0(t2)
    sltu t0, t3, t3
    sltu a3, a1, a3
    slli s0, t3, 15
    xor s2, t1, a3
    li t2, 0x0E70
    li t3, 0x2A99
    sw s0, 0(t2)
    addi s0, a0, -23564
    srl a2, t1, a0
    or t0, s2, t0
    srai t0, a3, 12
    li t2, 0x0A0E
    li t3, 0x65A0
    sw t1, 0(t2)
    srl a0, a1, t0
    li a0, 0
    lw lr, 0(sp)
    addi sp, sp, 2
    ret
