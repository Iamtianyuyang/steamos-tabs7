"""Run the panel callback and real DCS helpers, checking the vendor wire format."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


@pytest.mark.parametrize('power', [0, 4])
def test_panel_brightness_sends_high_byte_first(tmp_path, power):
    compiler = shutil.which('cc')
    if compiler is None:
        pytest.skip('host C compiler unavailable')
    panel = (KERNEL / 'drivers/gpu/drm/panel/panel-novatek-nt36523-gts7lwifi.c').read_text()
    dcs = (KERNEL / 'drivers/gpu/drm/drm_mipi_dsi.c').read_text()
    profile = json.loads((ROOT / 'hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json').read_text())
    maximum = profile['brightness']['max_level']
    source = r'''
#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>
#include <sys/types.h>
typedef uint8_t u8;
typedef uint16_t u16;
#define MIPI_DCS_SET_DISPLAY_BRIGHTNESS 0x51
#define BACKLIGHT_POWER_ON 0
#define BL_CORE_SUSPENDED (1 << 0)
#define BL_CORE_FBBLANK (1 << 1)
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
struct mipi_dsi_device { int id; };
struct nt36523 { struct mipi_dsi_device *dsi[2]; };
struct backlight_device { struct { int brightness, power, state; } props; struct nt36523 *data; };
static struct nt36523 *bl_get_data(struct backlight_device *bl) { return bl->data; }
static ssize_t mipi_dsi_dcs_write(struct mipi_dsi_device *dsi, u8 command,
                                 const void *payload, size_t len) {
    const u8 *bytes = payload;
    printf("%d:%02x", dsi->id, command);
    for (size_t i = 0; i < len; i++) printf(" %02x", bytes[i]);
    puts("");
    return len;
}
'''
    backlight = (KERNEL / 'include/linux/backlight.h').read_text()
    for signature in ('static inline bool backlight_is_blank(',
                      'static inline int backlight_get_brightness('):
        source += function(backlight, signature)
    for signature in ('int mipi_dsi_dcs_set_display_brightness(',
                      'int mipi_dsi_dcs_set_display_brightness_large('):
        source += function(dcs, signature)
    source += function(panel, 'static int nt36523_bl_update_status(')
    source += '\nint main(void) { struct mipi_dsi_device a={0},b={1}; struct nt36523 ctx={{&a,&b}}; '
    source += f'struct backlight_device bl={{{{{maximum},{power},0}},&ctx}}; '
    source += 'return nt36523_bl_update_status(&bl); }\n'
    c = tmp_path / 'brightness.c'
    exe = tmp_path / 'brightness'
    c.write_text(source)
    subprocess.run([compiler, '-Wall', '-Werror', str(c), '-o', str(exe)], check=True)
    wire = subprocess.check_output([str(exe)], text=True).strip()
    expected = maximum if power == 0 else 0
    assert wire.splitlines() == [f'{link}:51 {expected >> 8:02x} {expected & 255:02x}'
                                for link in (0, 1)]
