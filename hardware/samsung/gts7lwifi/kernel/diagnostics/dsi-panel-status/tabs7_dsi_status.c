// SPDX-License-Identifier: GPL-2.0-only
/* Explicitly requested DCS reads on the active mainline SM-T870 panel. */
#include <linux/device.h>
#include <linux/module.h>
#include <linux/platform_device.h>
#include <drm/drm_mipi_dsi.h>
#include <video/mipi_display.h>

static bool display_active;
module_param(display_active, bool, 0400);
static int power_mode = -1;
module_param(power_mode, int, 0400);
static int read_error;
module_param(read_error, int, 0400);

static int __init panel_status_init(void)
{
	struct device *controller, *child;
	struct mipi_dsi_device *dsi;
	u8 value = 0, id[3] = { 0 }, brightness[2] = { 0 };
	int ret;

	if (!display_active)
		return -EINVAL;
	controller = bus_find_device_by_name(&platform_bus_type, NULL, "ae94000.dsi");
	if (!controller)
		return -ENODEV;
	child = device_find_child_by_name(controller, "ae94000.dsi.0");
	put_device(controller);
	if (!child)
		return -ENODEV;
	dsi = container_of(child, struct mipi_dsi_device, dev);
	ret = mipi_dsi_dcs_read(dsi, MIPI_DCS_GET_POWER_MODE, &value, sizeof(value));
	read_error = ret == sizeof(value) ? 0 : (ret < 0 ? ret : -ENODATA);
	if (!read_error)
		power_mode = value;
	pr_info("tabs7_dsi_status: power read ret=%d mode=%#x\n", ret, value);
	ret = mipi_dsi_dcs_read(dsi, MIPI_DCS_GET_DISPLAY_ID, id, sizeof(id));
	pr_info("tabs7_dsi_status: display ID read ret=%d bytes=%*phN\n", ret, 3, id);
	ret = mipi_dsi_dcs_read(dsi, MIPI_DCS_GET_DISPLAY_BRIGHTNESS,
			       brightness, sizeof(brightness));
	pr_info("tabs7_dsi_status: brightness read ret=%d bytes=%*phN\n",
		ret, 2, brightness);
	put_device(child);
	return 0;
}

static void __exit panel_status_exit(void)
{
}

module_init(panel_status_init);
module_exit(panel_status_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Development-only active SM-T870 DCS power, ID and brightness readback");
