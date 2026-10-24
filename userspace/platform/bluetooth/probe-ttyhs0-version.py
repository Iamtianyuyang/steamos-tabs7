#!/usr/bin/env python3
"""Inspect Tab S7 BT UART, or issue exactly one read-version HCI command.

No Bluetooth socket, RF/power change, firmware download, RESET, or controller
baud-rate command is issued. --query temporarily configures ttyHS0 to 115200 8N1 RTS/CTS and
balances the vendor clock vote. Run under a separate 6-second timeout.
"""
import argparse
import array
import fcntl
import json
import os
from pathlib import Path
import platform
import select
import signal
import stat
import struct
import termios
import time

UART = Path("/dev/ttyHS0")
SYS = Path("/sys/class/tty/ttyHS0")
RELEASE = "4.19.325-cip136-st20-perf-g4478511c4b37"
TIOCPMGET, TIOCPMPUT = 0x544D, 0x544E
TIOCGETD, TIOCEXCL, TIOCNXCL = 0x5424, 0x540C, 0x540D


def log(phase, **values):
    print(json.dumps({"phase": phase, **values}, ensure_ascii=False), flush=True)


def text(path):
    return Path(path).read_bytes().replace(b"\0", b";").decode().strip(";\n ")


def owners(rdev, exclude_fd=None):
    found = []
    for process in Path("/proc").iterdir():
        if not process.name.isdecimal():
            continue
        try:
            for fd in (process / "fd").iterdir():
                if int(process.name) == os.getpid() and fd.name == str(exclude_fd):
                    continue
                try:
                    info = fd.stat()
                    if stat.S_ISCHR(info.st_mode) and info.st_rdev == rdev:
                        found.append({"pid": int(process.name), "fd": int(fd.name),
                                      "comm": text(process / "comm")})
                except (FileNotFoundError, ProcessLookupError):
                    pass
        except (FileNotFoundError, ProcessLookupError):
            pass
    return found


def inspect():
    info = UART.lstat()
    node = str((SYS / "device").resolve(strict=True))
    facts = {"kernel": os.uname().release, "machine": platform.machine(),
             "uart": str(UART), "sys_device": node, "character_node": stat.S_ISCHR(info.st_mode),
             "node_rdev": [os.major(info.st_rdev), os.minor(info.st_rdev)],
             "sys_dev": text(SYS / "dev"), "compatible": text(SYS / "device/of_node/compatible"),
             "status": text(SYS / "device/of_node/status"),
             "driver": (SYS / "device/driver").resolve(strict=True).name,
             "uart_owners": owners(info.st_rdev), "hci_devices": [], "rfkill": []}
    facts["hci_devices"] = [item.name for item in Path("/sys/class/bluetooth").glob("hci*")]
    for entry in Path("/sys/class/rfkill").glob("rfkill*"):
        facts["rfkill"].append({"path": str(entry), **{name: text(entry / name)
                               for name in ("name", "type", "soft", "hard")}})
    reasons = []
    if not facts["character_node"] or UART.is_symlink():
        reasons.append("UART is not a direct character node")
    if facts["sys_dev"] != ":".join(str(x) for x in facts["node_rdev"]):
        reasons.append("UART node does not match sysfs dev")
    if not node.endswith("/998000.qcom,qup_uart"):
        reasons.append("UART is not the verified 0x998000 controller")
    if facts["compatible"] != "qcom,msm-geni-serial-hs" or facts["status"] not in ("ok", "okay"):
        reasons.append("UART DT compatible/status mismatch")
    if facts["driver"] != "msm_geni_serial":
        reasons.append("UART is not bound to the reviewed vendor GENI driver")
    if facts["kernel"] != RELEASE or facts["machine"] not in ("aarch64", "arm64"):
        reasons.append("Unexpected kernel or architecture")
    if facts["uart_owners"] or facts["hci_devices"]:
        reasons.append("UART or HCI controller already in use")
    bt = [entry for entry in facts["rfkill"] if entry["name"] == "bt_power" and entry["type"] == "bluetooth"]
    if len(bt) != 1 or bt[0]["soft"] != "0" or bt[0]["hard"] != "0":
        reasons.append("bt_power must already be unblocked by the operator")
    wlan = [entry for entry in facts["rfkill"] if entry["type"] == "wlan"]
    if not wlan or any(entry["soft"] != "0" or entry["hard"] != "0" for entry in wlan):
        reasons.append("Existing Wi-Fi must remain unblocked")
    facts["query_refusal_reasons"] = reasons
    return facts, info.st_rdev


def write_all(fd, data, deadline):
    sent = 0
    while sent < len(data):
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([], [fd], [], remaining)[1]:
            raise TimeoutError("UART write exceeded the shared 2-second deadline")
        try:
            count = os.write(fd, data[sent:])
        except BlockingIOError:
            continue
        if count <= 0:
            raise RuntimeError("UART zero-length write")
        sent += count


def decode_version(event, payload, query):
    if query == "local-version" and event == 0x0E and len(payload) >= 4 and payload[1:3] == b"\x01\x10" and payload[3]:
        return {"reply_kind": "HCI_Read_Local_Version_error", "status": payload[3]}
    if query == "local-version" and event == 0x0E and len(payload) == 12 and payload[1:3] == b"\x01\x10":
        return {"reply_kind": "HCI_Read_Local_Version", "status": payload[3],
                "hci_version": payload[4], "hci_revision": struct.unpack_from("<H", payload, 5)[0],
                "lmp_version": payload[7], "manufacturer": struct.unpack_from("<H", payload, 8)[0],
                "lmp_subversion": struct.unpack_from("<H", payload, 10)[0]}
    # Original btqca.c specifies the VSE structure; mainline Linux (6.18 too) skips
    # one byte before newer QCA version data. The real QCA6390 packet puts
    # the 12-byte version length there, rather than an error status. Match the
    # complete structure, preserving bytes without guessing the chip type.
    if query == "qca-version" and event == 0xFF and len(payload) == 14 and payload[:2] == b"\x00\x02":
        product, patch, rom, soc = struct.unpack_from("<IHHI", payload, 2)
        return {"reply_kind": "QCA_legacy_EDL_version", "product_id": hex(product),
                "patch_version": hex(patch), "rom_version": hex(rom), "soc_id": hex(soc),
                "status": 0}
    if query == "qca-version" and event == 0x0E and len(payload) == 18 and payload[1:6] == b"\x00\xfc\x00\x19\x0c":
        product, patch, rom, soc = struct.unpack_from("<IHHI", payload, 6)
        return {"reply_kind": "QCA_modern_EDL_version", "product_id": hex(product),
                "patch_version": hex(patch), "rom_version": hex(rom), "soc_id": hex(soc),
                "status": 0, "version_info_length": payload[5], "payload_hex": payload.hex()}
    if query == "qca-version" and event == 0x0E and len(payload) == 4 and payload[1:3] == b"\x00\xfc" and payload[3]:
        return {"reply_kind": "QCA_version_command_error", "status": payload[3]}
    return None


def query_version(kind, facts, rdev):
    if os.geteuid() != 0 or facts["query_refusal_reasons"]:
        raise RuntimeError("Query refused: " + "; ".join(facts["query_refusal_reasons"] or ["root required"]))
    fd = None
    old = None
    clock = exclusive = False
    cleanup_errors = []
    deadline = None
    try:
        log("before-open")
        fd = os.open(UART, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        if os.fstat(fd).st_rdev != rdev:
            raise RuntimeError("UART node changed before open")
        fcntl.ioctl(fd, TIOCEXCL)
        exclusive = True
        if owners(rdev, fd):
            raise RuntimeError("Another UART FD owner appeared")
        discipline = array.array("i", [0])
        fcntl.ioctl(fd, TIOCGETD, discipline, True)
        if discipline[0] != 0:
            raise RuntimeError("UART line discipline is not N_TTY")
        old = termios.tcgetattr(fd)
        raw = list(old)
        raw[6] = list(old[6])
        raw[0] = raw[1] = raw[3] = 0
        raw[2] &= ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.HUPCL)
        raw[2] |= termios.CS8 | termios.CLOCAL | termios.CREAD | termios.CRTSCTS
        raw[4] = raw[5] = termios.B115200
        raw[6][termios.VMIN] = raw[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, raw)
        log("before-clock-get", baud=115200, flow_control="RTS/CTS", discipline=discipline[0])
        fcntl.ioctl(fd, TIOCPMGET)
        clock = True
        log("clock-get-complete")
        # Driver shutdown forces ioctl_count back to zero on last close. With
        # no preexisting FD owners and TIOCEXCL, this single vote is balanced.
        deadline = time.monotonic() + 2.0
        command = b"\x01\x01\x10\x00" if kind == "local-version" else b"\x01\x00\xfc\x01\x19"
        write_all(fd, command, deadline)
        log("version-request-sent", query=kind, command_hex=command.hex())
        buffer = bytearray()
        received = acknowledgements = 0
        reply = None
        while time.monotonic() < deadline and reply is None:
            if not select.select([fd], [], [], max(0, deadline - time.monotonic()))[0]:
                break
            try:
                packet = os.read(fd, 1024)
            except BlockingIOError:
                continue
            if not packet:
                raise RuntimeError("UART EOF")
            received += len(packet)
            if received > 8192:
                raise RuntimeError("UART RX limit exceeded")
            buffer.extend(packet)
            while buffer:
                packet_type = buffer[0]
                if packet_type in (0xFC, 0xFD, 0xFE):
                    del buffer[0]
                    log("IBS", byte=hex(packet_type))
                    if packet_type == 0xFD:
                        acknowledgements += 1
                        if acknowledgements > 4:
                            raise RuntimeError("Too many IBS wake requests")
                        write_all(fd, b"\xfc", deadline)
                        log("IBS-wake-ack-sent")
                    continue
                if packet_type != 0x04:
                    raise RuntimeError("Unexpected H4 packet type " + hex(packet_type) + "; no retry")
                if len(buffer) < 3 or len(buffer) < 3 + buffer[2]:
                    break
                size = 3 + buffer[2]
                event, payload = buffer[1], bytes(buffer[3:size])
                log("HCI-event", event=hex(event), payload_hex=payload.hex())
                del buffer[:size]
                decoded = decode_version(event, payload, kind)
                if decoded:
                    reply = decoded
        if reply is None:
            raise TimeoutError("No complete matching version response within 2 seconds; residual=" + buffer.hex())
        log("version-reply", **reply)
        if reply["status"] != 0:
            raise RuntimeError("Controller returned a nonzero HCI status")
        if "soc_id" in reply and (int(reply["soc_id"], 16) == 0 or int(reply["rom_version"], 16) == 0):
            raise RuntimeError("Controller returned a zero SoC or ROM version")
    finally:
        if fd is not None:
            if clock:
                try:
                    log("before-clock-put")
                    fcntl.ioctl(fd, TIOCPMPUT)
                    log("clock-put-complete")
                except OSError as error:
                    cleanup_errors.append("clock-put: " + str(error))
            if old is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, old)
                except OSError as error:
                    cleanup_errors.append("termios-restore: " + str(error))
            if exclusive:
                try:
                    fcntl.ioctl(fd, TIOCNXCL)
                except OSError as error:
                    cleanup_errors.append("exclusive-release: " + str(error))
            try:
                os.close(fd)
            except OSError as error:
                cleanup_errors.append("close: " + str(error))
            log("closed", cleanup_errors=cleanup_errors)
        if cleanup_errors:
            raise RuntimeError("UART cleanup incomplete: " + "; ".join(cleanup_errors))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--inspect", action="store_true", help="default: inspect paths and FD owners only")
    modes.add_argument("--query", choices=("local-version", "qca-version"), help="send one read-only version command")
    args = parser.parse_args()
    try:
        facts, rdev = inspect()
        log("inspect", facts=facts)
        if args.query:
            signal.signal(signal.SIGTERM, lambda _signum, _frame: (_ for _ in ()).throw(RuntimeError("terminated by watchdog")))
            query_version(args.query, facts, rdev)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        log("error", error=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
