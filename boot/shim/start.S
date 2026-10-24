/* SPDX-License-Identifier: GPL-2.0-only */
/*
 * Boot shim entry. The Samsung bootloader only accepts the vendor DTB (a
 * mainline DTB stops at "Custom Binary(boot) Blocked By OEM Lock") but runs any
 * kernel payload, so BOOT carries this shim as its "kernel": it takes the
 * vendor DTB the bootloader prepared, moves what the bootloader filled in into
 * the embedded mainline DTB, and enters the embedded mainline Image.
 *
 * Entered at EL1 with the MMU and D-cache off, x0 = bootloader DTB.
 */
	.section .head, "ax"
	.global _start
_start:
	b	entry			/* code0 */
	.long	0			/* code1 */
	.quad	0x80000			/* text_offset, as the Samsung LK requires */
	.quad	SHIM_RESERVE		/* image_size: the whole reserved window */
	.quad	0xa			/* flags: LE, 4K pages, place anywhere */
	.quad	0			/* res2 */
	.quad	0xecefecef		/* res3, as the Samsung LK requires */
	.quad	0			/* res4 */
	.ascii	"ARM\x64"		/* magic */
	.long	0			/* res5 */

	.text
entry:
	mov	x19, x0
	adr	x1, _start
	ldr	x2, =(SHIM_RESERVE - 0x1000)
	add	x2, x1, x2
	and	x2, x2, #~0xf
	mov	sp, x2

	adrp	x3, __bss_start
	add	x3, x3, :lo12:__bss_start
	adrp	x4, __bss_end
	add	x4, x4, :lo12:__bss_end
1:	cmp	x3, x4
	b.hs	2f
	str	xzr, [x3], #8
	b	1b

2:	mov	x0, x19
	bl	shim_main		/* returns x0 = kernel entry, x1 = DTB */
	mov	x20, x0
	mov	x21, x1

	/* The Image may have been moved: drop stale instructions. */
	ic	iallu
	dsb	sy
	isb

	mov	x0, x21
	mov	x1, xzr
	mov	x2, xzr
	mov	x3, xzr
	br	x20
	.ltorg
