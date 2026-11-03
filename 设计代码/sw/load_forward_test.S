    .section .text
    .globl _start
_start:
    li t0, 0x80ff0081
    sw t0, 0x80(zero)

    lb t1, 0x80(zero)
    addi t2, t1, 1
    li t3, -126
    bne t2, t3, fail

    lbu t1, 0x80(zero)
    sw t1, 0x84(zero)
    lw t2, 0x84(zero)
    li t3, 0x81
    bne t2, t3, fail

    lh t1, 0x82(zero)
    li t2, -32513
    bne t1, t2, fail

    lhu t1, 0x82(zero)
    slli t2, t1, 1
    li t3, 0x101fe
    bne t2, t3, fail

    li t0, 0x90
    sw t0, 0x88(zero)
    li t2, 0x5678
    lw t1, 0x88(zero)
    sw t2, 0(t1)
    lw t1, 0x90(zero)
    bne t1, t2, fail

    la t0, jump_target
    sw t0, 0x8c(zero)
    lw t1, 0x8c(zero)
    jalr zero, 0(t1)
    j fail
jump_target:
    li a0, 0x1234
    sw a0, 0x100(zero)
    j jump_target
fail:
    li a0, 0xdead
    sw a0, 0x100(zero)
    j fail
