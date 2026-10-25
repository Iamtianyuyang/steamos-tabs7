// SPDX-License-Identifier: GPL-2.0-only
/*
 * Novatek NT36523 WQXGA dual-DSI panel driver
 *
 * Target panel: Samsung Galaxy Tab S7 (gts7lwifi), NT36523 PPA957DB1
 *   2560x1600, video mode, dual-DSI, 3 C-PHY trios per DSI
 *
 * The init sequence, timings and reset delays live in the generated header
 * panel-novatek-nt36523-gts7lwifi.h, produced by scripts/steamos/emit-panel-seq.py
 * from the vendor 4.19 panel properties (see
 * hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json). Do not hand-edit
 * that data; regenerate it instead.
 *
 * Reference driver: panel-truly-nt35597.c (dual-DSI probe pattern). Mainline
 * also has panel-novatek-nt36523.c for other NT36523 panels; this one is kept
 * separate because the SM-T870 panel runs on C-PHY with a vendor sequence.
 */

#include <linux/backlight.h>
#include <linux/delay.h>
#include <linux/gpio/consumer.h>
#include <linux/interrupt.h>
#include <linux/module.h>
#include <linux/of.h>
#include <linux/of_graph.h>
#include <linux/regulator/consumer.h>

#include <video/mipi_display.h>

#include <drm/drm_mipi_dsi.h>
#include <drm/drm_modes.h>
#include <drm/drm_panel.h>

/* One downstream init command: DCS payload plus the vendor wait after it. */
struct nt36523_init_cmd {
	u8 len;
	u8 delay_ms;
	u8 payload[16];
};

struct nt36523_desc {
	const struct drm_display_mode *mode;
	unsigned int width_mm;
	unsigned int height_mm;
	const struct nt36523_init_cmd *init;
	unsigned int num_init;
};

/* Generated panel data: ppa957db1_init[], ppa957db1_mode, reset delays. */
#include "panel-novatek-nt36523-gts7lwifi.h"

struct nt36523 {
	struct drm_panel panel;
	struct mipi_dsi_device *dsi[2];
	const struct nt36523_desc *desc;

	struct regulator *vddio;
	struct regulator *buck;
	struct gpio_desc *reset_gpio;
	struct backlight_device *backlight;

	struct gpio_desc *esd_gpio;
	struct gpio_desc *ub_con_det_gpio;
	struct work_struct esd_work;
	struct mutex lock;	/* serialises prepare/unprepare against ESD recovery */
	unsigned int esd_recoveries;

	bool prepared;
	bool enabled;
};

static inline struct nt36523 *to_nt36523(struct drm_panel *panel)
{
	return container_of(panel, struct nt36523, panel);
}

static int nt36523_send_init_sequence(struct nt36523 *ctx)
{
	const struct nt36523_init_cmd *cmd;
	unsigned int index, link;
	int ret;

	for (index = 0; index < ctx->desc->num_init; index++) {
		cmd = &ctx->desc->init[index];

		/* Bonding video does not broadcast commands to the second DDIC. */
		for (link = 0; link < ARRAY_SIZE(ctx->dsi); link++) {
			ret = mipi_dsi_dcs_write_buffer(ctx->dsi[link], cmd->payload, cmd->len);
			if (ret < 0) {
				dev_err(ctx->panel.dev, "DSI%u init command %u failed: %d\n",
					link, index, ret);
				return ret;
			}
		}

		if (cmd->delay_ms)
			msleep(cmd->delay_ms);
	}

	return 0;
}

static const struct nt36523_desc ppa957db1_desc = {
	.mode		= &ppa957db1_mode,
	.width_mm	= PPA957DB1_PHYSICAL_WIDTH_MM,
	.height_mm	= PPA957DB1_PHYSICAL_HEIGHT_MM,
	.init		= ppa957db1_init,
	.num_init	= ARRAY_SIZE(ppa957db1_init),
};

static int nt36523_power_on(struct nt36523 *ctx)
{
	int ret;

	/*
	 * Vendor supply order: vddlcd (1.8 V, fixed regulator on TLMM135) first,
	 * then the buck (TLMM93) with a 10 ms boost time before the reset
	 * sequence. qcom,panel-supply-entry@1 carries post-on-sleep = 10.
	 */
	ret = regulator_enable(ctx->vddio);
	if (ret < 0)
		return ret;

	ret = regulator_enable(ctx->buck);
	if (ret < 0) {
		regulator_disable(ctx->vddio);
		return ret;
	}

	msleep(10);

	/*
	 * Vendor reset sequence uses physical levels: low, high, low, high,
	 * each held for 12 ms. The active-low GPIO descriptor takes logical
	 * reset states, so assert with 1 and release with 0.
	 */
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	msleep(PPA957DB1_RESET_DELAY_MS);
	gpiod_set_value_cansleep(ctx->reset_gpio, 0);
	msleep(PPA957DB1_RESET_DELAY_MS);
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);
	msleep(PPA957DB1_RESET_DELAY_MS);
	gpiod_set_value_cansleep(ctx->reset_gpio, 0);
	msleep(PPA957DB1_RESET_DELAY_MS);

	return 0;
}

static int nt36523_power_off(struct nt36523 *ctx)
{
	gpiod_set_value_cansleep(ctx->reset_gpio, 1);

	/* 10 ms discharge time from qcom,panel-supply-entry@1 */
	regulator_disable(ctx->buck);
	msleep(10);

	return regulator_disable(ctx->vddio);
}

static int nt36523_prepare(struct drm_panel *panel)
{
	struct nt36523 *ctx = to_nt36523(panel);
	unsigned int link;
	int ret;

	mutex_lock(&ctx->lock);
	if (ctx->prepared) {
		mutex_unlock(&ctx->lock);
		return 0;
	}

	ret = nt36523_power_on(ctx);
	if (ret < 0) {
		mutex_unlock(&ctx->lock);
		return ret;
	}

	ctx->dsi[0]->mode_flags |= MIPI_DSI_MODE_LPM;
	ctx->dsi[1]->mode_flags |= MIPI_DSI_MODE_LPM;

	/*
	 * The sequence contains sleep-out (0x11). The vendor driver sends
	 * display-on (0x29) separately after the initialization sequence.
	 */
	ret = nt36523_send_init_sequence(ctx);
	if (ret < 0)
		goto err;

	for (link = 0; link < ARRAY_SIZE(ctx->dsi); link++) {
		ret = mipi_dsi_dcs_set_display_on(ctx->dsi[link]);
		if (ret < 0)
			goto err;
	}

	msleep(20);

	WRITE_ONCE(ctx->prepared, true);
	mutex_unlock(&ctx->lock);
	return 0;

err:
	nt36523_power_off(ctx);
	mutex_unlock(&ctx->lock);
	return ret;
}

static int nt36523_unprepare(struct drm_panel *panel)
{
	struct nt36523 *ctx = to_nt36523(panel);
	unsigned int link;

	cancel_work_sync(&ctx->esd_work);
	mutex_lock(&ctx->lock);
	if (!ctx->prepared) {
		mutex_unlock(&ctx->lock);
		return 0;
	}
	WRITE_ONCE(ctx->prepared, false);

	ctx->dsi[0]->mode_flags &= ~MIPI_DSI_MODE_LPM;
	ctx->dsi[1]->mode_flags &= ~MIPI_DSI_MODE_LPM;

	for (link = 0; link < ARRAY_SIZE(ctx->dsi); link++)
		mipi_dsi_dcs_set_display_off(ctx->dsi[link]);
	msleep(120);
	for (link = 0; link < ARRAY_SIZE(ctx->dsi); link++)
		mipi_dsi_dcs_enter_sleep_mode(ctx->dsi[link]);
	msleep(60);

	nt36523_power_off(ctx);

	mutex_unlock(&ctx->lock);
	return 0;
}

static int nt36523_enable(struct drm_panel *panel)
{
	struct nt36523 *ctx = to_nt36523(panel);

	/* The video path is ready; reset/init edges are not panel faults. */
	WRITE_ONCE(ctx->enabled, true);
	return 0;
}

static int nt36523_disable(struct drm_panel *panel)
{
	struct nt36523 *ctx = to_nt36523(panel);

	WRITE_ONCE(ctx->enabled, false);
	cancel_work_sync(&ctx->esd_work);
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
	.enable		= nt36523_enable,
	.disable	= nt36523_disable,
	.unprepare	= nt36523_unprepare,
	.get_modes	= nt36523_get_modes,
};

/*
 * ESD handling, from the downstream panel node:
 *   samsung,esd-irq-gpio1 = <&tlmm 66 0>  (qcom,...,irq-trigger1 = "rising")
 *   samsung,ub-con-det    = <&tlmm 46 0>
 * The downstream driver masks ESD IRQs during reset/init. Here transitions
 * outside the enabled/prepared state and during recovery must be ignored.
 */
static void nt36523_esd_work(struct work_struct *work)
{
	struct nt36523 *ctx = container_of(work, struct nt36523, esd_work);
	unsigned int link;
	int ret;

	mutex_lock(&ctx->lock);
	if (!ctx->prepared || !READ_ONCE(ctx->enabled)) {
		mutex_unlock(&ctx->lock);
		return;
	}

	dev_warn(ctx->panel.dev, "ESD detected, recovering\n");

	/* Ignore the edges caused by our own power/reset sequence. */
	WRITE_ONCE(ctx->prepared, false);
	nt36523_power_off(ctx);
	ret = nt36523_power_on(ctx);
	if (ret)
		goto out;
	ret = nt36523_send_init_sequence(ctx);
	for (link = 0; !ret && link < ARRAY_SIZE(ctx->dsi); link++)
		ret = mipi_dsi_dcs_set_display_on(ctx->dsi[link]);
	if (!ret) {
		msleep(20);
		/* Init writes brightness zero; recover the user's current level. */
		ret = backlight_update_status(ctx->backlight);
	}
	if (ret) {
		nt36523_power_off(ctx);
	} else {
		WRITE_ONCE(ctx->prepared, true);
		ctx->esd_recoveries++;
		dev_info(ctx->panel.dev, "ESD recovery %u complete\n", ctx->esd_recoveries);
	}

out:
	if (ret)
		dev_err(ctx->panel.dev, "ESD recovery failed: %d\n", ret);
	mutex_unlock(&ctx->lock);
}

static irqreturn_t nt36523_esd_irq(int irq, void *data)
{
	struct nt36523 *ctx = data;

	if (READ_ONCE(ctx->prepared) && READ_ONCE(ctx->enabled))
		schedule_work(&ctx->esd_work);

	return IRQ_HANDLED;
}

static int nt36523_request_esd_irq(struct nt36523 *ctx, struct gpio_desc *gpio,
				   const char *name)
{
	int irq, ret;

	if (!gpio)
		return 0;

	irq = gpiod_to_irq(gpio);
	if (irq < 0)
		return dev_err_probe(ctx->panel.dev, irq, "%s has no interrupt\n", name);

	ret = devm_request_threaded_irq(ctx->panel.dev, irq, NULL, nt36523_esd_irq,
					IRQF_TRIGGER_RISING | IRQF_ONESHOT,
					dev_name(ctx->panel.dev), ctx);
	if (ret)
		return dev_err_probe(ctx->panel.dev, ret, "failed to request %s\n", name);

	return 0;
}

/*
 * Brightness uses the vendor DCS path: long write 0x51, high byte first,
 * levels 4..462 (qcom,mdss-dsi-bl-min-level / -max-level). The panel profile in
 * hardware/samsung/gts7lwifi/panel/ carries the same numbers.
 */
static int nt36523_bl_update_status(struct backlight_device *bl)
{
	struct nt36523 *ctx = bl_get_data(bl);
	unsigned int link;
	int ret;

	for (link = 0; link < ARRAY_SIZE(ctx->dsi); link++) {
		ret = mipi_dsi_dcs_set_display_brightness_large(ctx->dsi[link],
							    backlight_get_brightness(bl));
		if (ret < 0)
			return ret;
	}
	return 0;
}

static const struct backlight_ops nt36523_bl_ops = {
	.update_status	= nt36523_bl_update_status,
};

static struct backlight_device *nt36523_register_backlight(struct nt36523 *ctx)
{
	struct backlight_properties props = {
		.type		= BACKLIGHT_RAW,
		.max_brightness	= PPA957DB1_BRIGHTNESS_MAX,
		.brightness	= PPA957DB1_BRIGHTNESS_MAX,
	};

	return devm_backlight_device_register(ctx->panel.dev, "nt36523", ctx->panel.dev,
					      ctx, &nt36523_bl_ops, &props);
}

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

	/*
	 * Allocate the panel first: the backlight and ESD interrupt below are
	 * devres-managed through ctx->panel.dev, so it must already be set.
	 */
	ctx = devm_drm_panel_alloc(dev, struct nt36523, panel, &nt36523_funcs,
				   DRM_MODE_CONNECTOR_DSI);
	if (IS_ERR(ctx))
		return PTR_ERR(ctx);

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

	/*
	 * vddio is the panel's 1.8 V rail (vendor "vddlcd", a fixed regulator on
	 * TLMM135); buck is the second fixed regulator on TLMM93. Both are simple
	 * GPIO switches modelled as regulator-fixed in the board DTS.
	 */
	ctx->vddio = devm_regulator_get(dev, "vddio");
	if (IS_ERR(ctx->vddio)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->vddio),
				    "failed to get vddio supply\n");
		goto err_unreg;
	}

	ctx->buck = devm_regulator_get(dev, "buck");
	if (IS_ERR(ctx->buck)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->buck),
				    "failed to get buck supply\n");
		goto err_unreg;
	}

	ctx->reset_gpio = devm_gpiod_get(dev, "reset", GPIOD_OUT_HIGH);
	if (IS_ERR(ctx->reset_gpio)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->reset_gpio),
				    "failed to get reset gpio\n");
		goto err_unreg;
	}

	mutex_init(&ctx->lock);
	INIT_WORK(&ctx->esd_work, nt36523_esd_work);

	/* Optional: without the property there is no ESD detection. */
	ctx->esd_gpio = devm_gpiod_get_optional(dev, "esd", GPIOD_IN);
	if (IS_ERR(ctx->esd_gpio)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->esd_gpio), "failed to get esd gpio\n");
		goto err_unreg;
	}

	ctx->ub_con_det_gpio = devm_gpiod_get_optional(dev, "ub-con-det", GPIOD_IN);
	if (IS_ERR(ctx->ub_con_det_gpio)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->ub_con_det_gpio),
				    "failed to get ub-con-det gpio\n");
		goto err_unreg;
	}

	ctx->backlight = nt36523_register_backlight(ctx);
	if (IS_ERR(ctx->backlight)) {
		ret = dev_err_probe(dev, PTR_ERR(ctx->backlight),
				    "failed to register backlight\n");
		goto err_unreg;
	}
	ctx->panel.backlight = ctx->backlight;

	ret = nt36523_request_esd_irq(ctx, ctx->esd_gpio, "esd");
	if (ret)
		goto err_unreg;
	ret = nt36523_request_esd_irq(ctx, ctx->ub_con_det_gpio, "ub-con-det");
	if (ret)
		goto err_unreg;

	/* prepare() sends the init sequence, so the DSI hosts must be up first. */
	ctx->panel.prepare_prev_first = true;
	drm_panel_add(&ctx->panel);

	for (i = 0; i < ARRAY_SIZE(ctx->dsi); i++) {
		/*
		 * 3 C-PHY trios per link (qcom,panel-cphy-mode in the vendor DTS),
		 * 24 bpp RGB and burst video mode with BLLP power saving.
		 */
		ctx->dsi[i]->lanes = 3;
		ctx->dsi[i]->format = MIPI_DSI_FMT_RGB888;
		ctx->dsi[i]->mode_flags = MIPI_DSI_MODE_VIDEO |
					   MIPI_DSI_MODE_VIDEO_BURST |
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

	WRITE_ONCE(ctx->enabled, false);
	cancel_work_sync(&ctx->esd_work);
	mutex_destroy(&ctx->lock);

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

	drm_panel_disable(&ctx->panel);
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
		.name = "panel-novatek-nt36523-gts7lwifi",
		.of_match_table = nt36523_of_match,
	},
	.probe		= nt36523_probe,
	.remove		= nt36523_remove,
	.shutdown	= nt36523_shutdown,
};
module_mipi_dsi_driver(nt36523_driver);

MODULE_AUTHOR("gts7lwifi mainline contributors");
MODULE_DESCRIPTION("Novatek NT36523 WQXGA dual-DSI panel driver (SM-T870 PPA957DB1)");
MODULE_LICENSE("GPL");
