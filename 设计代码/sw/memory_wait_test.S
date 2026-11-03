.section .text
.globl _start
_start:
    li s0, 0x10000000
    la t0, handler
    csrw mtvec, t0
    li t0, 0x800
    csrw mie, t0
    li t0, 8
    csrw mstatus, t0
    li t0, 37
    sw t0, 0(s0)
    addi t1, t0, 5
    lw t2, 0(s0)
    add t3, t2, t1
    sw t3, 4(s0)
    li t4, 79
    bne t3, t4, fail
    li t0, 0x85
    sb t0, 1(s0)
    lbu t2, 1(s0)
    bne t2, t0, fail
    lb t2, 1(s0)
    li t0, -123
    bne t2, t0, fail
    beq zero, zero, skip_store
    sw t0, 32(s0)
skip_store:
    fence.i
    la s1, store_fault
    li s2, 7
    li s3, 0x20001000
    li a6, 43
    mv t0, a6
store_fault:
    sw t0, 0(s3)
    csrw mscratch, t0
    csrr t1, mscratch
    bne t1, t0, fail
    csrw mscratch, zero
    la s1, load_fault
    li s2, 5
    li s3, 0x1000fffc
    li a6, 91
    mv t0, a6
load_fault:
    lw t0, 0(s3)
    csrw mscratch, t0
    bne t0, a6, fail
    csrw mscratch, zero
    li s1, 0x4000
    li s2, 1
    mv s3, s1
    jalr zero, s1, 0
fetch_recovered:
    li t0, 3
    bne a3, t0, fail
    li t0, 1
    bne a4, t0, fail
    li a0, 0x600d
done:
    j done

handler:
    csrr t4, mcause
    bltz t4, irq_handler
    bne t4, s2, fail
    csrr t4, mepc
    bne t4, s1, fail
    csrr t4, mtval
    bne t4, s3, fail
    csrr t4, mscratch
    bnez t4, fail
    li t4, 1
    beq s2, t4, fetch_fault_handler
    bne t0, a6, fail
    addi t4, s1, 4
    j fault_return
fetch_fault_handler:
    la t4, fetch_recovered
fault_return:
    csrw mepc, t4
    addi a3, a3, 1
    mret
irq_handler:
    li t5, 0x8000000b
    bne t4, t5, fail
    csrw mie, zero
    addi a4, a4, 1
    mret
fail:
    li a0, 0xbad
    j fail
