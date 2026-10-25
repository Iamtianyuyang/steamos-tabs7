"""Both physical DDICs must receive the profile, including its sleep-out."""
import json
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


def test_init_profile_reaches_both_dsi_links(tmp_path):
    compiler = shutil.which('cc')
    if compiler is None:
        pytest.skip('host C compiler unavailable')
    driver = (ROOT / 'linux-sm8250-v6.18/drivers/gpu/drm/panel/panel-novatek-nt36523-gts7lwifi.c').read_text()
    profile = json.loads((ROOT / 'hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json').read_text())
    start = driver.index('static int nt36523_send_init_sequence(')
    callback = driver[start:driver.index('\n}\n', start) + 3]
    harness = r'''
#include <stdio.h>
#include <stdint.h>
#include <stddef.h>
typedef uint8_t u8;
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define dev_err(...) ((void)0)
struct mipi_dsi_device { int id; };
struct nt36523_init_cmd { u8 len, delay_ms; u8 payload[16]; };
struct nt36523_desc { const struct nt36523_init_cmd *init; unsigned int num_init; };
struct nt36523 { struct mipi_dsi_device *dsi[2]; const struct nt36523_desc *desc;
 struct { void *dev; } panel; };
static void msleep(int ms) {}
static int mipi_dsi_dcs_write_buffer(struct mipi_dsi_device *dsi, const void *data, size_t len) {
 const u8 *bytes=data;
 printf("%d",dsi->id);
 for (size_t i=0;i<len;i++) printf(" %02x",bytes[i]);
 puts(""); return len;
}
'''
    harness += callback + '\nstatic const struct nt36523_init_cmd commands[]={\n'
    for cmd in profile['init_sequence']['commands']:
        payload = cmd['payload'][1:]
        harness += '{%d,%d,{%s}},\n' % (len(payload), cmd['wait'][0], ','.join(map(str, payload)))
    harness += '};\nint main(void) { struct mipi_dsi_device a={0},b={1}; '
    harness += 'struct nt36523_desc desc={commands,ARRAY_SIZE(commands)}; '
    harness += 'struct nt36523 ctx={{&a,&b},&desc}; return nt36523_send_init_sequence(&ctx); }\n'
    c = tmp_path / 'dual.c'
    exe = tmp_path / 'dual'
    c.write_text(harness)
    subprocess.run([compiler, '-Wall', '-Werror', str(c), '-o', str(exe)], check=True)
    rows = subprocess.check_output([str(exe)], text=True).splitlines()
    expected = [' '.join(f'{b:02x}' for b in cmd['payload'][1:])
                for cmd in profile['init_sequence']['commands']]
    for link in (0, 1):
        actual = [row.split(' ', 1)[1] for row in rows if row.startswith(f'{link} ')]
        assert actual == expected, f'DSI{link} did not receive the complete profile'
