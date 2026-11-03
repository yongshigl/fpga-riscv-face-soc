.section .text
.globl _start
_start:
    li s0, 0x10000000
    li s1, 61

    li t0, 37
    lw t1, 0(s0)
    addi t2, t0, 5
    li t3, 42
    bne t2, t3, fail

    li t0, 43
    sw t1, 8(s0)
    add t2, t1, t0
    li t3, 74
    bne t2, t3, fail

    li t0, 99
    li t0, 52
    addi t2, t0, 1
    li t3, 53
    bne t2, t3, fail

    li t0, 19
    li t1, 23
    sub t2, t1, t0
    li t3, 4
    bne t2, t3, fail

    addi zero, zero, 123
    addi t2, zero, 9
    li t3, 9
    bne t2, t3, fail

    li t0, 61
    lw t1, 4(s0)
    beq t0, s1, branch_target
    sw zero, 28(s0)
    j fail
branch_target:
    li t3, 47
    bne t1, t3, fail

    la t0, jalr_target
    lw t1, 0(s0)
    jalr zero, t0, 0
    sw zero, 28(s0)
    j fail
jalr_target:
    li t3, 31
    bne t1, t3, fail

    li a0, 0x600d
done:
    j done
fail:
    li a0, 0xbad
    j fail
