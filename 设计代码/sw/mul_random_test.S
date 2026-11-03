    .section .text
    .globl _start
_start:
    lui  s1, 0x10000
    li   s0, 4096
1:  lw   t0, 0(s1)
    lw   t1, 4(s1)
    mul  t2, t0, t1
    sw   t2, 0x200(zero)
    mulh t2, t0, t1
    sw   t2, 0x204(zero)
    mulhsu t2, t0, t1
    sw   t2, 0x208(zero)
    mulhu t2, t0, t1
    sw   t2, 0x20c(zero)
    addi s1, s1, 8
    addi s0, s0, -1
    bnez s0, 1b
    li   a0, 0x600d
    sw   a0, 0x100(zero)
2:  j    2b
