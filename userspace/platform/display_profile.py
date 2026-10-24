"""Validated SM-T870 display profiles shared by Phoc and nested GNOME."""
import configparser
from pathlib import Path

SELECTOR = Path("/etc/tabs7linux/gnome-display")
PROFILES = {
    "scaled-60": (1280, 800, 60, 2, 1),
    "scaled-120": (1280, 800, 120, 2, 1),
    "native-60": (2560, 1600, 60, 1, 2),
    "native-120": (2560, 1600, 120, 1, 2),
}


def load_profile():
    if SELECTOR.is_symlink():
        raise ValueError("GNOME display selector must not be a symlink")
    name = SELECTOR.read_text().strip() if SELECTOR.exists() else "scaled-60"
    if name not in PROFILES:
        raise ValueError(f"Unknown GNOME display profile: {name}")
    width, height, hz, outer_scale, gnome_scale = PROFILES[name]
    return dict(name=name, width=width, height=height, hz=hz,
                outer_scale=outer_scale, gnome_scale=gnome_scale)


def write_phoc_config(destination, profile):
    config = configparser.ConfigParser()
    config.read(Path(__file__).with_name("phoc.ini"))
    config["output:DSI-1"]["mode"] = f"1600x2560@{profile['hz']}Hz"
    config["output:DSI-1"]["scale"] = str(profile["outer_scale"])
    with destination.open("w") as output:
        config.write(output)
