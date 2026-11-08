MEMORY {
  FLASH (rx) : ORIGIN = 0x20000, LENGTH = 0x1f800
  RAM (rw) : ORIGIN = 0x20003e28, LENGTH = 0x1d8
}
SECTIONS {
  .text : { *(.text*) *(.rodata*) . = ALIGN(4); } > FLASH
  .data : { *(.data*) } > RAM AT> FLASH
  .bss (NOLOAD) : {
    __bss_start__ = .;
    *(.bss*) *(COMMON)
    . = ALIGN(4);
    __bss_end__ = .;
  } > RAM
  /DISCARD/ : { *(.comment*) *(.ARM.exidx*) *(.ARM.extab*) *(.eh_frame*) }
  ASSERT(SIZEOF(.data) == 0, "radio payload must have no initialized RAM")
  ASSERT(__bss_end__ <= 0x20004000, "radio patch RAM overflow")
}
