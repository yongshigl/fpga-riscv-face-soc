    .section .text.start
    .globl _start
_start:
    li t0, 0
    li t1, 20
loop:
    addi t0, t0, 1
    blt t0, t1, loop
    mv a0, t0
done:
    j done
