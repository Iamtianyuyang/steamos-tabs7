#!/usr/bin/env python3
"""Extract the authoritative SM-T870 panel profile from the vendor kernel tree.

The downstream 4.19 source is the only place that carries the real DSI
timings, lane configuration and init sequence for this panel. This script turns
them into one reviewable JSON document so a mainline 6.18 driver can be written
against known values instead of guesses.

Input is the vendor tree under out/ (ignored, not committed):

    out/vendor-kernel-prep/src/techpack/display/msm/samsung/NT36523_PPA957DB1/

Read-only; writes one JSON file. Run it again after refreshing the vendor tree
and commit the diff when the panel data actually changed.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[2]
VENDOR_NODE = 'techpack/display/msm/samsung/NT36523_PPA957DB1'
SOURCE_FILES = ('dsi_panel_NT36523_PPA957DB1_wqxga_video.dtsi',
                'ss_dsi_panel_NT36523_PPA957DB1.c',
                'ss_dsi_panel_NT36523_PPA957DB1.h')


def sha(data):
    return hashlib.sha256(data if isinstance(data, bytes) else data.encode()).hexdigest()


def block(text, opening_index):
    """Return the text between the brace at opening_index and its match."""
    depth = 0
    for index in range(opening_index, len(text)):
        if text[index] == '{':
            depth += 1
        elif text[index] == '}':
            depth -= 1
            if depth == 0:
                return text[opening_index + 1:index]
    raise ValueError('unbalanced braces')


def number(text, key, required=True, cast=int):
    match = re.search(r'%s\s*=\s*<\s*(-?0x[0-9a-fA-F]+|-?\d+)\s*>' % re.escape(key), text)
    if not match:
        if required:
            raise ValueError(f'missing required property: {key}')
        return None
    value = match.group(1)
    return cast(int(value, 16) if value.lower().startswith(('0x', '-0x')) or '0x' in value.lower()
                else value)


def flag(text, key):
    return re.search(r'%s\s*;' % re.escape(key), text) is not None


def byte_groups(text, key):
    """Parse a vendor TX/RX command property into one hex list per command.

    The downstream DTS writes either one command per line inside one bracket
    (`[15 00 ... 02 FF 10`), or a separate bracket per command. Lines may carry
    C comments, which are stripped.
    """
    match = re.search(r'%s\s*=\s*\[(.*?)\];' % re.escape(key), text, re.S)
    if not match:
        return None
    body = re.sub(r'/\*.*?\*/', ' ', match.group(1), flags=re.S)
    body = re.sub(r'//[^\n]*', ' ', body)
    groups = []
    for chunk in re.split(r'\n|\]\s*\[', body):
        tokens = chunk.split()
        if not tokens:
            continue
        try:
            groups.append([int(token, 16) for token in tokens])
        except ValueError as error:
            raise ValueError(f'{key}: not a hex command group: {chunk.strip()[:60]}') from error
    return groups


def describe_commands(groups):
    """Split vendor command groups into type/last/vc/ack/wait/payload words."""
    commands = []
    for group in groups:
        if len(group) < 7:
            raise ValueError(f'command group too short: {group}')
        commands.append({'type': group[0], 'last': group[1], 'vc': group[2], 'ack': group[3],
                         'wait': group[4:6], 'payload': group[6:], 'raw': group})
    return commands


def extract(dtsi_text):
    timings_start = dtsi_text.index('qcom,mdss-dsi-display-timings')
    timings_block = dtsi_text[timings_start:]
    inner = timings_block[timings_block.index('{'):]
    inner = block(inner, 0)

    lanes = len(re.findall(r'qcom,mdss-dsi-lane-\d-state\s*;', dtsi_text))
    reset_sequence = [[int(value), int(delay)] for value, delay in
                      re.findall(r'<\s*(\d+)\s+(\d+)\s*>', re.search(
                          r'qcom,mdss-dsi-reset-sequence\s*=\s*((?:<\s*\d+\s+\d+\s*>,\s*)*<\s*\d+\s+\d+\s*>)',
                          dtsi_text).group(1))]
    dfps = [int(value) for value in re.search(
        r'qcom,dsi-supported-dfps-list\s*=\s*<([^>]*)>', dtsi_text).group(1).split()]
    on_cmds = byte_groups(dtsi_text, 'qcom,mdss-dsi-on-command')
    if on_cmds is None:
        raise ValueError('missing required property: qcom,mdss-dsi-on-command')

    profile = {
        'panel': {
            'name': re.search(r'qcom,mdss-dsi-panel-name\s*=\s*"([^"]+)"', dtsi_text).group(1),
            'display_model': re.search(r'samsung,disp-model\s*=\s*"([^"]+)"', dtsi_text).group(1),
            'vendor': re.search(r'samsung,panel-vendor\s*=\s*"([^"]+)"', dtsi_text).group(1),
            'bpp': number(dtsi_text, 'qcom,mdss-dsi-bpp'),
            'panel_type': re.search(r'qcom,mdss-dsi-panel-type\s*=\s*"([^"]+)"', dtsi_text).group(1),
            'traffic_mode': re.search(r'qcom,mdss-dsi-traffic-mode\s*=\s*"([^"]+)"', dtsi_text).group(1),
            'color_order': re.search(r'qcom,mdss-dsi-color-order\s*=\s*"([^"]+)"', dtsi_text).group(1),
            'physical_width_mm': number(dtsi_text, 'qcom,mdss-pan-physical-width-dimension'),
            'physical_height_mm': number(dtsi_text, 'qcom,mdss-pan-physical-height-dimension'),
        },
        'link': {
            'lanes': lanes,
            'cphy': flag(dtsi_text, 'qcom,panel-cphy-mode'),
            'topology': [int(value) for value in re.search(
                r'qcom,display-topology\s*=\s*<([^>]*)>', inner).group(1).split()],
            'clockrate_hz': number(inner, 'qcom,mdss-dsi-panel-clockrate'),
            'phy_timings_raw': [int(token, 16) for token in re.search(
                r'qcom,mdss-dsi-panel-phy-timings\s*=\s*\[([^\]]*)\]', inner, re.S).group(1).split()],
            'lp11_init': flag(dtsi_text, 'qcom,mdss-dsi-lp11-init'),
            'rx_eot_ignore': flag(dtsi_text, 'qcom,mdss-dsi-rx-eot-ignore'),
            'tx_eot_append': flag(dtsi_text, 'qcom,mdss-dsi-tx-eot-append'),
            'reset_sequence': reset_sequence,
        },
        'timing': {
            'width': number(inner, 'qcom,mdss-dsi-panel-width'),
            'height': number(inner, 'qcom,mdss-dsi-panel-height'),
            'framerate': number(inner, 'qcom,mdss-dsi-panel-framerate'),
            'h_pulse_width': number(inner, 'qcom,mdss-dsi-h-pulse-width'),
            'h_back_porch': number(inner, 'qcom,mdss-dsi-h-back-porch'),
            'h_front_porch': number(inner, 'qcom,mdss-dsi-h-front-porch'),
            'h_sync_skew': number(inner, 'qcom,mdss-dsi-h-sync-skew'),
            'v_pulse_width': number(inner, 'qcom,mdss-dsi-v-pulse-width'),
            'v_back_porch': number(inner, 'qcom,mdss-dsi-v-back-porch'),
            'v_front_porch': number(inner, 'qcom,mdss-dsi-v-front-porch'),
            'dfps_list': dfps,
        },
        'brightness': {
            'min_level': number(dtsi_text, 'qcom,mdss-dsi-bl-min-level'),
            'max_level': number(dtsi_text, 'qcom,mdss-dsi-bl-max-level'),
            'max_level_brightness': number(dtsi_text, 'qcom,mdss-brightness-max-level'),
            'dcs_command': byte_groups(dtsi_text, 'samsung,brightness_tx_cmds_revA'),
        },
        'samsung_phy_overrides': {
            key: value for key, value in (
                (match.group(1), int(match.group(2), 16))
                for match in re.finditer(r'samsung,(phy_[a-z0-9_]+)\s*=\s*<(0x[0-9a-fA-F]+)>', dtsi_text))
        },
        'init_sequence': {
            'count': len(on_cmds),
            'payload_sha256': sha(json.dumps(on_cmds)),
            'commands': describe_commands(on_cmds),
        },
    }
    return profile


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--vendor-root', type=Path,
                        default=ROOT / 'out/vendor-kernel-prep/src',
                        help='vendor kernel source root (default: out/vendor-kernel-prep/src)')
    parser.add_argument('--output', type=Path,
                        default=ROOT / 'hardware/samsung/gts7lwifi/panel/nt36523-ppa957db1.json')
    args = parser.parse_args()

    node = args.vendor_root / VENDOR_NODE
    missing = [name for name in SOURCE_FILES if not (node / name).is_file()]
    if missing:
        raise SystemExit(f'vendor panel sources missing under {node}: {missing}')
    text = (node / SOURCE_FILES[0]).read_text()
    profile = extract(text)
    profile['provenance'] = {
        'vendor_node': VENDOR_NODE,
        'files': {name: {'bytes': (node / name).stat().st_size,
                         'sha256': sha((node / name).read_bytes())} for name in SOURCE_FILES},
        'extracted_by': 'scripts/steamos/extract-panel-profile.py',
        'note': 'values are the downstream 4.19 panel driver properties; the mainline driver must match them',
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(profile, indent=2) + '\n')

    timing = profile['timing']
    link = profile['link']
    print(f"{profile['panel']['name']} ({profile['panel']['display_model']})")
    print(f"  link      : {link['lanes']} lanes, cphy={link['cphy']}, topology={link['topology']}, "
          f"clock={link['clockrate_hz']}")
    print(f"  timing    : {timing['width']}x{timing['height']} @ {timing['framerate']} Hz, "
          f"h {timing['h_pulse_width']}/{timing['h_back_porch']}/{timing['h_front_porch']}, "
          f"v {timing['v_pulse_width']}/{timing['v_back_porch']}/{timing['v_front_porch']}")
    print(f"  init      : {profile['init_sequence']['count']} command groups "
          f"({profile['init_sequence']['payload_sha256'][:16]}…)")
    print(f"  phy tuning: {profile['samsung_phy_overrides']}")
    print(f'wrote {args.output.relative_to(ROOT)}')


if __name__ == '__main__':
    try:
        main()
    except (OSError, ValueError, KeyError, AttributeError) as error:
        sys.exit(f'Panel profile extraction failed: {error}')
