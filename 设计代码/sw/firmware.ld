ENTRY(_start)

SECTIONS
{
    . = 0x00000000;
    .text : {
        *(.text.start)
        *(.text*)
        *(.rodata*)
    }
    /DISCARD/ : {
        *(.comment)
        *(.riscv.attributes)
    }
}
