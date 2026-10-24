/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Entry probe: a tiny arm64 "Image" that tells whether the bootloader jumps
 * into the BOOT kernel at all, without depending on any kernel code.
 *
 * 1. Paint the bootloader splash (cont_splash_mem, 1600x2560 XRGB8888 at
 *    0x9c000000) solid red. A red screen proves the payload ran and that the
 *    simple-framebuffer geometry in the board DTS is right.
 * 2. Loop forever petting the APSS watchdog (qcom,kpss-wdt WDT_RST at
 *    0x17c10000 + 0x4). If the ~26 s drop to Download mode stops, the
 *    watchdog was the cause.
 *
 * Entered at EL1 with the MMU and D-cache off (arm64 boot protocol), so the
 * stores go straight to memory and the device registers.
 */
	.section .text
	.global _start
_start:
	b	entry			/* code0 */
	.long	0			/* code1 */
	.quad	0x80000			/* text_offset, as the Samsung LK requires */
	.quad	image_end - _start	/* image_size */
	.quad	0xa			/* flags: LE, 4K pages, place anywhere */
	.quad	0			/* res2 */
	.quad	0xecefecef		/* res3, as the Samsung LK requires */
	.quad	0			/* res4 */
	.ascii	"ARM\x64"		/* magic */
	.long	0			/* res5 */

entry:
	ldr	x1, =0x9c000000
	ldr	x2, =(1600 * 2560)
	ldr	w3, =0xffff0000		/* opaque red in a8r8g8b8 */
1:	str	w3, [x1], #4
	subs	x2, x2, #1
	b.ne	1b
	dsb	sy

	ldr	x4, =0x17c10004		/* WDT_RST */
2:	mov	w5, #1
	str	w5, [x4]
	dsb	sy
	ldr	x6, =0x1000000		/* well under a second per pet, bite is ~14 s */
3:	subs	x6, x6, #1
	b.ne	3b
	b	2b

	.ltorg
	/* The BOOT packer reads this string to label the payload. */
	.asciz	"Linux version entry-probe (tabs7linux)"
	.balign	8
image_end:
