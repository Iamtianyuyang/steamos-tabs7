"""The 6.18 DTS C-PHY tuning must match the extracted vendor panel profile."""
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.18'
DTS = KERNEL / 'arch/arm64/boot/dts/qcom/sm8250-samsung-gts7-common.dtsi'
PROFILE = ROOT / 'hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json'
DRIVER_PARSE = KERNEL / 'drivers/gpu/drm/msm/dsi/phy/dsi_phy.c'
DRIVER_APPLY = KERNEL / 'drivers/gpu/drm/msm/dsi/phy/dsi_phy_7nm.c'
BINDING = KERNEL / 'Documentation/devicetree/bindings/display/msm/dsi-phy-7nm.yaml'

# vendor property name -> device tree property
PROPERTY_MAP = {
    'phy_vreg_ctrl_0': 'qcom,cphy-vreg-ctrl-0',
    'phy_ctrl2': 'qcom,cphy-cmn-ctrl-2',
    'phy_offset_top_ctrl': 'qcom,cphy-rescode-offset-top',
    'phy_offset_bot_ctrl': 'qcom,cphy-rescode-offset-bot',
    'phy_offset_mid_ctrl': 'qcom,cphy-rescode-offset-mid',
    'phy_str_swi_cal_sel_ctrl': 'qcom,cphy-str-swi-cal-sel',
}


def node(text, label):
    match = re.search(r'&%s\s*\{(.*?)\n\};' % re.escape(label), text, re.S)
    if not match:
        raise AssertionError(f'node &{label} not found')
    return match.group(1)


class CphyTuningTest(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(PROFILE.read_text())
        self.dts = DTS.read_text()
        self.overrides = self.profile['samsung_phy_overrides']

    def test_both_phys_declare_cphy(self):
        for label in ('mdss_dsi0_phy', 'mdss_dsi1_phy'):
            self.assertIn('phy-type = <11>', node(self.dts, label), label)

    def test_override_values_match_the_vendor_profile(self):
        for label in ('mdss_dsi0_phy', 'mdss_dsi1_phy'):
            body = node(self.dts, label)
            for vendor_key, dt_key in PROPERTY_MAP.items():
                match = re.search(r'%s = <(0x[0-9a-fA-F]+|\d+)>;' % re.escape(dt_key), body)
                self.assertIsNotNone(match, f'{label}: {dt_key} is missing')
                self.assertEqual(int(match.group(1), 0), self.overrides[vendor_key],
                                 f'{label}: {dt_key} must be 0x{self.overrides[vendor_key]:x}')

    def test_endpoints_use_the_profile_lane_count(self):
        lanes = self.profile['link']['lanes']
        expected = '<0 1 2>' if lanes == 3 else '<0 1 2 3>'
        matches = re.findall(r'data-lanes = (<[^>]+>)', self.dts)
        self.assertTrue(matches)
        for match in matches:
            self.assertEqual(match, expected, 'C-PHY link is 3 trios per the profile')

    def test_driver_reads_exactly_the_documented_properties(self):
        parse = DRIVER_PARSE.read_text()
        for dt_key in PROPERTY_MAP.values():
            self.assertIn(dt_key, parse, f'{dt_key} is not parsed')
        apply = DRIVER_APPLY.read_text()
        self.assertIn('dsi_phy_7nm_apply_cphy_overrides', apply)
        self.assertIn('msm_dsi_phy_cphy_override', apply)
        self.assertIn('dsi_phy_7nm_apply_cphy_overrides(phy, base)', apply)

    def test_binding_documents_every_override(self):
        binding = BINDING.read_text()
        for dt_key in PROPERTY_MAP.values():
            self.assertIn(dt_key, binding, f'{dt_key} is not in the binding')

    def test_overrides_are_only_applied_in_cphy_mode(self):
        apply = DRIVER_APPLY.read_text()
        helper = apply[apply.index('static void dsi_phy_7nm_apply_cphy_overrides'):]
        helper = helper[:helper.index('\n}\n')]
        self.assertIn('if (!phy->cphy_mode || !override->mask)', helper)


if __name__ == '__main__':
    unittest.main()
