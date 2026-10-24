// SPDX-License-Identifier: GPL-2.0-only
/*
 * Novatek NT36523 WQXGA dual-DSI panel driver
 *
 * Target panel: Samsung Galaxy Tab S7 (gts7lwifi), NT36523 PPA957DB1
 *   2560x1600, video mode, dual-DSI, 4 lanes per DSI
 *
 * DSI init sequence: TODO — extract from downstream techpack
 *   arch/arm64/boot/dts/samsung/gts7l/dsi_panel_NT36523_PPA957DB1_wqxga_video.dtsi
 *   (qcom,mdss-dsi-on-command)
 *
 * Reference driver: panel-truly-nt35597.c (dual-DSI probe pattern)
 */

#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/module.h>
#include <linux/of_device.h>
#include <linux/of_graph.h>
#include <linux/regulator/consumer.h>

#include <video/mipi_display.h>

#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>

struct nt36523_cmd {
	u8 cmd;
	u8 data;
};

struct nt36523_desc {
	const struct drm_display_mode *mode;
	unsigned int width_mm;
	unsigned int height_mm;
	const struct nt36523_cmd *on_cmds;
	unsigned int num_on_cmds;
};

struct nt36523 {
	struct drm_panel panel;
	struct mipi_dsi_device *dsi[2];
	const struct nt36523_desc *desc;

	struct regulator *vddio;
	struct gpio_desc *reset_gpio;
	struct gpio_desc *ldo_en_gpio;
	struct gpio_desc *buck_en_gpio;

	bool prepared;
};

static inline struct nt36523 *to_nt36523(struct drm_panel *panel)
{
	return container_of(panel, struct nt36523, panel);
}

static int nt36523_write_cmd(struct nt36523 *ctx, u8 cmd, u8 data)
{
	return mipi_dsi_dcs_write(ctx->dsi[0], cmd, &data, 1);
}

/*
 * NT36523 PPA957DB1 init sequence
 * TODO: fill from downstream qcom,mdss-dsi-on-command
 * These are placeholder no-ops — the panel will not light up until
 * the real sequence is populated.
 */
static const struct nt36523_cmd ppa957db1_on_cmds[] = {
	/*
	 * Placeholder: extract from downstream
	 *   dsi_panel_NT36523_PPA957DB1_wqxga_video.dtsi
	 * Format: { .cmd = REG, .data = VALUE }
	 * Example for NT36xxx page switch:
	 *   { 0xFF, 0x10 },  -- page 0 (user)
	 *   { 0xFB, 0x01 },  -- reload MTP
	 *   ...
	 */
	{ 0xFF, 0x10 },
	{ 0xFB, 0x01 },
};

/*
 * Display timing for NT36523 PPA957DB1, WQXGA 2560x1600 @ 60Hz
 *
 * Clock and porch values are approximate — calibrate against downstream
 * techpack timing parameters:
 *   mdss_dsi_panel_timings (h_back_porch, h_front_porch, h_sync_skew,
 *                            v_back_porch, v_front_porch, v_sync_lines)
 *
 * Each DSI carries the full 2560px horizontal (IC does internal split);
 * so hdisplay here reflects the full panel width.
 */
static const struct drm_display_mode ppa957db1_mode = {
	.clock		= 268316,	/* TODO: confirm from downstream */

	.hdisplay	= 2560,
	.hsync_start	= 2560 + 96,	/* HFP */
	.hsync_end	= 2560 + 96 + 16,	/* HSW */
	.htotal		= 2560 + 96 + 16 + 64,	/* HBP */

	.vdisplay	= 1600,
	.vsync_start	= 1600 + 16,	/* VFP */
	.vsync_end	= 1600 + 16 + 4,	/* VSW */
	.vtotal		= 1600 + 16 + 4 + 16,	/* VBP */

	.type = DRM_MODE_TYPE_DRIVER | DRM_MODE_TYPE_PREFERRED,
};

static const struct nt36523_desc ppa957db1_desc = {
	.mode		= &ppa957db1_mode,
	.width_mm	= 220,	/* 220mm diagonal tablet panel */
	.height_mm	= 138,
	.on_cmds	= ppa957db1_on_cmds,
	.num_on_cmds	= ARRAY_SIZE(ppa957db1_on_cmds),
};

static int nt36523_power_on(struct nt36523 *ctx)
{
	int ret;

	ret = regulator_enable(ctx->vddio);
	if (ret < 0)
		return ret;

	/* buck enable, then LDO, then reset sequence */
	gpiod_set_value_cansleep(ctx->buck_en_gpio, 1);
	usleep_range(2000, 3000);
	gpiod_set_value_cansleep(ctx->ldo_en_gpio, 1);
	usleep_range(2000, 3000);

	/* reset: high → low → high */
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	usleep_range(5000, 6000);
	gpiod_set_value_cansleep(ctx->reset_gpio, 0);
	usleep_range(5000, 6000);
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	msleep(20);

	return 0;
}

static int nt36523_power_off(struct nt36523 *ctx)
{
	gpiod_set_value_cansleep(ctx->reset_gpio, 0);
	usleep_range(2000, 3000);
	gpiod_set_value_cansleep(ctx->ldo_en_gpio, 0);
	usleep_range(2000, 3000);
	gpiod_set_value_cansleep(ctx->buck_en_gpio, 0);

	return regulator_disable(ctx->vddio);
}

static int nt36523_prepare(struct drm_panel *panel)
{
	struct nt36523 *ctx = to_nt36523(panel);
	int ret, i;

	if (ctx->prepared)
		return 0;

	ret = nt36523_power_on(ctx);
	if (ret < 0)
		return ret;

	ctx->dsi[0]->mode_flags |= MIPI_DSI_MODE_LPM;
	ctx->dsi[1]->mode_flags |= MIPI_DSI_MODE_LPM;

	/* send init sequence via primary DSI */
	for (i = 0; i < ctx->desc->num_on_cmds; i++) {
		ret = nt36523_write_cmd(ctx,
					ctx->desc->on_cmds[i].cmd,
					ctx->desc->on_cmds[i].data);
		if (ret < 0) {
			dev_err(panel->dev, "init cmd %d failed: %d\n", i, ret);
			goto err;
		}
	}

	ret = mipi_dsi_dcs_exit_sleep_mode(ctx->dsi[0]);
	if (ret < 0) {
		dev_err(panel->dev, "exit sleep failed: %d\n", ret);
		goto err;
	}
	msleep(120);

	ret = mipi_dsi_dcs_set_display_on(ctx->dsi[0]);
	if (ret < 0) {
		dev_err(panel->dev, "display on failed: %d\n", ret);
		goto err;
	}
	msleep(20);

	ctx->prepared = true;
	return 0;

err:
	nt36523_power_off(ctx);
	return ret;
}

static int nt36523_unprepare(struct drm_panel *panel)
{
	struct nt36523 *ctx = to_nt36523(panel);

	if (!ctx->prepared)
		return 0;

	ctx->dsi[0]->mode_flags &= ~MIPI_DSI_MODE_LPM;
	ctx->dsi[1]->mode_flags &= ~MIPI_DSI_MODE_LPM;

	mipi_dsi_dcs_set_display_off(ctx->dsi[0]);
	msleep(120);
	mipi_dsi_dcs_enter_sleep_mode(ctx->dsi[0]);
	msleep(60);

	nt36523_power_off(ctx);

	ctx->prepared = false;
	return 0;
}

static int nt36523_get_modes(struct drm_panel *panel,
			     struct drm_connector *connector)
{
	struct nt36523 *ctx = to_nt36523(panel);
	struct drm_display_mode *mode;

	mode = drm_mode_duplicate(connector->dev, ctx->desc->mode);
	if (!mode)
		return -ENOMEM;

	drm_mode_set_name(mode);
	drm_mode_probed_add(connector, mode);

	connector->display_info.width_mm = ctx->desc->width_mm;
	connector->display_info.height_mm = ctx->desc->height_mm;

	return 1;
}

static const struct drm_panel_funcs nt36523_funcs = {
	.prepare	= nt36523_prepare,
	.unprepare	= nt36523_unprepare,
	.get_modes	= nt36523_get_modes,
};

static int nt36523_probe(struct mipi_dsi_device *dsi)
{
	struct device *dev = &dsi->dev;
	struct device_node *dsi1_node;
	struct mipi_dsi_host *dsi1_host;
	struct mipi_dsi_device *dsi1;
	struct nt36523 *ctx;
	int ret, i;

	static const struct mipi_dsi_device_info dsi1_info = {
		.type = "nt36523-secondary",
		.channel = 0,
		.node = NULL,
	};

	ctx = devm_kzalloc(dev, sizeof(*ctx), GFP_KERNEL);
	if (!ctx)
		return -ENOMEM;

	ctx->desc = of_device_get_match_data(dev);
	if (!ctx->desc)
		return -ENODEV;

	/* find secondary DSI host via port@1 of the panel node */
	dsi1_node = of_graph_get_remote_node(dev->of_node, 1, -1);
	if (!dsi1_node) {
		dev_err(dev, "failed to get secondary DSI node\n");
		return -ENODEV;
	}

	dsi1_host = of_find_mipi_dsi_host_by_node(dsi1_node);
	of_node_put(dsi1_node);
	if (!dsi1_host)
		return dev_err_probe(dev, -EPROBE_DEFER,
				     "secondary DSI host not found\n");

	dsi1 = mipi_dsi_device_register_full(dsi1_host, &dsi1_info);
	if (IS_ERR(dsi1))
		return dev_err_probe(dev, PTR_ERR(dsi1),
				     "failed to register secondary DSI\n");

	ctx->dsi[0] = dsi;
	ctx->dsi[1] = dsi1;
	mipi_dsi_set_drvdata(dsi, ctx);

	ctx->vddio = devm_regulator_get(dev, "vddio");
	if (IS_ERR(ctx->vddio)) {
		ret = PTR_ERR(ctx->vddio);
		goto err_unreg;
	}

	ctx->reset_gpio = devm_gpiod_get(dev, "reset", GPIOD_OUT_LOW);
	if (IS_ERR(ctx->reset_gpio)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->reset_gpio),
				    "failed to get reset gpio\n");
		goto err_unreg;
	}

	ctx->ldo_en_gpio = devm_gpiod_get(dev, "ldo-en", GPIOD_OUT_LOW);
	if (IS_ERR(ctx->ldo_en_gpio)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->ldo_en_gpio),
				    "failed to get ldo-en gpio\n");
		goto err_unreg;
	}

	ctx->buck_en_gpio = devm_gpiod_get(dev, "buck-en", GPIOD_OUT_LOW);
	if (IS_ERR(ctx->buck_en_gpio)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->buck_en_gpio),
				    "failed to get buck-en gpio\n");
		goto err_unreg;
	}

	drm_panel_init(&ctx->panel, dev, &nt36523_funcs, DRM_MODE_CONNECTOR_DSI);
	drm_panel_add(&ctx->panel);

	for (i = 0; i < ARRAY_SIZE(ctx->dsi); i++) {
		ctx->dsi[i]->lanes = 4;
		ctx->dsi[i]->format = MIPI_DSI_FMT_RGB888;
		ctx->dsi[i]->mode_flags = MIPI_DSI_MODE_VIDEO |
					   MIPI_DSI_MODE_VIDEO_HSE |
					   MIPI_DSI_CLOCK_NON_CONTINUOUS;
		ret = mipi_dsi_attach(ctx->dsi[i]);
		if (ret < 0) {
			dev_err(dev, "failed to attach DSI%d: %d\n", i, ret);
			goto err_panel;
		}
	}

	return 0;

err_panel:
	drm_panel_remove(&ctx->panel);
err_unreg:
	mipi_dsi_device_unregister(dsi1);
	return ret;
}

static void nt36523_remove(struct mipi_dsi_device *dsi)
{
	struct nt36523 *ctx = mipi_dsi_get_drvdata(dsi);

	if (ctx->dsi[0])
		mipi_dsi_detach(ctx->dsi[0]);
	if (ctx->dsi[1]) {
		mipi_dsi_detach(ctx->dsi[1]);
		mipi_dsi_device_unregister(ctx->dsi[1]);
	}

	drm_panel_remove(&ctx->panel);
}

static void nt36523_shutdown(struct mipi_dsi_device *dsi)
{
	struct nt36523 *ctx = mipi_dsi_get_drvdata(dsi);

	drm_panel_unprepare(&ctx->panel);
}

static const struct of_device_id nt36523_of_match[] = {
	{
		.compatible = "samsung,gts7l-nt36523",
		.data = &ppa957db1_desc,
	},
	{}
};
MODULE_DEVICE_TABLE(of, nt36523_of_match);

static struct mipi_dsi_driver nt36523_driver = {
	.driver = {
		.name = "panel-novatek-nt36523",
		.of_match_table = nt36523_of_match,
	},
	.probe		= nt36523_probe,
	.remove		= nt36523_remove,
	.shutdown	= nt36523_shutdown,
};
module_mipi_dsi_driver(nt36523_driver);

MODULE_AUTHOR("gts7lwifi mainline contributors");
MODULE_DESCRIPTION("Novatek NT36523 WQXGA dual-DSI panel driver");
MODULE_LICENSE("GPL");
