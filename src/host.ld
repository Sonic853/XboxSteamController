/* LPC11U37, official host SHA 2c42adb2... . This is a linked extension,
 * not a replacement image. The original bootloader/scatter data remain intact. */
MEMORY {
  PATCH (rx) : ORIGIN = 0x00014000, LENGTH = 0x0000a000
  STATE (rw) : ORIGIN = 0x10001fd0, LENGTH = 0x10
}
SECTIONS {
  .text : {
    __host_payload_start = .;
    *(.text .text.*)
    *(.rodata .rodata.*)
    . = ALIGN(4);
    __host_payload_end = .;
  } > PATCH
  .data : { *(.data .data.*) } > STATE
  .bss (NOLOAD) : {
    __host_state_start = .;
    *(.bss .bss.* COMMON)
    __host_state_end = .;
  } > STATE
  /DISCARD/ : { *(.ARM.exidx* .ARM.extab* .comment .note* .eh_frame*) }
}
__host_new_msp = ORIGIN(STATE);
__host_bond_start = 0x1e000;
__host_bond_end = 0x20000;
ASSERT(SIZEOF(.data) == 0, "host extension must not require data copy")
ASSERT(SIZEOF(.bss) == 16, "host extension owns exactly 16 scratch bytes")
ASSERT(__host_payload_end <= __host_bond_start, "host payload overlaps bond journal")
