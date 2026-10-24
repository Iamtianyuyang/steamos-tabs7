// SPDX-License-Identifier: GPL-2.0-only
/*
 * Move the bootloader-provided boot data from the vendor DTB into the
 * embedded mainline DTB and place the embedded Image for entry.
 *
 * Runs with the MMU off, so every access is Device memory: the build uses
 * -mstrict-align and the copies below stay word aligned.
 *
 * Failures paint the splash so the cause is visible without a console:
 *   yellow  - the bootloader DTB is not a valid FDT
 *   magenta - the embedded mainline DTB cannot be opened
 *   cyan    - a property could not be copied
 * A green bar across the top means the shim is about to enter the kernel.
 */
#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "libfdt.h"

#define SZ_64K		0x10000UL
#define SZ_2M		0x200000UL
#define DTB_ROOM	0x100000UL	/* 1 MiB for the patched mainline DTB */

/* Bootloader splash, see the simple-framebuffer node in the board DTS. */
#define FB_BASE		0x9c000000UL
#define FB_WIDTH	1600UL
#define FB_HEIGHT	2560UL

#define YELLOW		0xffffff00u
#define MAGENTA		0xffff00ffu
#define CYAN		0xff00ffffu
#define GREEN		0xff00ff00u

extern char _start[], __dtb_start[], __kernel_start[];

struct handoff {
	uint64_t kernel;
	uint64_t dtb;
};

static void paint(uint32_t colour, unsigned long rows)
{
	volatile uint32_t *fb = (volatile uint32_t *)FB_BASE;
	unsigned long i;

	for (i = 0; i < FB_WIDTH * rows; i++)
		fb[i] = colour;
	__asm__ volatile("dsb sy" ::: "memory");
}

static void __attribute__((noreturn)) fail(uint32_t colour)
{
	paint(colour, FB_HEIGHT);
	for (;;)
		__asm__ volatile("wfe");
}

static uint64_t align_up(uint64_t value, uint64_t align)
{
	return (value + align - 1) & ~(align - 1);
}

static uint64_t read_le64(const void *where)
{
	const uint8_t *p = where;
	uint64_t value = 0;
	int i;

	for (i = 7; i >= 0; i--)
		value = (value << 8) | p[i];
	return value;
}

/* Word copy that handles overlap; both ends are 8-byte aligned here. */
static void move_words(uint64_t *dst, const uint64_t *src, uint64_t bytes)
{
	uint64_t words = (bytes + 7) / 8, i;

	if (dst < src) {
		for (i = 0; i < words; i++)
			dst[i] = src[i];
	} else if (dst > src) {
		for (i = words; i > 0; i--)
			dst[i - 1] = src[i - 1];
	}
}

static void copy_prop(const void *from, void *to, const char *path, const char *name,
		      int required)
{
	int src = fdt_path_offset(from, path);
	int dst = fdt_path_offset(to, path);
	const void *value;
	int len;

	if (src < 0 || dst < 0) {
		if (required)
			fail(CYAN);
		return;
	}
	value = fdt_getprop(from, src, name, &len);
	if (!value) {
		if (required)
			fail(CYAN);
		return;
	}
	if (fdt_setprop(to, dst, name, value, len))
		fail(CYAN);
}

struct handoff shim_main(const void *bootloader_dtb)
{
	uint64_t base = (uint64_t)_start;
	uint64_t image_size, kernel, dtb;
	void *patched;

	if (fdt_check_header(bootloader_dtb))
		fail(YELLOW);

	/*
	 * The arm64 boot protocol wants the Image at a 2 MiB aligned address.
	 * It sits at a 2 MiB offset inside the payload, but the bootloader loads
	 * the payload at base + text_offset, so move it when needed.
	 */
	image_size = read_le64(__kernel_start + 16);
	kernel = align_up(base + SZ_2M, SZ_2M);
	move_words((uint64_t *)kernel, (const uint64_t *)__kernel_start, image_size);

	dtb = align_up(kernel + image_size, SZ_64K);
	patched = (void *)dtb;
	if (fdt_open_into(__dtb_start, patched, DTB_ROOM))
		fail(MAGENTA);

	/* What the bootloader computed for this boot. */
	copy_prop(bootloader_dtb, patched, "/chosen", "bootargs", 1);
	copy_prop(bootloader_dtb, patched, "/chosen", "linux,initrd-start", 1);
	copy_prop(bootloader_dtb, patched, "/chosen", "linux,initrd-end", 1);
	copy_prop(bootloader_dtb, patched, "/chosen", "kaslr-seed", 0);
	copy_prop(bootloader_dtb, patched, "/memory", "reg", 1);

	if (fdt_pack(patched))
		fail(CYAN);

	paint(GREEN, 64);
	return (struct handoff){ .kernel = kernel, .dtb = dtb };
}
