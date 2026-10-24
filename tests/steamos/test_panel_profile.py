"""The committed panel profile must stay faithful to the vendor source."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    'extract_panel_profile', ROOT / 'scripts/steamos/extract-panel-profile.py')
extractor = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extractor)

PROFILE = ROOT / 'hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json'
VENDOR_SOURCE = (ROOT / 'out/vendor-kernel-prep/src' / extractor.VENDOR_NODE /
                 extractor.SOURCE_FILES[0])

FIXTURE = """
&mdss_mdp {
	panel: panel {
		qcom,mdss-dsi-panel-name = "fixture_panel";
		qcom,mdss-dsi-bpp = <24>;
		qcom,mdss-dsi-panel-type = "dsi_video_mode";
		qcom,mdss-dsi-traffic-mode = "burst_mode";
		qcom,mdss-dsi-color-order = "rgb_swap_rgb";
		qcom,mdss-pan-physical-width-dimension = <148>;
		qcom,mdss-pan-physical-height-dimension = <236>;
		qcom,mdss-dsi-lane-0-state;
		qcom,mdss-dsi-lane-1-state;
		qcom,mdss-dsi-lane-2-state;
		qcom,mdss-dsi-reset-sequence = <0 12>, <1 12>;
		qcom,mdss-dsi-lp11-init;
		qcom,panel-cphy-mode;
		samsung,panel-vendor = "CST";
		samsung,disp-model = "FIXTURE";
		samsung,phy_vreg_ctrl_0 = <0x50>;
		qcom,mdss-dsi-bl-min-level = <4>;
		qcom,mdss-dsi-bl-max-level = <462>;
		qcom,mdss-brightness-max-level = <462>;
		qcom,dsi-supported-dfps-list = <120 60>;
		samsung,brightness_tx_cmds_revA = [
			39 01 00 00 00 00 03 51 00 00 /* 8bit brightness */
		];
		qcom,mdss-dsi-display-timings {
			wqxga@0 {
				qcom,display-topology = <2 0 2>;
				qcom,mdss-dsi-panel-width = <800>;
				qcom,mdss-dsi-panel-height = <2560>;
				qcom,mdss-dsi-panel-framerate = <120>;
				qcom,mdss-dsi-h-pulse-width = <2>;
				qcom,mdss-dsi-h-back-porch = <30>;
				qcom,mdss-dsi-h-front-porch = <84>;
				qcom,mdss-dsi-h-sync-skew = <0>;
				qcom,mdss-dsi-v-pulse-width = <1>;
				qcom,mdss-dsi-v-back-porch = <7>;
				qcom,mdss-dsi-v-front-porch = <26>;
				qcom,mdss-dsi-panel-clockrate = <998000000>;
				qcom,mdss-dsi-panel-phy-timings = [00 24 0A 0A];
				qcom,mdss-dsi-on-command= [
					15 00 00 00 00 00 02 FF 10
					39 01 00 00 00 00 03 51 00 00 /* inline comment */
				];
			};
		};
	};
};
"""


class ExtractionTest(unittest.TestCase):
    def test_fixture_is_parsed_into_the_reviewed_shape(self):
        profile = extractor.extract(FIXTURE)
        self.assertEqual(profile['panel']['name'], 'fixture_panel')
        self.assertEqual(profile['link']['lanes'], 3)
        self.assertTrue(profile['link']['cphy'])
        self.assertEqual(profile['link']['topology'], [2, 0, 2])
        self.assertEqual(profile['timing']['width'], 800)
        self.assertEqual(profile['timing']['framerate'], 120)
        self.assertEqual(profile['timing']['dfps_list'], [120, 60])
        self.assertEqual(profile['link']['reset_sequence'], [[0, 12], [1, 12]])
        self.assertEqual(profile['samsung_phy_overrides'], {'phy_vreg_ctrl_0': 0x50})

    def test_command_groups_are_split_per_line_with_comments_stripped(self):
        profile = extractor.extract(FIXTURE)
        commands = profile['init_sequence']['commands']
        self.assertEqual([command['raw'] for command in commands],
                         [[0x15, 0, 0, 0, 0, 0, 2, 0xFF, 0x10],
                          [0x39, 1, 0, 0, 0, 0, 3, 0x51, 0, 0]])
        self.assertEqual(profile['brightness']['dcs_command'],
                         [[0x39, 1, 0, 0, 0, 0, 3, 0x51, 0, 0]])

    def test_missing_required_property_is_an_error(self):
        with self.assertRaises(ValueError):
            extractor.extract(FIXTURE.replace('qcom,mdss-dsi-display-timings', 'qcom,other'))


class CommittedProfileTest(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(PROFILE.read_text())

    def test_profile_records_the_vendor_authority(self):
        self.assertEqual(self.profile['panel']['display_model'], 'PPA957DB1')
        self.assertEqual(self.profile['link']['lanes'], 3)
        self.assertTrue(self.profile['link']['cphy'])
        self.assertEqual(self.profile['timing']['width'], 800)
        self.assertEqual(self.profile['timing']['height'], 2560)
        self.assertEqual(self.profile['timing']['framerate'], 120)
        self.assertEqual(self.profile['init_sequence']['count'], 359)
        self.assertIn('phy_vreg_ctrl_0', self.profile['samsung_phy_overrides'])
        self.assertEqual(self.profile['provenance']['files'][extractor.SOURCE_FILES[0]]['sha256'],
                         'c33f3069bfdc7c32a7e385aa478bef6a891ac998da7b8e1d59f3843fa09c7cca')

    def test_brightness_uses_dcs_0x51(self):
        payload = self.profile['brightness']['dcs_command'][0]
        self.assertEqual(payload[7], 0x51)

    @unittest.skipUnless(VENDOR_SOURCE.is_file(), 'vendor kernel source is not present')
    def test_committed_profile_matches_the_vendor_source(self):
        fresh = extractor.extract(VENDOR_SOURCE.read_text())
        for section in ('panel', 'link', 'timing', 'brightness', 'samsung_phy_overrides',
                        'init_sequence'):
            self.assertEqual(fresh[section], self.profile[section], section)


if __name__ == '__main__':
    unittest.main()
