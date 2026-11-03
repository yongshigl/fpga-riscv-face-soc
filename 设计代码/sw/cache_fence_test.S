    .section .text.start
    .globl _start
    .option norvc
    .option norelax
_start:
    la t0, trap_handler
    csrw mtvec, t0
    li s0, 0x10000000
    li t0, 0x00150513
    sw t0, 0(s0)
    li t0, 0x00008067
    sw t0, 4(s0)
    fence.i
    li a0, 0
    jalr ra, 0(s0)
    li t1, 1
    bne a0, t1, failed

    // The instruction line is resident; D-cache write-through alone cannot replace it.
    li t0, 0x00750513
    sw t0, 0(s0)
    jalr ra, 0(s0)
    li t1, 2
    bne a0, t1, failed

    fence.i
    jalr ra, 0(s0)
    li t1, 9
    bne a0, t1, failed
    lw t1, 0(s0)
    bne t0, t1, failed

    // A second patch exercises invalidation of another completed refill.
    li t0, 0xffd50513
    sw t0, 0(s0)
    fence.i
    jalr ra, 0(s0)
    li t1, 6
    bne a0, t1, failed
    li a0, 0x600d
finished:
    j finished
trap_handler:
failed:
    li a0, 0xbad
    j failed
