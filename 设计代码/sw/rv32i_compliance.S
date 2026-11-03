    .section .text
    .globl _start
_start:
    addi t0, zero, 9
    addi t1, zero, 4
    add  t2, t0, t1
    addi t3, zero, 13
    bne  t2, t3, fail
    sub  t2, t0, t1
    addi t3, zero, 5
    bne  t2, t3, fail
    xor  t2, t0, t1
    addi t3, zero, 13
    bne  t2, t3, fail
    or   t2, t0, t1
    bne  t2, t3, fail
    and  t2, t0, t1
    addi t3, zero, 0
    bne  t2, t3, fail
    sll  t2, t1, t1
    addi t3, zero, 64
    bne  t2, t3, fail
    srl  t2, t2, t1
    addi t3, zero, 4
    bne  t2, t3, fail
    addi t0, zero, -8
    srai t2, t0, 2
    addi t3, zero, -2
    bne  t2, t3, fail
    slt  t2, t0, t3
    addi t3, zero, 1
    bne  t2, t3, fail
    sltu t2, zero, t0
    bne  t2, t3, fail
    slti t2, zero, 1
    bne  t2, t3, fail
    sltiu t2, zero, 1
    bne  t2, t3, fail
    andi t2, t3, 1
    bne  t2, t3, fail
    ori  t2, zero, 9
    xori t2, t2, 3
    addi t3, zero, 10
    bne  t2, t3, fail
    lui  t2, 0x12345
    lui  t3, 0x12345
    bne  t2, t3, fail
    auipc t2, 0
    beq  t2, t2, branch_taken
    jal  zero, fail
branch_taken:
    bge  t2, t2, branch_ge
    jal  zero, fail
branch_ge:
    bltu zero, t2, branch_lt
    jal  zero, fail
branch_lt:
    bgeu t2, zero, branch_geu
    jal  zero, fail
branch_geu:
    blt  zero, t2, branch_signed_lt
    jal  zero, fail
branch_signed_lt:
    bne  zero, t2, branch_ne
    jal  zero, fail
branch_ne:
    li   t0, 8
    sb   t0, 0(zero)
    li   t0, 0x2345
    sh   t0, 2(zero)
    li   t0, 0x12345678
    sw   t0, 4(zero)
    lbu  t1, 0(zero)
    li   t2, 8
    bne  t1, t2, fail
    lhu  t1, 2(zero)
    li   t2, 0x2345
    bne  t1, t2, fail
    lw   t1, 4(zero)
    bne  t1, t0, fail
    lb   t1, 0(zero)
    li   t2, 8
    bne  t1, t2, fail
    jal  t3, jal_target
    jal  zero, fail
jal_target:
    bne  t3, zero, jal_link_ok
    jal  zero, fail
jal_link_ok:
    la   t0, jalr_target
    jalr t3, 0(t0)
    jal  zero, fail
jalr_target:
    li   a0, 0x1234
    sw   a0, 256(zero)
    ecall
fail:
    li   a0, 0xdead
    sw   a0, 256(zero)
    ebreak
