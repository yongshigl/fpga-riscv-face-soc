    .section .text.start
    .globl _start
_start:
    li t0, 0x100
    li t1, -1
    li t2, 2
    mul t3, t1, t2
    sw t3, 0(t0)
    mulh t3, t1, t2
    sw t3, 4(t0)
    mulhsu t3, t1, t2
    sw t3, 8(t0)
    mulhu t3, t1, t2
    sw t3, 12(t0)
    li t1, 0x80000000
    li t2, -1
    mulh t3, t1, t2
    sw t3, 16(t0)
    li t2, 2
    mulh t3, t1, t2
    sw t3, 20(t0)
    mulhu t3, t1, t2
    sw t3, 24(t0)
    mulhsu t3, t1, t2
    sw t3, 28(t0)
    mulh t3, t1, t2
    bne t3, zero, high_branch_ok
    li a0, 0xdead
    j done
high_branch_ok:
    mulhu t3, t1, t2
    addi t3, t3, 5
    sw t3, 32(t0)
    mulh t3, t1, t2
    mulh t4, t3, t2
    sw t4, 36(t0)
    li t1, 7
    sw t1, 40(t0)
    lw t1, 40(t0)
    mul t3, t1, t2
    sw t3, 44(t0)
    lw t1, 40(t0)
    mulh t3, t1, t2
    sw t3, 48(t0)
    li a0, 0x1234
done:
    j done
