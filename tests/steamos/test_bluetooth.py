"""Bluetooth on 6.18: QCA6390 BT half on uart6, enable line on TLMM 76.

The expectations come from the vendor board dtsi (bluetooth_gts7l_common), not
from the live vendor DT, which reports a different enable pin.
"""
from pathlib import Path
import json
import re

import pytest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'
DTS = KERNEL / 'arch/arm64/boot/dts/qcom/sm8250-samsung-gts7-common.dtsi'
VENDOR = ROOT / 'out/vendor-kernel-prep/src/arch/arm64/boot/dts/samsung/drivers/bluetooth_gts7l_common.dtsi'
QUPV3 = ROOT / 'out/vendor-kernel-prep/src/arch/arm64/boot/dts/vendor/qcom/kona-qupv3.dtsi'
CONFIG = ROOT / 'hardware/samsung/gts7lwifi/kernel/configs/steamos-bluetooth-6.18.config'
FIRMWARE = ROOT / 'out/firmware/bluetooth'
INITRAMFS = ROOT / 'scripts/build/mainline/build-initramfs.sh'


def block(text, start):
    begin = text.index(start)
    depth = 0
    for i in range(text.index('{', begin), len(text)):
        depth += {'{': 1, '}': -1}.get(text[i], 0)
        if depth == 0:
            return text[begin:i + 1]
    raise AssertionError(start)


def vendor():
    if not VENDOR.exists():
        pytest.skip('prepared vendor source unavailable')
    return VENDOR.read_text()


def uart6():
    return block(DTS.read_text(), '&uart6 {')


def bt():
    return block(uart6(), 'bluetooth {')


def test_bt_uart_is_the_vendor_serial_engine():
    """Vendor enables qupv3_se6_4uart, which kona-qupv3 defines at 0x998000."""
    assert '&qupv3_se6_4uart {' in vendor()
    if not QUPV3.exists():
        pytest.skip('prepared vendor qupv3 unavailable')
    node = block(QUPV3.read_text(), 'qupv3_se6_4uart:')
    assert 'reg = <0x998000' in node
    dtsi = (KERNEL / 'arch/arm64/boot/dts/qcom/sm8250.dtsi').read_text()
    assert 'uart6: serial@998000' in dtsi
    assert 'status = "okay";' in uart6()


def test_enable_gpio_matches_the_board_dtsi():
    """TLMM 76 is BT_EN; the live DT's GPIO 21 sits in the TZ fingerprint range.

    The line is owned by the PMU node: qca6390 always goes through the pwrseq
    "bluetooth" target, and that provider drives bt-enable-gpios.
    """
    text = vendor()
    assert 'qca,bt-reset-gpio = <&tlmm 76 0>;' in text
    assert 'pins = "gpio76";' in block(text, 'bt_en_sleep:')
    pmu = block(DTS.read_text(), 'qca6390-pmu {')
    assert 'bt-enable-gpios = <&tlmm 76 GPIO_ACTIVE_HIGH>;' in pmu
    assert 'pinctrl-0 = <&wlan_en_default>, <&bt_en_default>;' in pmu
    assert 'pins = "gpio76";' in block(DTS.read_text(), 'bt_en_default:')
    assert 'enable-gpios' not in bt(), 'pwrseq owns the line, not the serdev node'


def test_vendor_gps_uart_is_not_reused_for_bluetooth():
    """&qupv3_se3_4uart (0x98c000) is Samsung's GPS UART, not the BT one."""
    gps = ROOT / 'out/vendor-kernel-prep/src/arch/arm64/boot/dts/samsung/drivers/gps_bcm47531.dtsi'
    if not gps.exists():
        pytest.skip('prepared vendor gps dtsi unavailable')
    assert 'qupv3_se3_4uart: qcom,qup_uart@98c000' in gps.read_text()
    dts = DTS.read_text()
    assert 'serial@98c000' not in dts


def test_bt_node_supplies_satisfy_the_pwrseq_match():
    """hci_qca's own vreg list for qca6390 is empty, but the pwrseq provider is
    found through vddaon-supply, so the PMU LDO handles must be on this node."""
    source = (KERNEL / 'drivers/bluetooth/hci_qca.c').read_text()
    assert '.num_vregs = 0' in block(source, 'qca_soc_data_qca6390')
    assert 'devm_pwrseq_get(&serdev->dev,' in source
    node = bt()
    for supply in ('vddaon', 'vddrfacmn', 'vddbtcmx', 'vddrfa0p8',
                   'vddrfa1p2', 'vddrfa1p7'):
        assert f'{supply}-supply = <&vreg_pmu_' in node, supply
    # Each handle must resolve to an LDO nested under the PMU node.
    regulators = block(block(DTS.read_text(), 'qca6390-pmu {'), 'regulators {')
    for label in ('vreg_pmu_aon_0p59', 'vreg_pmu_btcmx_0p85', 'vreg_pmu_rfa_1p7'):
        assert f'{label}: ldo' in regulators, label


def test_firmware_names_are_the_upstream_ones():
    node = bt()
    # The driver prefixes "qca/", so the DT carries bare names; a "qca/" here
    # turns into qca/qca/... and the load fails.
    assert 'firmware-name = "htnv20.bin", "htbtfw20.tlv";' in node
    assert 'qca/htnv20.bin' not in node
    # The kernel side prefixes the firmware directory itself (btqca/qca_ setup
    # paths), which the doubled "qca/qca/..." failure in the first boot showed.
    manifest = FIRMWARE / 'manifest.json'
    if not manifest.exists():
        pytest.skip('staged firmware unavailable')
    listed = {entry['path'] for entry in json.loads(manifest.read_text())['files']}
    for name in ('qca/htnv20.bin', 'qca/htbtfw20.tlv'):
        assert name in listed, name


def test_staged_firmware_matches_its_manifest():
    import hashlib
    manifest = FIRMWARE / 'manifest.json'
    if not manifest.exists():
        pytest.skip('staged firmware unavailable')
    for entry in json.loads(manifest.read_text())['files']:
        path = FIRMWARE / entry['path']
        if not path.exists():
            pytest.skip('staged firmware unavailable')
        blob = path.read_bytes()
        assert len(blob) == entry['size'], entry['path']
        assert hashlib.sha256(blob).hexdigest() == entry['sha256'], entry['path']


def test_initramfs_ships_the_bluetooth_firmware():
    script = INITRAMFS.read_text()
    assert 'out/firmware/bluetooth' in script
    for name in ('htnv20.bin', 'htbtfw20.tlv'):
        assert name in script, name
    assert 'lib/firmware/qca' in script


def test_config_asks_for_the_hci_uart_stack():
    text = CONFIG.read_text()
    for symbol in ('CONFIG_SERIAL_DEV_BUS', 'CONFIG_SERIAL_DEV_CTRL_TTYPORT',
                   'CONFIG_BT', 'CONFIG_BT_HCIUART',
                   'CONFIG_BT_HCIUART_SERDEV', 'CONFIG_BT_HCIUART_QCA',
                   # Without BR/EDR there is no SCO socket and every profile
                   # connect fails with "Protocol not available".
                   'CONFIG_BT_BREDR', 'CONFIG_BT_LE', 'CONFIG_BT_RFCOMM',
                   'CONFIG_BT_BNEP', 'CONFIG_BT_HIDP',
                   # BT_HIDP depends on the HID core, LE HID goes through uhid.
                   'CONFIG_HID_SUPPORT', 'CONFIG_HID', 'CONFIG_UHID',
                   # BlueZ needs AF_ALG for IRK generation.
                   'CONFIG_CRYPTO_USER_API', 'CONFIG_CRYPTO_USER_API_HASH'):
        assert re.search(rf'^{symbol}=y$', text, re.M), symbol
    declared = set()
    for path in KERNEL.glob('**/Kconfig*'):
        if path.is_file():
            declared |= set(re.findall(r'^(?:menu)?config (\w+)$',
                                       path.read_text(errors='ignore'), re.M))
    for symbol in re.findall(r'^CONFIG_(\w+)=y$', text, re.M):
        assert symbol in declared, symbol


def test_local_bd_address_is_provided_and_honoured():
    """The NVM in linux-firmware has no per-device address, so the DT carries
    one and hci_qca has to ask the core to use it."""
    node = bt()
    address = re.search(r'local-bd-address = \[ ([0-9a-f ]+)\];', node)
    assert address, 'local-bd-address missing'
    raw = [int(x, 16) for x in address.group(1).split()]
    assert len(raw) == 6
    # The property is little endian, so the address's first octet is the last
    # property byte; that one must be a locally administered unicast address
    # (top two bits 10b), i.e. 02:00:00:00:5a:ad here.
    first = raw[5]
    assert first & 0x02, 'must be a locally administered address'
    assert not first & 0x01, 'must be unicast'
    driver = (KERNEL / 'drivers/bluetooth/hci_qca.c').read_text()
    assert 'local-bd-address' in driver
    assert 'HCI_QUIRK_USE_BDADDR_PROPERTY' in driver
    probe = block(driver, 'static int qca_serdev_probe(')
    assert 'HCI_QUIRK_USE_BDADDR_PROPERTY' in probe
    assert 'bdaddr_property_broken' in probe
