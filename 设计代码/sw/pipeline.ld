ENTRY(_start)

MEMORY
{
    ROM (rx)  : ORIGIN = 0x00000000, LENGTH = 64K
    RAM (rwx) : ORIGIN = 0x10000000, LENGTH = 32K
}

SECTIONS
{
    .text : {
        *(.text.start)
        *(.text*)
        *(.rodata*)
    } > ROM

    .data : {
        *(.data*)
        *(.sdata*)
    } > RAM

    .bss (NOLOAD) : {
        *(.bss*)
        *(.sbss*)
        *(COMMON)
    } > RAM

    _end = .;
}
