"""Run the actual PHY enable/bias callbacks with fresh, bonded MMIO banks."""
from pathlib import Path
import re
import shutil
import subprocess
import xml.etree.ElementTree as ET

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'


@pytest.mark.parametrize('cphy', [False, True])
def test_first_phy_enable_programs_output_muxes(tmp_path, cphy):
    compiler = shutil.which('cc')
    if compiler is None:
        pytest.skip('host C compiler unavailable')
    driver = (KERNEL / 'drivers/gpu/drm/msm/dsi/phy/dsi_phy_7nm.c').read_text()
    functions = []
    for signature in ('static void dsi_pll_enable_pll_bias(', 'static int dsi_7nm_phy_enable('):
        start = driver.index(signature + ('struct dsi_pll_7nm *pll)\n{' if 'bias' in signature else 'struct msm_dsi_phy *phy,'))
        end = driver.index('\n}\n', start) + 3
        functions.append(driver[start:end])
    # Derive the register addresses and single-bit fields from the real XML,
    # so this test does not depend on an ignored kernel build directory.
    ns = {'r': 'http://nouveau.freedesktop.org/'}
    xml = ET.parse(KERNEL / 'drivers/gpu/drm/msm/registers/display/dsi_phy_7nm.xml')
    defines = []
    for domain in xml.findall('r:domain', ns):
        for reg in domain.findall('r:reg32', ns):
            name = domain.get('name') + '_' + reg.get('name')
            defines.append(f'#define REG_{name} {reg.get("offset")}')
            for bit in reg.findall('r:bitfield', ns):
                if bit.get('pos') is not None:
                    defines.append(f'#define {name}_{bit.get("name")} (1U << {bit.get("pos")})')
    defines += re.findall(r'^#define DSI_PHY_7NM_QUIRK_.*$', driver, re.M)
    harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <limits.h>
#include <errno.h>
#include <string.h>
typedef uint32_t u32;
#define __iomem
#define BIT(n) (1U << (n))
#define DBG(...) do {} while (0)
#define pr_warn(...) do {} while (0)
#define pr_err(...) do {} while (0)
#define DRM_DEV_ERROR(...) do {} while (0)
#define WARN_ON(x) ((void)(x))
#define spin_lock_irqsave(lock, flags) do { (void)(lock); (flags)=0; } while (0)
#define spin_unlock_irqrestore(lock, flags) do { (void)(lock); (void)(flags); } while (0)
#define readl_poll_timeout_atomic(addr, status, cond, delay, timeout) \
    ((void)(addr), (void)(delay), (void)(timeout), (status)=1, (cond) ? 0 : -1)
static u32 readl(void *p) { return *(u32 *)p; }
static void writel(u32 v, void *p) { *(u32 *)p=v; }
static void ndelay(int n) { (void)n; }
static void udelay(int n) { (void)n; }
struct msm_dsi_dphy_timing {
    u32 hs_exit, clk_prepare, hs_rqst, clk_zero, clk_trail, hs_zero, hs_prepare, hs_trail;
    struct { u32 clk_pre, clk_post; } shared_timings;
};
struct msm_dsi_phy_clk_request { unsigned long bitclk_rate; };
struct phy_cfg { u32 quirks; };
struct msm_dsi_phy {
    void *base, *pll_base, *pll_data;
    struct phy_cfg *cfg;
    struct msm_dsi_dphy_timing timing;
    bool cphy_mode;
};
struct dsi_pll_7nm { struct msm_dsi_phy *phy; int pll_enable_lock, pll_enable_cnt; };
static int msm_dsi_cphy_timing_calc_v4(struct msm_dsi_dphy_timing *t, struct msm_dsi_phy_clk_request *c) { return 0; }
static int msm_dsi_dphy_timing_calc_v4(struct msm_dsi_dphy_timing *t, struct msm_dsi_phy_clk_request *c) { return 0; }
static bool dsi_phy_hw_v4_0_is_pll_on(struct msm_dsi_phy *p) { return false; }
static void dsi_phy_7nm_apply_cphy_overrides(struct msm_dsi_phy *p, void *b) {}
static void dsi_phy_hw_v4_0_lane_settings(struct msm_dsi_phy *p) {}
static int dsi_7nm_set_usecase(struct msm_dsi_phy *p) { return 0; }
'''
    harness += '\n'.join(defines) + '\n' + '\n'.join(functions)
    harness += r'''
int main(void) {
    u32 common[2][1024] = {0}, plls[2][1024] = {0};
    struct phy_cfg cfg = {0};
    struct msm_dsi_phy_clk_request clocks = {998000000};
    for (int i=0; i<2; i++) {
        struct msm_dsi_phy p = {.base=common[i], .pll_base=plls[i], .cfg=&cfg, .cphy_mode=CPHY};
        struct dsi_pll_7nm pll = {.phy=&p};
        p.pll_data=&pll;
        if (dsi_7nm_phy_enable(&p, &clocks)) return 1;
        /* CCF preparing a fresh PHY must not skip its initial mux setup. */
        dsi_pll_enable_pll_bias(&pll);
        printf("link%d first %x refs%d\n", i, readl(p.pll_base+REG_DSI_7nm_PHY_PLL_SYSTEM_MUXES), pll.pll_enable_cnt);
        /* Warm re-enable remains covered by the existing bias helper. */
        memset(plls[i], 0, sizeof(plls[i]));
        pll.pll_enable_cnt=0;
        dsi_pll_enable_pll_bias(&pll);
        printf("link%d warm %x refs%d\n", i, readl(p.pll_base+REG_DSI_7nm_PHY_PLL_SYSTEM_MUXES), pll.pll_enable_cnt);
    }
}
'''.replace('CPHY', str(int(cphy)))
    source, binary = tmp_path / 'phy.c', tmp_path / 'phy'
    source.write_text(harness)
    subprocess.run([compiler, '-Wall', '-Werror', str(source), '-o', str(binary)], check=True)
    output = subprocess.check_output([str(binary)], text=True).splitlines()
    assert output == [f'link{i} {phase} c0 refs{refs}' for i in range(2)
                      for phase, refs in [('first', 2), ('warm', 1)]]
