    .section .text.start
    .globl _start
_start:
    li   sp, 0x10008000
    call main
halt:
    j halt
