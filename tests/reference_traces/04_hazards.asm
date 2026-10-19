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
    li t0, 5
    add t1, t0, t0
    add t2, t1, t1
    add t3, t2, t2
    li t0, 0x0100
    lw t1, 0(t0)
    add t2, t1, t1
    li t0, 0x0100
    lw t1, 0(t0)
    sw t1, 4(t0)
    li t0, 0x0100
    li t2, 0x1234
    lw t1, 0(t0)
    sw t2, 0(t1)
    li t0, 0x0100
    lw t1, 0(t0)
    beq t1, t0, Lbranch_1
Lbranch_1:
    li t0, 0x0100
    la t2, Ljalr_2
    sw t2, 0(t0)
    lw t1, 0(t0)
    jalr r0, t1, 0
Ljunk_3:
    addi s0, s0, 1
Ljalr_2:
    li t0, 1
    beq t0, t0, Lover_4
    addi s0, s0, 1
    addi s1, s1, 1
Lover_4:
    jal r15, fn0
    add t0, r15, r15
    la t0, fn1
    jalr r15, t0, 0
    add t1, r15, r15
    add r0, t0, t1
    addi r0, r0, 1
    li t0, 0x0200
    li t1, 0x1234
    sw t1, 0(t0)
    lw t2, 0(t0)
    li t0, 7
    nop
    add t1, t0, t0
    nop
    nop
    add t2, t0, t0
    li a0, 0
    lw lr, 0(sp)
    addi sp, sp, 2
    ret

fn0:
    li a0, 1
    ret

fn1:
    li a0, 2
    ret
