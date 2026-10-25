// SPDX-License-Identifier: GPL-2.0-only
/* Development-only SM8250 register comparison. No register writes. */
#include <linux/init.h>
#include <linux/io.h>
#include <linux/kobject.h>
#include <linux/module.h>
#include <linux/slab.h>
#include <linux/sysfs.h>
#include <linux/version.h>

/* Loading this probe is an explicit assertion that the display is powered. */
static bool display_active;
module_param(display_active, bool, 0400);
MODULE_PARM_DESC(display_active, "Confirm the display is powered before reading MMIO");

struct register_range {
	u32 start;
	u32 bytes;
};

/* Physical addresses, independent of the vendor/mainline driver's base offset.
 * DPU offsets: dpu_6_0_sm8250.h. DSI/PHY: sm8250.dtsi.
 * Clock offsets: gcc-sm8250.c / dispcc-sm8250.c.
 */
static const struct register_range ranges[] = {
	{ 0x0010b000, 0x004 }, /* GCC_DISP_BCR */
	{ 0x0010b034, 0x008 }, /* HF/SF AXI clocks */
	{ 0x0010b044, 0x004 }, /* display XO clock */
	{ 0x0af00000, 0x040 }, /* DISP_CC PLL0 */
	{ 0x0af02000, 0x2e0 }, /* DISP_CC branches and root clocks */
	{ 0x0af03000, 0x004 }, /* MDSS_GDSCR */
	{ 0x0ae00000, 0x030 }, /* MDSS top */
	{ 0x0ae01000, 0x494 }, /* MDP top, including split configuration */
	{ 0x0ae02000, 0x1e0 }, /* CTL0 */
	{ 0x0ae02200, 0x1e0 }, /* CTL1 */
	{ 0x0ae05000, 0x1f8 }, /* VIG0 */
	{ 0x0ae07000, 0x1f8 }, /* VIG1 */
	{ 0x0ae45000, 0x320 }, /* LM0 */
	{ 0x0ae46000, 0x320 }, /* LM1 */
	{ 0x0ae6b800, 0x2c0 }, /* INTF1 */
	{ 0x0ae6c000, 0x2c0 }, /* INTF2 */
	{ 0x0ae94000, 0x400 }, /* DSI0, including HW version */
	{ 0x0ae94400, 0x200 }, /* DSI0 PHY common */
	{ 0x0ae94900, 0x260 }, /* DSI0 PHY PLL */
	{ 0x0ae96000, 0x400 }, /* DSI1 */
	{ 0x0ae96400, 0x200 }, /* DSI1 PHY common */
	{ 0x0ae96900, 0x260 }, /* DSI1 PHY PLL */
};

/* Little-endian file format: magic, format version, count, then address/value
 * pairs. Reads serve the immutable capture, never live hardware accesses.
 */
struct register_value {
	__le32 address;
	__le32 value;
};

struct snapshot {
	__le32 magic;
	__le32 version;
	__le32 count;
	struct register_value values[];
};

static struct snapshot *capture;
static struct kobject *snapshot_kobj;
static size_t capture_bytes;

static ssize_t snapshot_read(struct file *file, struct kobject *kobj,
#if LINUX_VERSION_CODE >= KERNEL_VERSION(6, 18, 0)
			     const struct bin_attribute *attr, char *buf,
#else
			     struct bin_attribute *attr, char *buf,
#endif
			     loff_t offset, size_t count)
{
	if (offset < 0)
		return -EINVAL;
	if (offset >= capture_bytes)
		return 0;
	count = min_t(size_t, count, capture_bytes - offset);
	memcpy(buf, (const char *)capture + offset, count);
	return count;
}

static struct bin_attribute snapshot_attr = {
	.attr = { .name = "registers", .mode = 0400 },
	.read = snapshot_read,
};

static int __init snapshot_init(void)
{
	unsigned int i, offset, index = 0, count = 0;
	void __iomem *mapping;
	int ret;

	if (!display_active)
		return -EINVAL;

	for (i = 0; i < ARRAY_SIZE(ranges); i++)
		count += ranges[i].bytes / sizeof(u32);
	capture_bytes = sizeof(*capture) + count * sizeof(capture->values[0]);
	capture = kzalloc(capture_bytes, GFP_KERNEL);
	if (!capture)
		return -ENOMEM;
	capture->magic = cpu_to_le32(0x37534254); /* TBS7 */
	capture->version = cpu_to_le32(1);
	capture->count = cpu_to_le32(count);

	for (i = 0; i < ARRAY_SIZE(ranges); i++) {
		mapping = ioremap(ranges[i].start, ranges[i].bytes);
		if (!mapping) {
			ret = -ENOMEM;
			goto free_capture;
		}
		for (offset = 0; offset < ranges[i].bytes; offset += sizeof(u32)) {
			capture->values[index].address = cpu_to_le32(ranges[i].start + offset);
			capture->values[index].value = cpu_to_le32(readl(mapping + offset));
			index++;
		}
		iounmap(mapping);
	}

	snapshot_kobj = kobject_create_and_add("tabs7_display_snapshot", kernel_kobj);
	if (!snapshot_kobj) {
		ret = -ENOMEM;
		goto free_capture;
	}
	snapshot_attr.size = capture_bytes;
	ret = sysfs_create_bin_file(snapshot_kobj, &snapshot_attr);
	if (ret) {
		kobject_put(snapshot_kobj);
		goto free_capture;
	}
	pr_info("tabs7_display_snapshot: captured %u register values\n", count);
	return 0;

free_capture:
	kfree(capture);
	return ret;
}

static void __exit snapshot_exit(void)
{
	sysfs_remove_bin_file(snapshot_kobj, &snapshot_attr);
	kobject_put(snapshot_kobj);
	kfree(capture);
}

module_init(snapshot_init);
module_exit(snapshot_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Read-only SM-T870 active display register snapshot for kernel comparisons");
