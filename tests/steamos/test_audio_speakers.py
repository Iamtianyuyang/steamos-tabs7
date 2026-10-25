"""Speaker path: DT against the vendor audio DTS, boost registers, machine TDM setup."""
from pathlib import Path
import re
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'
DTS = KERNEL / 'arch/arm64/boot/dts/qcom/sm8250-samsung-gts7-common.dtsi'
VENDOR_SRC = ROOT / 'out/vendor-kernel-prep/src'
VENDOR_AUDIO = VENDOR_SRC / 'arch/arm64/boot/dts/samsung/drivers/audio_gts7xl_common.dtsi'


def block(text, start):
    begin = text.index(start)
    depth = 0
    for i in range(text.index('{', begin), len(text)):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[begin:i + 1]
    raise AssertionError(start)


def function(text, signature):
    start = text.index(signature)
    return text[start:text.index('\n}\n', start) + 3]


def vendor():
    if not VENDOR_AUDIO.exists():
        pytest.skip('prepared vendor source unavailable')
    return VENDOR_AUDIO.read_text()


def test_amplifiers_match_the_vendor_nodes():
    text = vendor()
    dts = block(DTS.read_text(), '&i2c11 {')
    for addr in ('40', '41', '42', '43'):
        theirs = block(text, f'cs35l41@{addr}')
        ours = block(dts, f'amplifier@{addr}')
        assert f'reg = <0x{addr}>;' in ours
        right = 'cirrus,right-channel-amp' in theirs
        prefix = re.search(r'sound-name-prefix = "([^"]+)"', ours).group(1)
        # sm8250_tdm_hw_params() puts "...Right" amplifiers on slot 1.
        assert ('Right' in prefix) == right, addr
        assert 'interrupts = <84 IRQ_TYPE_LEVEL_LOW>;' in ours
        assert 'cirrus,boost-peak-milliamp = <4100>;' in theirs and 'cirrus,boost-peak-milliamp = <4100>;' in ours
        assert 'cirrus,asp-sdout-hiz = <0x3>;' in theirs and 'cirrus,asp-sdout-hiz = <3>;' in ours
        # Vendor GPIO2: source 4 (interrupt, push-pull active low), output enabled.
        assert 'cirrus,gpio2-src-select = <4>;' in ours and 'cirrus,gpio2-output-enable;' in ours


def test_reset_is_released_by_a_logical_high():
    text = vendor()
    assert 'reset-gpios = <&tlmm 69 0>;' in text, 'vendor flag 0 = active high'
    dts = block(DTS.read_text(), '&i2c11 {')
    assert dts.count('reset-gpios = <&tlmm 69 GPIO_ACTIVE_HIGH>;') == 4
    driver = (KERNEL / 'sound/soc/codecs/cs35l41.c').read_text()
    assert 'gpiod_set_value_cansleep(cs35l41->reset_gpio, 1);' in driver


def test_boost_settings_reproduce_the_vendor_registers():
    defaults = (VENDOR_SRC / 'drivers/mfd/cs35l41-tables.c')
    if not defaults.exists():
        pytest.skip('prepared vendor source unavailable')
    regs = dict(re.findall(r'\{(CS35L41_BSTCVRT_\w+),\s*(0x[0-9A-Fa-f]+)\}', defaults.read_text()))
    vendor_driver = (VENDOR_SRC / 'sound/soc/codecs/cs35l41.c').read_text()
    # The vendor driver only programs the peak current, never these two.
    assert 'CS35L41_BSTCVRT_COEFF' not in vendor_driver
    assert 'CS35L41_BSTCVRT_SLOPE_LBST' not in vendor_driver

    lib = (KERNEL / 'sound/soc/codecs/cs35l41-lib.c').read_text()

    def table(name):
        body = lib[lib.index(name):]
        body = body[body.index('{'):body.index('};')]
        return [[int(x, 16) for x in re.findall(r'0x[0-9A-Fa-f]+', row)]
                for row in re.findall(r'\{([^{}]*)\}', body)] or \
               [int(x, 16) for x in re.findall(r'0x[0-9A-Fa-f]+', body)]

    k1, k2 = table('cs35l41_bst_k1_table'), table('cs35l41_bst_k2_table')
    slope = [int(x, 16) for x in re.findall(r'0x[0-9A-Fa-f]+',
             lib[lib.index('cs35l41_bst_slope_table'):].split('};')[0])]
    dts = block(DTS.read_text(), '&i2c11 {')
    ind = int(re.search(r'boost-ind-nanohenry = <(\d+)>', dts).group(1))
    cap = int(re.search(r'boost-cap-microfarad = <(\d+)>', dts).group(1))
    lbst = {1000: 0, 1200: 1, 1500: 2, 2200: 3}[ind]
    cbst = 0 if cap <= 19 else 1 if cap <= 50 else 2 if cap <= 100 else 3 if cap <= 200 else 4
    assert k1[lbst][cbst] | k2[lbst][cbst] << 8 == int(regs['CS35L41_BSTCVRT_COEFF'], 16)
    assert slope[lbst] << 8 | lbst == int(regs['CS35L41_BSTCVRT_SLOPE_LBST'], 16)


def test_tdm_port_matches_the_vendor_cpu_dai():
    text = vendor()
    theirs = block(text, '&tdm_pri_tx {')  # tdm_pri_rx carries the same values
    ours = block(DTS.read_text(), '&q6afedai {')
    pairs = {'sync-mode': 'tdm-sync-mode', 'sync-src': 'tdm-sync-src', 'data-out': 'tdm-data-out',
             'invert-sync': 'tdm-invert-sync', 'data-delay': 'tdm-data-delay'}
    for vendor_key, key in pairs.items():
        value = re.search(rf'qcom,msm-cpudai-tdm-{vendor_key} = <(\d+)>', theirs).group(1)
        assert f'qcom,{key} = <{value}>;' in ours, key
    rate = int(re.search(r'qcom,msm-cpudai-tdm-clk-rate = <(\d+)>', theirs).group(1))
    machine = (KERNEL / 'sound/soc/qcom/sm8250.c').read_text()
    assert f'#define TDM_BCLK_RATE\t\t{rate}' in machine
    assert rate == 48000 * 4 * 32


def test_machine_driver_programs_slots_and_sides(tmp_path):
    compiler = shutil.which('cc')
    if compiler is None:
        pytest.skip('host C compiler unavailable')
    machine = (KERNEL / 'sound/soc/qcom/sm8250.c').read_text()
    defines = '\n'.join(re.findall(r'^#define TDM_\w+\s+\d+$', machine, re.M))
    offsets = re.search(r'^static const unsigned int tdm_slot_offset\[\].*$', machine, re.M).group(0)
    harness = r'''
#include <stdbool.h>
#include <stdio.h>
#include <string.h>
#define ARRAY_SIZE(a) (sizeof(a) / sizeof((a)[0]))
#define GENMASK(h, l) (((1U << ((h) - (l) + 1)) - 1) << (l))
#define EINVAL 22
struct snd_soc_component { const char *name_prefix; };
struct snd_soc_dai { const char *name; struct snd_soc_component *component; };
struct snd_soc_pcm_runtime { struct snd_soc_dai *cpu; struct snd_soc_dai **codecs; int num_codecs; };
struct snd_pcm_hw_params { unsigned int channels; };
static unsigned int params_channels(struct snd_pcm_hw_params *p) { return p->channels; }
static struct snd_soc_dai *snd_soc_rtd_to_cpu(struct snd_soc_pcm_runtime *r, int i) { return r->cpu; }
#define for_each_rtd_codec_dais(rtd, i, dai) \
    for ((i) = 0; (i) < (rtd)->num_codecs && ((dai) = (rtd)->codecs[i]); (i)++)
static int snd_soc_dai_set_tdm_slot(struct snd_soc_dai *d, unsigned int tx, unsigned int rx, int slots, int width)
{ printf("%s tdm tx=%x rx=%x slots=%d width=%d\n", d->name, tx, rx, slots, width); return 0; }
static int snd_soc_dai_set_channel_map(struct snd_soc_dai *d, unsigned int txn, const unsigned int *tx,
                                       unsigned int rxn, const unsigned int *rx)
{
    printf("%s map tx=%u rx=", d->name, txn);
    for (unsigned int i = 0; i < rxn; i++) printf("%u%s", rx[i], i + 1 < rxn ? "," : "\n");
    return 0;
}
'''
    harness += defines + '\n' + offsets + '\n' + function(machine, 'static int sm8250_tdm_hw_params(')
    harness += r'''
int main(void) {
    struct snd_soc_component fl = {"Front Left"}, fr = {"Front Right"}, rl = {"Rear Left"}, rr = {"Rear Right"};
    struct snd_soc_dai cpu = {"cpu"}, a = {"fl", &fl}, b = {"fr", &fr}, c = {"rl", &rl}, d = {"rr", &rr};
    struct snd_soc_dai *codecs[] = {&a, &b, &c, &d};
    struct snd_soc_pcm_runtime rtd = {&cpu, codecs, 4};
    struct snd_pcm_hw_params stereo = {2}, surround = {4};
    printf("ret %d\n", sm8250_tdm_hw_params(&rtd, &stereo));
    printf("ret %d\n", sm8250_tdm_hw_params(&rtd, &surround));
}
'''
    source, binary = tmp_path / 'tdm.c', tmp_path / 'tdm'
    source.write_text(harness)
    subprocess.run([compiler, '-Wall', '-Werror', '-Wno-unused-parameter', str(source), '-o', str(binary)],
                   check=True)
    output = subprocess.check_output([str(binary)], text=True).splitlines()
    assert output == [
        'cpu tdm tx=0 rx=3 slots=4 width=32',
        'cpu map tx=0 rx=0,4',
        'fl map tx=0 rx=0,1',
        'fr map tx=0 rx=1,0',
        'rl map tx=0 rx=0,1',
        'rr map tx=0 rx=1,0',
        'ret 0',
        'ret -22',
    ]


def test_every_sound_frontend_is_declared():
    dts = DTS.read_text()
    used = set(re.findall(r'&q6asmdai (MSM_FRONTEND_DAI_MULTIMEDIA\d+)', block(dts, '&sound {')))
    declared = set(re.findall(r'reg = <(MSM_FRONTEND_DAI_MULTIMEDIA\d+)>', block(dts, '&q6asmdai {')))
    assert used and used <= declared


def test_routing_opens_the_adm_with_the_backend_width():
    """The TDM backend is S32_LE; ADM rejects a COPP opened with width 0."""
    routing = (KERNEL / 'sound/soc/qcom/qdsp6/q6routing.c').read_text()
    hw = function(routing, 'static int routing_hw_params(')
    assert re.search(r'case SNDRV_PCM_FORMAT_S32_LE:\s*session->bits_per_sample = 32;', hw)
    machine = (KERNEL / 'sound/soc/qcom/sm8250.c').read_text()
    assert 'SNDRV_PCM_FORMAT_S32_LE' in function(machine, 'static int sm8250_be_hw_params_fixup(')


def test_wireplumber_config_keeps_bluetooth_working():
    """The bluez monitor only runs without seat monitoring in this session.

    WirePlumber 0.5's bluez monitor is created only when logind reports an
    active seat; the Game Mode session has none, so A2DP endpoints would never
    be registered and every Bluetooth connect would fail with "Protocol not
    available".
    """
    conf = (ROOT / 'userspace/steamos/wireplumber/99-tabs7-audio.conf').read_text()
    assert 'monitor.bluez.seat-monitoring = disabled' in conf
    assert 'wireplumber.profiles' in conf
