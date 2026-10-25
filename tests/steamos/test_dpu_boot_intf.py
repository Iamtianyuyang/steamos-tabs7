"""Run the actual DPU helper that stops interfaces left running by the bootloader."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'
DPU = KERNEL / 'drivers/gpu/drm/msm/disp/dpu1'


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


def test_boot_intfs_are_stopped_before_the_first_modeset(tmp_path):
    compiler = shutil.which('cc')
    if compiler is None:
        pytest.skip('host C compiler unavailable')
    kms = (DPU / 'dpu_kms.c').read_text()
    helper = function(kms, 'static void dpu_kms_stop_boot_intfs(')
    init = function(kms, 'static int dpu_kms_hw_init(struct msm_kms *kms)\n{')
    # Must run once the INTF blocks exist, before any DRM object can modeset.
    assert init.index('dpu_rm_init(') < init.index('dpu_kms_stop_boot_intfs(dpu_kms);')
    assert init.index('dpu_kms_stop_boot_intfs(dpu_kms);') < init.index('_dpu_kms_drm_obj_init(')
    harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
typedef uint8_t u8;
typedef uint32_t u32;
enum dpu_intf { INTF_NONE, INTF_0 = 1, INTF_1, INTF_2, INTF_3, INTF_MAX };
struct dpu_intf_cfg { enum dpu_intf id; };
struct dpu_mdss_cfg { u32 intf_count; const struct dpu_intf_cfg *intf; };
struct dpu_hw_intf_status { u8 is_en, is_prog_fetch_en; u32 frame_count, line_count; };
struct dpu_hw_intf;
struct dpu_hw_intf_ops {
    void (*enable_timing)(struct dpu_hw_intf *intf, u8 enable);
    void (*get_status)(struct dpu_hw_intf *intf, struct dpu_hw_intf_status *s);
};
struct dpu_hw_intf { struct dpu_hw_intf_ops ops; u32 engine_en; int writes; };
struct dpu_rm { struct dpu_hw_intf *hw_intf[INTF_MAX - INTF_0]; };
struct dpu_kms { void *dev; const struct dpu_mdss_cfg *catalog; struct dpu_rm rm; };
static struct dpu_hw_intf *dpu_rm_get_intf(struct dpu_rm *rm, enum dpu_intf idx)
{ return rm->hw_intf[idx - INTF_0]; }
static int sleeps;
static void msleep(unsigned int ms) { sleeps += ms; }
#define drm_info(dev, ...) do { (void)(dev); printf("info " __VA_ARGS__); } while (0)
static void enable_timing(struct dpu_hw_intf *i, u8 en) { i->engine_en = en; i->writes++; }
static void get_status(struct dpu_hw_intf *i, struct dpu_hw_intf_status *s) { s->is_en = i->engine_en; }
'''
    harness += helper + r'''
int main(void) {
    /* SM8250: INTF_0 (DP) idle, bonded INTF_1/INTF_2 scanning the splash, INTF_3 absent. */
    const struct dpu_intf_cfg cfg[] = {{INTF_0}, {INTF_1}, {INTF_2}, {INTF_3}};
    const struct dpu_mdss_cfg catalog = {4, cfg};
    struct dpu_hw_intf hw[3] = {0};
    struct dpu_kms kms = {.catalog = &catalog};
    for (int i = 0; i < 3; i++) {
        hw[i].ops.enable_timing = enable_timing;
        hw[i].ops.get_status = get_status;
        kms.rm.hw_intf[i] = &hw[i];
    }
    hw[1].engine_en = hw[2].engine_en = 1;
    dpu_kms_stop_boot_intfs(&kms);
    for (int i = 0; i < 3; i++)
        printf("intf%d en=%u writes=%d\n", i, hw[i].engine_en, hw[i].writes);
    printf("slept=%d\n", sleeps);
    /* Nothing running: no writes and no delay on a later probe. */
    sleeps = 0;
    dpu_kms_stop_boot_intfs(&kms);
    printf("idle slept=%d writes=%d\n", sleeps, hw[1].writes + hw[2].writes);
}
'''
    source, binary = tmp_path / 'intf.c', tmp_path / 'intf'
    source.write_text(harness)
    subprocess.run([compiler, '-Wall', '-Werror', str(source), '-o', str(binary)], check=True)
    output = subprocess.check_output([str(binary)], text=True).splitlines()
    assert output == [
        'info stopping INTF_1 left enabled by the bootloader',
        'info stopping INTF_2 left enabled by the bootloader',
        'intf0 en=0 writes=0',
        'intf1 en=0 writes=1',
        'intf2 en=0 writes=1',
        'slept=40',
        'idle slept=0 writes=2',
    ]
