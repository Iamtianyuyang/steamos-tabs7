"""Generated panel data must match the profile and the driver must use it."""
import importlib.util
import json
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[2]
KERNEL = ROOT / 'linux-sm8250-v6.2'
PROFILE = ROOT / 'hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json'
HEADER = KERNEL / 'drivers/gpu/drm/panel/panel-novatek-nt36523-gts7lwifi.h'
DRIVER = KERNEL / 'drivers/gpu/drm/panel/panel-novatek-nt36523.c'


def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


emitter = load('emit_panel_seq', 'scripts/steamos/emit-panel-seq.py')


class GeneratedHeaderTest(unittest.TestCase):
    def setUp(self):
        self.profile = json.loads(PROFILE.read_text())
        self.header = HEADER.read_text()
        self.driver = DRIVER.read_text()
        self.numbers = emitter.mode_numbers(self.profile)

    def test_header_is_regenerated_from_the_profile(self):
        self.assertEqual(self.header, emitter.emit(self.profile),
                         'run scripts/steamos/emit-panel-seq.py after changing the profile')

    def test_every_vendor_command_is_emitted_in_order(self):
        rows = re.findall(r'\{\s*(\d+),\s*(\d+),\s*\{([^}]*)\} \},', self.header)
        commands = self.profile['init_sequence']['commands']
        self.assertEqual(len(rows), len(commands))
        for row, command in zip(rows, commands):
            self.assertEqual(int(row[0]), len(command['payload']) - 1)
            self.assertEqual(int(row[1]), command['wait'][0])
            payload = [int(token.strip().rstrip(','), 16) for token in row[2].split('0x')[1:]]
            self.assertEqual(payload, command['payload'][1:])

    def test_mode_uses_the_full_panel_and_doubled_porches(self):
        timing = self.profile['timing']
        for field, value in (
                ('hdisplay', timing['width'] * 2),
                ('hsync_start', timing['width'] * 2 + timing['h_front_porch'] * 2),
                ('htotal', timing['width'] * 2 + (timing['h_front_porch'] + timing['h_pulse_width'] +
                                                  timing['h_back_porch']) * 2),
                ('vdisplay', timing['height']),
                ('vtotal', timing['height'] + timing['v_front_porch'] + timing['v_pulse_width'] +
                 timing['v_back_porch'])):
            self.assertRegex(self.header, r'\.%s\s*=\s*%d,' % (field, value), field)
        self.assertRegex(self.header, r'\.clock\s*=\s*%d,' % self.numbers['clock'])

    def test_reset_delay_and_brightness_come_from_the_profile(self):
        self.assertIn('#define PPA957DB1_RESET_DELAY_MS\t12', self.header)
        self.assertIn('#define PPA957DB1_BRIGHTNESS_MAX\t462', self.header)
        self.assertIn(f'#define PPA957DB1_INIT_CMDS\t\t{len(self.profile["init_sequence"]["commands"])}',
                      self.header)

    def test_driver_replays_the_generated_sequence(self):
        self.assertIn('#include "panel-novatek-nt36523-gts7lwifi.h"', self.driver)
        self.assertIn('nt36523_send_init_sequence', self.driver)
        self.assertIn('mipi_dsi_dcs_write_buffer', self.driver)
        self.assertNotIn('placeholder', self.driver.lower())
        self.assertNotIn('TODO: fill from downstream', self.driver)

    def test_driver_declares_the_cphy_link(self):
        self.assertIn('ctx->dsi[i]->lanes = 3;', self.driver)
        self.assertIn('MIPI_DSI_MODE_VIDEO_BURST', self.driver)
        self.assertIn('MIPI_DSI_FMT_RGB888', self.driver)

    def test_sleep_out_and_display_on_are_not_sent_twice(self):
        self.assertNotIn('mipi_dsi_dcs_exit_sleep_mode', self.driver)
        self.assertNotIn('mipi_dsi_dcs_set_display_on', self.driver)
        dcs = [command['payload'][1] for command in self.profile['init_sequence']['commands']
               if len(command['payload']) > 1]
        self.assertIn(0x11, dcs)
        self.assertIn(0x29, dcs)


if __name__ == '__main__':
    unittest.main()
