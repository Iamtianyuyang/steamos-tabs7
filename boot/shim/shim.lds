/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Linked at 0 and position independent (PC-relative addressing only). The
 * mainline DTB follows the code; the Image starts at 2 MiB; the file is padded
 * by the build script to SHIM_RESERVE so the bootloader keeps the ramdisk and
 * its DTB out of the window the shim moves the Image and DTB into.
 */
ENTRY(_start)
SECTIONS
{
	. = 0;
	.head : { KEEP(*(.head)) }
	.text : { *(.text*) }
	.rodata : { *(.rodata*) }
	.data : { *(.data*) }
	. = ALIGN(16);
	__bss_start = .;
	.bss : { *(.bss*) *(COMMON) }
	. = ALIGN(16);
	__bss_end = .;
	. = ALIGN(8);
	.payload.dtb : { __dtb_start = .; KEEP(*(.payload.dtb)) }
	. = 0x200000;
	.payload.kernel : { __kernel_start = .; KEEP(*(.payload.kernel)) }
	/DISCARD/ : { *(.comment) *(.note*) *(.eh_frame*) }
}
