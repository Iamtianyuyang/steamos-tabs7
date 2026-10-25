"""Replay reset-induced IRQs through the actual panel ESD callbacks."""
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
DRIVER = ROOT / 'linux-sm8250-v6.18/drivers/gpu/drm/panel/panel-novatek-nt36523-gts7lwifi.c'


def test_esd_does_not_requeue_its_own_reset_and_restores_brightness(tmp_path):
    compiler = shutil.which('cc')
    if compiler is None:
        pytest.skip('host C compiler unavailable')
    source = DRIVER.read_text()
    callbacks = []
    for signature in ('static irqreturn_t nt36523_esd_irq(', 'static void nt36523_esd_work('):
        start = source.index(signature)
        callbacks.append(source[start:source.index('\n}\n', start) + 3])
    harness = r'''
#include <assert.h>
#include <stdbool.h>
#include <stddef.h>
#define IRQ_HANDLED 1
#define ARRAY_SIZE(x) (sizeof(x)/sizeof((x)[0]))
#define EIO 5
#define READ_ONCE(x) (x)
#define WRITE_ONCE(x,v) ((x)=(v))
#define container_of(ptr,type,member) ((type *)((char *)(ptr)-offsetof(type,member)))
#define dev_warn(...) ((void)0)
#define dev_err(...) ((void)0)
#define dev_info(...) ((void)0)
typedef int irqreturn_t;
struct work_struct { int queued; };
struct nt36523 { struct work_struct esd_work; bool prepared, enabled;
 int lock, esd_recoveries; struct { void *dev; } panel;
 void *dsi[2], *backlight; };
static struct nt36523 *active;
static int irq_calls, off_calls, restored, init_error;
static irqreturn_t nt36523_esd_irq(int irq, void *data);
static void schedule_work(struct work_struct *work) { work->queued++; }
static void mutex_lock(int *lock) {}
static void mutex_unlock(int *lock) {}
void msleep(int ms) {}
static void edge(void) { irq_calls++; nt36523_esd_irq(164, active); }
static int nt36523_power_off(struct nt36523 *ctx) { off_calls++; edge(); return 0; }
static int nt36523_power_on(struct nt36523 *ctx) { edge(); return 0; }
static int nt36523_send_init_sequence(struct nt36523 *ctx) { edge(); return init_error; }
static int mipi_dsi_dcs_set_display_on(void *dsi) { edge(); return 0; }
int backlight_update_status(void *bl) { restored++; return 0; }
'''
    harness += '\n'.join(callbacks)
    harness += r'''
int main(void) {
 struct nt36523 ctx={0}; active=&ctx;
 nt36523_esd_irq(164,&ctx); assert(ctx.esd_work.queued==0);
 ctx.prepared=true;
 nt36523_esd_irq(164,&ctx); assert(ctx.esd_work.queued==0);
 ctx.enabled=true;
 nt36523_esd_irq(164,&ctx); assert(ctx.esd_work.queued==1);
 ctx.esd_work.queued=0;
 nt36523_esd_work(&ctx.esd_work);
 assert(irq_calls>=3); assert(ctx.esd_work.queued==0);
 assert(ctx.prepared && ctx.enabled); assert(restored==1);
 assert(ctx.esd_recoveries==1);
 init_error=-EIO;
 nt36523_esd_work(&ctx.esd_work);
 assert(!ctx.prepared); assert(off_calls==3);
 assert(ctx.esd_work.queued==0);
 return 0;
}
'''
    c = tmp_path / 'esd.c'
    exe = tmp_path / 'esd'
    c.write_text(harness)
    subprocess.run([compiler, '-Wall', '-Werror', str(c), '-o', str(exe)], check=True)
    subprocess.run([str(exe)], check=True)
