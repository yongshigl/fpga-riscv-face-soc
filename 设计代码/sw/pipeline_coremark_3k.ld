ENTRY(_start)

MEMORY
{
    ROM (rx)  : ORIGIN = 0x00000000, LENGTH = 16K
    RAM (rwx) : ORIGIN = 0x10000000, LENGTH = 3K
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
    ASSERT(_end <= 0x10000c00, "CoreMark data exceeds 3 KiB RAM")
}
