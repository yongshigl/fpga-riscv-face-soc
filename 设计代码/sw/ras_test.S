.section .text
.globl _start
_start:
    li sp, 0x10000400
    la t0, trap_handler
    csrw mtvec, t0
    li a0, 12
    li a1, 0
    jal ra, recurse
    li t0, 13
    bne a1, t0, fail

    la t0, coroutine
    jalr ra, t0, 0
coroutine_resume:
    li t1, 1
    bne a2, t1, fail

    la t0, same_link
    jalr t0, t0, 0
same_link_resume:
    li t1, 2
    bne a2, t1, fail

    li t1, 0
    beq zero, zero, wrong_path_done
    jal ra, wrong_path
wrong_path_done:
    bnez t1, fail

    la t0, same_link
    jalr zero, t0, 2
after_trap:
    li t1, 1
    bne a3, t1, fail
    li a0, 0x600d
done:
    j done

recurse:
    addi sp, sp, -8
    sw ra, 4(sp)
    addi a1, a1, 1
    beqz a0, recurse_return
    addi a0, a0, -1
    jal ra, recurse
recurse_return:
    lw ra, 4(sp)
    addi sp, sp, 8
    ret

coroutine:
    li a2, 1
    jalr t0, ra, 0

same_link:
    li a2, 2
    jalr zero, t0, 0

wrong_path:
    li t1, 1
    ret

trap_handler:
    csrr t1, mcause
    bnez t1, fail
    csrr t1, mepc
    addi t1, t1, 4
    csrw mepc, t1
    addi a3, a3, 1
    mret

fail:
    li a0, 0xbad
    j fail
