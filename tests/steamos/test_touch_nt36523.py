"""NT36523 touch: run the real IRQ decoder and panel-follower gating, check the DTS."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'
DRIVER = KERNEL / 'drivers/input/touchscreen/novatek-nvt-ts.c'
DTS = KERNEL / 'arch/arm64/boot/dts/qcom/sm8250-samsung-gts7-common.dtsi'
VENDOR = ROOT / 'hardware/samsung/gts7lwifi/devicetree/vendor/input_gts7l_common.dtsi'


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


def node(text, name):
    start = text.index(name)
    return text[start:text.index('\n\t};\n', start)]


def test_dts_matches_the_vendor_touch_node():
    # The extracted vendor file is UTF-16 with CRLF line ends.
    vendor = node(VENDOR.read_bytes().decode('utf-16').replace('\r\n', '\n'),
                  'novatek_ts: touchscreen@62')
    dts = DTS.read_text()
    bus = dts[dts.index('&i2c5 {'):]
    bus = bus[:bus.index('\n};\n') + 1]
    touch = node(bus, 'touchscreen@1')
    assert 'status = "okay"' in bus
    # Events come from the firmware address, as the vendor driver reads them.
    assert 'reg = <0x01>;' in touch
    assert 'compatible = "novatek,nt36523-ts";' in touch
    irq = re.search(r'novatek,irq-gpio = <&tlmm (\d+) (0x[0-9a-f]+)>', vendor)
    assert f'interrupts = <{irq.group(1)} IRQ_TYPE_EDGE_FALLING>;' in touch
    assert int(irq.group(2), 16) & 0xf == 0x2, 'vendor flags are IRQF_TRIGGER_FALLING'
    x, y = re.search(r'novatek,resolution = <(\d+) (\d+)>', vendor).groups()
    assert f'touchscreen-size-x = <{x}>;' in touch
    assert f'touchscreen-size-y = <{y}>;' in touch
    # TDDI: no reset or supplies of its own, it follows the display panel.
    assert 'panel = <&display_panel>;' in touch
    assert 'reset-gpios' not in touch and 'supply' not in touch
    assert 'display_panel: panel@0 {' in dts


def test_irq_decoder_and_panel_gating(tmp_path):
    compiler = shutil.which('cc')
    if compiler is None:
        pytest.skip('host C compiler unavailable')
    driver = DRIVER.read_text()
    defines = '\n'.join(re.findall(r'^#define NVT_TS_\w+\s+\S.*$', driver, re.M))
    functions = [function(driver, sig) for sig in (
        'static irqreturn_t nvt_ts_irq(',
        'static void nvt_ts_set_irq(',
        'static int nvt_ts_start(',
        'static void nvt_ts_stop(',
        'static int nvt_ts_panel_prepared(',
        'static int nvt_ts_panel_unpreparing(',
    )]
    harness = r'''
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <stddef.h>
typedef uint8_t u8;
typedef int irqreturn_t;
#define IRQ_HANDLED 1
#define GENMASK(h, l) (((1U << ((h) - (l) + 1)) - 1) << (l))
#define fallthrough __attribute__((fallthrough))
#define container_of(p, t, m) ((t *)((char *)(p) - offsetof(t, m)))
#define dev_warn(d, ...) ((void)(d), printf("warn\n"))
#define dev_err(d, ...) ((void)(d), printf("err\n"))
enum { MT_TOOL_FINGER = 0, MT_TOOL_PALM = 2 };
struct device { int unused; };
struct mutex { int locked; };
static void mutex_lock(struct mutex *m) { m->locked++; }
static void mutex_unlock(struct mutex *m) { m->locked--; }
struct input_dev { struct mutex mutex; int users; void *drvdata; };
struct i2c_client { struct device dev; int irq; };
struct gpio_desc;
struct regulator_bulk_data { const char *supply; };
struct touchscreen_properties { int unused; };
struct drm_panel_follower { const void *funcs; };
static int irq_depth = 1;   /* IRQF_NO_AUTOEN */
static void enable_irq(int irq) { irq_depth--; }
static void disable_irq(int irq) { irq_depth++; }
static bool input_device_enabled(struct input_dev *d) { return d->users > 0; }
static void *input_get_drvdata(struct input_dev *d) { return d->drvdata; }
static void gpiod_set_value_cansleep(struct gpio_desc *g, int v) { printf("reset %d\n", v); }
static int regulator_bulk_enable(int n, struct regulator_bulk_data *r) { printf("supply on\n"); return 0; }
static void regulator_bulk_disable(int n, struct regulator_bulk_data *r) { printf("supply off\n"); }
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
static void input_mt_slot(struct input_dev *d, int s) { printf("slot %d ", s); }
static void input_mt_report_slot_state(struct input_dev *d, int tool, bool a) { printf("tool %d active %d ", tool, a); }
static void touchscreen_report_pos(struct input_dev *d, struct touchscreen_properties *p, int x, int y, bool mt)
{ printf("x %d y %d\n", x, y); }
static void input_mt_sync_frame(struct input_dev *d) { printf("frame\n"); }
static void input_sync(struct input_dev *d) { }
'''
    harness += defines + r'''
struct nvt_ts_data {
    struct i2c_client *client;
    struct input_dev *input;
    struct gpio_desc *reset_gpio;
    struct regulator_bulk_data regulators[2];
    struct touchscreen_properties prop;
    int max_touches;
    struct drm_panel_follower panel_follower;
    bool is_panel_follower;
    bool panel_prepared;
    bool irq_enabled;
    u8 buf[NVT_TS_TOUCH_SIZE * NVT_TS_MAX_TOUCHES];
};
static u8 frame[NVT_TS_TOUCH_SIZE * NVT_TS_MAX_TOUCHES];
static int nvt_ts_read_data(struct i2c_client *c, u8 reg, u8 *data, int count)
{ memcpy(data, frame, count); return 0; }
'''
    harness += '\n'.join(functions) + r'''
int main(void) {
    struct i2c_client client = {.irq = 42};
    struct input_dev input = {0};
    struct nvt_ts_data data = {.client = &client, .input = &input, .max_touches = 10,
                               .is_panel_follower = true};
    input.drvdata = &data;
    memset(frame, 0xff, sizeof(frame));
    /* Vendor packing: id << 3 | status, x[11:4], y[11:4], x[3:0] << 4 | y[3:0]. */
    u8 enter[6] = {1 << 3 | 1, 0x63, 0x9f, 0xff};      /* id 1 at (1599, 2559) */
    u8 palm[6]  = {2 << 3 | 5, 0x32, 0x00, 0x05};      /* id 2 palm at (800, 5) */
    u8 glove[6] = {10 << 3 | 6, 0x00, 0x01, 0x10};     /* id 10 glove at (1, 16) */
    memcpy(frame, enter, 6); memcpy(frame + 6, palm, 6); memcpy(frame + 54, glove, 6);
    nvt_ts_irq(42, &data);
    /* Open before the panel is up: no interrupt yet, no reset or supply toggles. */
    input.users = 1;
    nvt_ts_start(&input);
    printf("open depth %d\n", irq_depth);
    nvt_ts_panel_prepared(&data.panel_follower);
    printf("prepared depth %d\n", irq_depth);
    nvt_ts_panel_unpreparing(&data.panel_follower);
    printf("unpreparing depth %d\n", irq_depth);
    nvt_ts_panel_prepared(&data.panel_follower);
    nvt_ts_stop(&input);
    input.users = 0;
    printf("close depth %d\n", irq_depth);
    /* Panel goes down while closed, then back up: stays masked until opened. */
    nvt_ts_panel_unpreparing(&data.panel_follower);
    nvt_ts_panel_prepared(&data.panel_follower);
    printf("closed prepared depth %d\n", irq_depth);
    input.users = 1;
    nvt_ts_start(&input);
    printf("reopen depth %d lock %d\n", irq_depth, input.mutex.locked);
}
'''
    source, binary = tmp_path / 'touch.c', tmp_path / 'touch'
    source.write_text(harness)
    subprocess.run([compiler, '-Wall', '-Werror', '-Wno-unused-function', str(source), '-o', str(binary)],
                   check=True)
    output = subprocess.check_output([str(binary)], text=True).splitlines()
    assert output == [
        'slot 0 tool 0 active 1 x 1599 y 2559',
        'slot 1 tool 2 active 1 x 800 y 5',
        'slot 9 tool 0 active 1 x 1 y 16',
        'frame',
        'open depth 1',
        'prepared depth 0',
        'unpreparing depth 1',
        'close depth 1',
        'closed prepared depth 1',
        'reopen depth 0 lock 0',
    ]


def test_gpi_dma0_uses_the_vendor_ee_offset():
    """i2c5 is in GSI mode; its GPI must address the EE the firmware gave Linux."""
    kona = ROOT / 'out/vendor-kernel-prep/src/arch/arm64/boot/dts/vendor/qcom/kona.dtsi'
    if not kona.exists():
        pytest.skip('prepared vendor source unavailable')
    vendor = kona.read_text()
    vendor = vendor[vendor.index('gpi_dma0: qcom,gpi-dma@900000'):]
    offset = int(re.search(r'qcom,gpi-ee-offset = <(0x[0-9a-f]+)>', vendor[:vendor.index('};')]).group(1), 16)
    dts = DTS.read_text()
    board = dts[dts.index('&gpi_dma0 {'):]
    board = board[:board.index('};')]
    assert f'qcom,gpi-ee-offset = <{offset:#x}>;' in board
    gpi = (KERNEL / 'drivers/dma/qcom/gpi.c').read_text()
    probe = function(gpi, 'static int gpi_probe(')
    assert probe.index('"qcom,gpi-ee-offset"') < probe.index('gpi_dev->ee_base = gpi_dev->ee_base - ee_offset;')
