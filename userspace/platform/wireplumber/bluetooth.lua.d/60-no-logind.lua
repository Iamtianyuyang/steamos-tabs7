-- WirePlumber on this native Linux has no systemd-logind. The stock
-- bluez monitor only creates its SPA monitor when the logind plugin
-- reports an active seat, which never happens here, so no A2DP
-- MediaEndpoint1 is ever registered and BlueZ has no audio profile.
-- Disable the logind gate; the monitor is then created unconditionally.
bluez_monitor.properties["with-logind"] = false
