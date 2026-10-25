"""Wi-Fi on 6.18: QCA6390 PMU rails, enable line and the PCI consumer.

The expectations come from the vendor sources and from the live vendor DT of
this device, not from upstream reference boards: upstream QCA6390 devices use
different rails and different enable GPIOs.
"""
from pathlib import Path
import hashlib
import json
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'
DTS = KERNEL / 'arch/arm64/boot/dts/qcom/sm8250-samsung-gts7-common.dtsi'
VENDOR_SRC = ROOT / 'out/vendor-kernel-prep/src'
VENDOR_KONA = VENDOR_SRC / 'arch/arm64/boot/dts/vendor/qcom/kona.dtsi'
VENDOR_GTS7L = VENDOR_SRC / 'arch/arm64/boot/dts/samsung/gts7l'
VENDOR_WIFI = VENDOR_GTS7L / 'kona-sec-gts7lwifi-r02.dtsi'
CONFIG = ROOT / 'hardware/samsung/gts7lwifi/kernel/configs/steamos-wifi-6.18.config'

# Vendor supply name -> (mainline rail, vendor initial voltage in uV). The
# live vendor DT of this device resolves the vendor regulators to
# pm8150_s6/s4/s5 and pm8150a_s8; Samsung's pm8150a is mainline's pm8150l, so
# s8 is vreg_s8c (smps8). vdd-wlan-dig is the one rail the board changes: the
# SoC dtsi defaults it to pm8009_s2 and gts7lwifi overrides it to pm8150_s6.
RAILS = {
    'vdd-wlan-aon': ('vreg_s6a_0p95', 950000),
    'vdd-wlan-dig': ('vreg_s6a_0p95', 950000),
    'vdd-wlan-io': ('vreg_s4a_1p8', 1800000),
    'vdd-wlan-rfa1': ('vreg_s5a_1p9', 2040000),
    'vdd-wlan-rfa2': ('vreg_s8c_1p35', 1350000),
}

# Vendor PMU input -> the mainline PMU input that carries the same rail.
PMU_SUPPLIES = {
    'vddaon-supply': 'vdd-wlan-aon',
    'vddpmu-supply': 'vdd-wlan-dig',
    'vddrfa0p95-supply': 'vdd-wlan-dig',
    'vddio-supply': 'vdd-wlan-io',
    'vddrfa1p9-supply': 'vdd-wlan-rfa1',
    'vddpcie1p9-supply': 'vdd-wlan-rfa1',
    'vddrfa1p3-supply': 'vdd-wlan-rfa2',
    'vddpcie1p3-supply': 'vdd-wlan-rfa2',
}

LDO_LABELS = ['vreg_pmu_rfa_cmn', 'vreg_pmu_aon_0p59', 'vreg_pmu_wlcx_0p8',
              'vreg_pmu_wlmx_0p85', 'vreg_pmu_btcmx_0p85', 'vreg_pmu_rfa_0p8',
              'vreg_pmu_rfa_1p2', 'vreg_pmu_rfa_1p7', 'vreg_pmu_pcie_0p9',
              'vreg_pmu_pcie_1p8']


def block(text, start):
    begin = text.index(start)
    depth = 0
    for i in range(text.index('{', begin), len(text)):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[begin:i + 1]
    raise AssertionError(start)


def dts():
    return DTS.read_text()


def pmu():
    return block(dts(), 'qca6390-pmu {')


def wifi():
    return block(dts(), 'wifi@0 {')


def vendor():
    if not VENDOR_KONA.exists():
        pytest.skip('prepared vendor source unavailable')
    return VENDOR_KONA.read_text()


def test_pmu_rails_match_the_vendor_mapping():
    """Each PMU input must use the rail (and voltage) the vendor configures."""
    node = block(vendor(), 'qcom,cnss-qca6490@b0000000')
    board = VENDOR_WIFI.read_text()
    ours = pmu()
    for supply, vendor_name in PMU_SUPPLIES.items():
        rail, microvolt = RAILS[vendor_name]
        # Vendor side: the supply and its requested voltage are both present.
        assert f'{vendor_name}-supply = <&' in node, vendor_name
        config = re.search(rf'qcom,{vendor_name}-config = <(\d+) ', node)
        assert config, vendor_name
        assert abs(int(config.group(1)) - microvolt) <= 8000, vendor_name
        # Our side: same rail on the same PMU input.
        assert f'{supply} = <&{rail}>;' in ours, supply
    # The SoC dtsi puts VDD_WLAN_DIG on PM8009 S2; this board overrides it.
    assert 'vdd-wlan-dig-supply = <&pm8009_s2>;' in node
    assert 'vdd-wlan-dig-supply = <&pm8150_s6>;' in board
    assert 'vddpmu-supply = <&vreg_s6a_0p95>;' in ours
    assert 'vddrfa0p95-supply = <&vreg_s6a_0p95>;' in ours


def test_pcie_1p9_rail_note_matches_the_vendor_fixed_voltage():
    """The vendor pins pm8150_s5 at 2.04 V; our rail keeps its 1.9 V default.

    Keep that difference documented instead of silently changing a rail other
    consumers (pm8150l LDO7/12/14/15) also sit on.
    """
    node = block(vendor(), 'qcom,cnss-qca6490@b0000000')
    assert 'qcom,vdd-wlan-rfa1-config = <2040000 2040000 0 0 1>;' in node
    common = dts()
    s5 = block(common, 'vreg_s5a_1p9: smps5 {')
    assert 'regulator-min-microvolt = <1900000>;' in s5
    assert 'regulator-max-microvolt = <2040000>;' in s5
    assert 'vdd-l7-l12-l14-l15-supply = <&vreg_s5a_1p9>;' in common
    # The node comment above qca6390-pmu records the difference.
    assert '2.04 V' in common


def test_wlan_enable_line_matches_the_vendor_gpio():
    """wlan-en comes from the board dtsi, not from the SoC one."""
    board = VENDOR_WIFI.read_text()
    assert 'wlan-en-gpio = <&tlmm 90 0>;' in board
    assert 'wlan-enable-gpios = <&tlmm 90 GPIO_ACTIVE_HIGH>;' in pmu()
    pinctrl = (VENDOR_GTS7L / 'kona-sec-gts7lwifi-pinctrl-r02.dtsi')
    if not pinctrl.exists():
        pytest.skip('prepared vendor pinctrl unavailable')
    assert 'pins = "gpio90";' in block(pinctrl.read_text(), 'qca6390_wlan_en_active:')
    assert 'pinctrl-0 = <&wlan_en_default>;' in pmu()
    assert 'pins = "gpio90";' in block(dts(), 'wlan_en_default:')


def test_bluetooth_is_left_out_until_its_gpio_is_resolved():
    """Vendor source says bt-en is TLMM 76, the live vendor DT says 21.

    21 is inside the TrustZone-reserved fingerprint range (20-23) this board
    declares, so neither value is safe to adopt from the WiFi step alone.
    """
    gts7l = (VENDOR_GTS7L / 'kona-sec-gts7l-r00.dtsi')
    if gts7l.exists():
        assert 'qcom,bt-en-gpio = <&tlmm 76 0>;' in gts7l.read_text()
    ours = pmu()
    assert 'bt-enable-gpios' not in ours
    assert 'bt-enable-gpios' not in wifi()
    assert 'gpio-reserved-ranges = <20 4>' in dts()


def test_pmu_regulators_nest_under_the_pmu_node():
    """pwrseq_qcom_wcn_match() requires supply -> ldo -> regulators -> PMU."""
    node = pmu()
    regulators = block(node, 'regulators {')
    for label in LDO_LABELS:
        assert f'{label}: ldo' in regulators, label
    aon = block(regulators, 'regulators {')
    assert 'vreg_pmu_aon_0p59: ldo1 {' in aon
    assert 'regulator-name = "vreg_pmu_aon_0p59";' in aon
    # The consumer's vddaon-supply must point into that same node, and no
    # other node may define the label, or the match walks somewhere else.
    assert 'vddaon-supply = <&vreg_pmu_aon_0p59>;' in wifi()
    text = dts()
    assert text.count('vreg_pmu_aon_0p59:') == 1


def test_binding_required_supplies_are_present():
    binding = KERNEL / 'Documentation/devicetree/bindings/regulator/qcom,qca6390-pmu.yaml'
    text = binding.read_text()
    section = text[text.index('const: qcom,qca6390-pmu'):]
    required = section[:section.index('  - if:')]
    names = re.findall(r'^\s+- (vdd[\w-]+)-supply$', required, re.M)
    assert names, 'binding lists no supplies'
    ours = pmu()
    for name in names:
        assert f'{name}-supply = <&' in ours, name


def test_pci_device_and_host_are_enabled():
    text = dts()
    assert '&pcie0 {' in text and 'status = "okay";' in block(text, '&pcie0 {')
    assert 'status = "okay";' in block(text, '&pcie0_phy {')
    node = wifi()
    assert 'compatible = "pci17cb,1101";' in node
    # pci-pwrctrl-pwrseq validates this supply before binding, and ath11k
    # needs the same node to be a child of the root port.
    assert '&pcieport0 {' in text
    for supply in ('vddrfacmn', 'vddwlcx', 'vddwlmx', 'vddrfa0p8',
                   'vddrfa1p2', 'vddrfa1p7', 'vddpcie0p9', 'vddpcie1p8'):
        assert f'{supply}-supply = <&vreg_pmu_' in node, supply


def test_config_fragment_asks_for_the_driver_stack():
    text = CONFIG.read_text()
    for symbol in ('CONFIG_PCI', 'CONFIG_PCIE_QCOM', 'CONFIG_PHY_QCOM_QMP_PCIE',
                   'CONFIG_POWER_SEQUENCING',
                   'CONFIG_POWER_SEQUENCING_QCOM_WCN', 'CONFIG_WLAN',
                   'CONFIG_WLAN_VENDOR_ATH', 'CONFIG_CFG80211', 'CONFIG_MAC80211',
                   'CONFIG_ATH11K', 'CONFIG_ATH11K_PCI', 'CONFIG_RFKILL',
                   'CONFIG_FW_LOADER'):
        assert re.search(rf'^{symbol}=y$', text, re.M), symbol


def test_every_requested_config_symbol_exists_in_the_tree():
    kconfigs = [p for p in KERNEL.glob('**/Kconfig*') if p.is_file()]
    declared = set()
    for path in kconfigs:
        declared |= set(re.findall(r'^(?:menu)?config (\w+)$',
                                   path.read_text(errors='ignore'), re.M))
    for symbol in re.findall(r'^CONFIG_(\w+)=y$', CONFIG.read_text(), re.M):
        assert symbol in declared, symbol


# Firmware: upstream linux-firmware layout, staged under the ignored out/ tree.
FIRMWARE = ROOT / 'out/firmware/ath11k'
INITRAMFS = ROOT / 'scripts/build/mainline/build-initramfs.sh'


def test_initramfs_ships_the_ath11k_firmware():
    """ath11k asks for its firmware at PCI probe, before the rootfs exists."""
    script = INITRAMFS.read_text()
    assert 'out/firmware/ath11k' in script
    assert 'sha256sum --quiet -c SHA256SUMS' in script
    for name in ('amss.bin', 'm3.bin', 'board-2.bin'):
        assert name in script, name
    assert 'lib/firmware/ath11k/QCA6390/hw2.0' in script


def test_staged_firmware_matches_its_manifest():
    manifest = FIRMWARE / 'manifest.json'
    if not manifest.exists():
        pytest.skip('staged firmware unavailable')
    data = json.loads(manifest.read_text())
    assert data['files'], 'manifest lists no files'
    for entry in data['files']:
        path = FIRMWARE / entry['path']
        if not path.exists():
            pytest.skip('staged firmware unavailable')
        blob = path.read_bytes()
        assert len(blob) == entry['size'], entry['path']
        assert hashlib.sha256(blob).hexdigest() == entry['sha256'], entry['path']


def test_board_data_covers_the_reported_board_id():
    """The device reports qmi-chip-id 0 / qmi-board-id 0xff, like the Xiaomi
    Pad 5 Pro; the generic 255 entry is the one ath11k then picks."""
    board = FIRMWARE / 'QCA6390/hw2.0/board-2.bin'
    if not board.exists():
        pytest.skip('staged firmware unavailable')
    blob = board.read_bytes()
    # Names are padded inside the container, so match the prefixes.
    assert b'bus=pci,qmi-chip-id=0,qmi-board-id=255' in blob
    assert b'bus=pci,qmi-chip-id=0,qmi-board-id=4' in blob
    assert b'bus=pci,qmi-chip-id=0,qmi-board-id=255,variant=Xiaomi_Pad_5Pro' in blob
