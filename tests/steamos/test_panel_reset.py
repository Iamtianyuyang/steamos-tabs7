"""Exercise the actual panel power callbacks against the vendor electrical trace."""
import json
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'


def test_reset_electrical_levels_match_vendor_sequence(tmp_path):
    compiler = shutil.which('cc')
    if compiler is None:
        pytest.skip('host C compiler unavailable')
    driver = (KERNEL / 'drivers/gpu/drm/panel/panel-novatek-nt36523-gts7lwifi.c').read_text()
    dts = (KERNEL / 'arch/arm64/boot/dts/qcom/sm8250-samsung-gts7-common.dtsi').read_text()
    profile = json.loads((ROOT / 'hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json').read_text())
    polarity = re.search(r'reset-gpios = <&tlmm 82 GPIO_ACTIVE_(LOW|HIGH)>', dts).group(1)
    callbacks = []
    for name in ('nt36523_power_on', 'nt36523_power_off'):
        start = driver.index('static int ' + name + '(')
        end = driver.index('\n}\n', start) + 3
        callbacks.append(driver[start:end])
    harness = r'''
#include <stdio.h>
struct regulator { int id; };
struct gpio_desc { int active_low; };
struct nt36523 { struct regulator *vddio, *buck; struct gpio_desc *reset_gpio; };
#define PPA957DB1_RESET_DELAY_MS 12
static int regulator_enable(struct regulator *r) { printf("enable %d\n", r->id); return 0; }
static int regulator_disable(struct regulator *r) { printf("disable %d\n", r->id); return 0; }
static void msleep(int ms) { printf("delay %d\n", ms); }
static void gpiod_set_value_cansleep(struct gpio_desc *g, int value) {
    printf("reset %d\n", !!value ^ g->active_low);
}
'''
    harness += '\n'.join(callbacks)
    harness += '\nint main(void) { struct regulator a={1}, b={2}; '
    harness += f'struct gpio_desc g={{{int(polarity == "LOW")}}}; '
    harness += 'struct nt36523 ctx={&a,&b,&g}; nt36523_power_on(&ctx); puts("off"); nt36523_power_off(&ctx); }\n'
    source = tmp_path / 'reset.c'
    binary = tmp_path / 'reset'
    source.write_text(harness)
    subprocess.run([compiler, '-Wall', '-Werror', str(source), '-o', str(binary)], check=True)
    output = subprocess.check_output([str(binary)], text=True).splitlines()
    expected = ['enable 1', 'enable 2', 'delay 10']
    for level, delay in profile['link']['reset_sequence']:
        expected += [f'reset {level}', f'delay {delay}']
    expected += ['off', 'reset 0', 'disable 2', 'delay 10', 'disable 1']
    assert output == expected
