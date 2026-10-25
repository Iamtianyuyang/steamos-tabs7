// SPDX-License-Identifier: GPL-2.0-only
/* One-variable display bring-up probe: vote for GCC_DISP_XO_CLK via CCF. */
#include <linux/clk.h>
#include <linux/err.h>
#include <linux/module.h>
#include <linux/of.h>
#include <dt-bindings/clock/qcom,gcc-sm8250.h>

static struct clk *display_xo;

static int __init display_xo_init(void)
{
	struct of_phandle_args spec = { .args_count = 1 };
	int ret;

	spec.np = of_find_compatible_node(NULL, NULL, "qcom,gcc-sm8250");
	if (!spec.np)
		return -ENODEV;
	spec.args[0] = GCC_DISP_XO_CLK;
	display_xo = of_clk_get_from_provider(&spec);
	of_node_put(spec.np);
	if (IS_ERR(display_xo))
		return PTR_ERR(display_xo);
	ret = clk_prepare_enable(display_xo);
	if (ret) {
		clk_put(display_xo);
		return ret;
	}
	pr_info("tabs7_disp_xo: holding GCC_DISP_XO_CLK until probe unload\n");
	return 0;
}

static void __exit display_xo_exit(void)
{
	clk_disable_unprepare(display_xo);
	clk_put(display_xo);
}

module_init(display_xo_init);
module_exit(display_xo_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("Temporary SM8250 display XO clock vote for controlled diagnosis");
