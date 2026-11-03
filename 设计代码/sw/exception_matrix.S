    .section .text
    .globl _start
_start:
    la   t0, trap_entry
    csrw mtvec, t0
    csrw mscratch, zero

    .word 0x0020006f
after_instruction_misaligned:
    ecall
after_ecall:
    ebreak
after_ebreak:
    lw   t0, 1(zero)
after_load_misaligned:
    sw   t0, 2(zero)
after_store_misaligned:
    .word 0xffffffff
after_illegal:
    csrr t0, mscratch
    li   t1, 6
    bne  t0, t1, fail
    li   a0, 0x5afe
    sw   a0, 256(zero)
done:
    j done

trap_entry:
    csrr t0, mscratch
    csrr t1, mcause
    beqz t0, expect_instruction_misaligned
    li   t2, 1
    beq  t0, t2, expect_ecall
    li   t2, 2
    beq  t0, t2, expect_breakpoint
    li   t2, 3
    beq  t0, t2, expect_load_misaligned
    li   t2, 4
    beq  t0, t2, expect_store_misaligned
    li   t2, 5
    beq  t0, t2, expect_illegal
    j fail
expect_instruction_misaligned:
    li   t2, 0
    j check_cause
expect_ecall:
    li   t2, 11
    j check_cause
expect_breakpoint:
    li   t2, 3
    j check_cause
expect_load_misaligned:
    li   t2, 4
    j check_cause
expect_store_misaligned:
    li   t2, 6
    j check_cause
expect_illegal:
    li   t2, 2
check_cause:
    bne  t1, t2, fail
    addi t0, t0, 1
    csrw mscratch, t0
    csrr t0, mepc
    addi t0, t0, 4
    csrw mepc, t0
    mret

fail:
    li   a0, 0xdead
    sw   a0, 256(zero)
    j fail
