#!/usr/bin/env python3
"""Strict Tab S7 volatile QCA6390 bootstrap; default inspection makes no writes.

--bootstrap downloads verified original firmware and a RAM NVM copy, activates
it with a Bluetooth HCI RESET, then keeps the same UART FD alive as H4/N_HCI.
This initial 115200 profile disables SIBS and holds the UART clock vote.
An authenticated suspend lease can temporarily release that vote; it never
changes the firmware profile, H4 attachment or Bluetooth power preference.
"""
import argparse
import array
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import select
import signal
import socket
import stat
import struct
import termios
import time

HERE = Path(__file__).resolve().parent
PROBE_SHA = "0a4922963fc616f2b9ecc00cfe520956f941381b8802db29007cda380ce30d4b"
PATCH_SHA = "40f6f13b397999e6e43dfbe0ea6016b9d84eab6e5090345fb443248004a3c3dd"
NVM_SHA = "b3ee40fd0063061d872e8459c084c0343b8c1a42702dd7dfcfc2044dd73d6ee6"
CONFIG_SHA = "02267534fad767e591c1c809523b8e589bb131c21d25c0731ade662a554d9117"
STEAMOS_IPC_CONFIG_SHA = "7b6104ec713df7192b62f3fccbbd955aa4d7c575985179906c72dcfbbb069916"
STEAMOS_SYSTEMD_CONFIG_SHA = "c4ca8fa1621234598ad80bf71be6263c603a36f6d6484f91a6c425472a0bb808"
TIOCSETD, N_HCI = 0x5423, 15
HCIUARTSETPROTO, HCIUARTGETDEVICE = 0x400455C8, 0x800455CA
TIOCPMACT = 0x544F
SUSPEND_SOCKET = Path("/run/tabs7-bluetooth/suspend.sock")
STOP = False


def sha(data):
    return hashlib.sha256(data).hexdigest()


def load_probe():
    path = HERE / "probe-ttyhs0-version.py"
    if sha(path.read_bytes()) != PROBE_SHA:
        raise RuntimeError("Reviewed UART probe source hash mismatch")
    spec = importlib.util.spec_from_file_location("tabs7_bt_uart_probe", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def log(phase, **fields):
    print(json.dumps({"phase": phase, **fields}, ensure_ascii=False), flush=True)


def firmware(patch_path, nvm_path):
    patch, nvm = patch_path.read_bytes(), nvm_path.read_bytes()
    if sha(patch) != PATCH_SHA or sha(nvm) != NVM_SHA:
        raise RuntimeError("Firmware differs from the verified SM-T870 vendor originals")
    for data, expected_type in ((patch, 1), (nvm, 2)):
        type_length = struct.unpack_from("<I", data)[0]
        if type_length & 255 != expected_type or type_length >> 8 != len(data) - 4:
            raise RuntimeError("Invalid firmware TLV type or length")
    fields = struct.unpack_from("<IIBBBBHHHHI", patch, 4)
    if fields[4] != 3 or fields[6] != 0x10 or fields[7] != 0x200 or fields[8] != 0x5DEF:
        raise RuntimeError("Unexpected patch download mode/product/ROM/patch version")
    tags, offset = {}, 4
    while offset < len(nvm):
        if offset + 12 > len(nvm):
            raise RuntimeError("Truncated NVM tag header")
        tag, length, r1, r2 = struct.unpack_from("<HHII", nvm, offset)
        begin, end = offset + 12, offset + 12 + length
        if end > len(nvm) or tag in tags or r1 or r2:
            raise RuntimeError("Invalid NVM tag bounds/duplicate/reserved field")
        tags[tag] = (begin, length)
        offset = end
    if any(tags.get(tag, (None, None))[1] != size for tag, size in ((2, 6), (17, 6), (27, 9))):
        raise RuntimeError("QCA6390 HAL-format NVM tags do not match")
    p17, _ = tags[17]
    p27, _ = tags[27]
    if nvm[p17:p17 + 6] != bytes.fromhex("151122002e00") or nvm[p27:p27 + 9] != bytes.fromhex("01010a28000000ffff"):
        raise RuntimeError("Unexpected UART/SIBS tag layout")
    return patch, nvm, tags


def address(path):
    raw = path.read_bytes()
    value = raw.strip().decode("ascii")
    if not re.fullmatch(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}", value):
        raise RuntimeError("Factory bt_addr must contain exactly one colon-separated Bluetooth address")
    parts = bytes(int(item, 16) for item in value.split(":"))
    if parts in (b"\0" * 6, b"\xff" * 6):
        raise RuntimeError("Factory Bluetooth address is empty or invalid")
    return parts[::-1], sha(raw)


def restored_core():
    if sha(gzip.decompress(Path("/proc/config.gz").read_bytes())) not in (CONFIG_SHA, STEAMOS_IPC_CONFIG_SHA, STEAMOS_SYSTEMD_CONFIG_SHA):
        raise RuntimeError("Native config differs from the reviewed kernel profiles")
    names = set()
    for line in Path("/proc/kallsyms").read_text().splitlines():
        fields = line.split()
        if len(fields) == 3:
            names.add(fields[2])
    for key in ("hci_sock_ops", "hci_sock_destruct", "hci_sock_bind"):
        if not any(name == key or name.startswith(key + ".") for name in names):
            raise RuntimeError("HCI socket repair not present; do not attach or start BlueZ")
    if not Path("/sys/module/tabs7_hci_uart").is_dir():
        raise RuntimeError("Verified tabs7_hci_uart H4 module must already be loaded")
    if not re.search(r"^n_hci\s+15\s*$", Path("/proc/tty/ldiscs").read_text(), re.M):
        raise RuntimeError("Matching N_HCI line discipline is not registered")


class Transport:
    def __init__(self, fd, probe):
        self.fd, self.probe = fd, probe
        self.rx = bytearray()
        self.rx_total = self.ibs_acks = 0

    def event(self, deadline):
        while True:
            while self.rx:
                kind = self.rx[0]
                if kind in (0xFC, 0xFD, 0xFE):
                    del self.rx[0]
                    log("IBS", byte=hex(kind))
                    if kind == 0xFD:
                        self.ibs_acks += 1
                        if self.ibs_acks > 16:
                            raise RuntimeError("Unexpected persistent IBS traffic during initialization")
                        self.probe.write_all(self.fd, b"\xfc", deadline)
                    continue
                if kind != 4:
                    raise RuntimeError("Unexpected H4 type " + hex(kind))
                if len(self.rx) < 3 or len(self.rx) < 3 + self.rx[2]:
                    break
                size = 3 + self.rx[2]
                event, payload = self.rx[1], bytes(self.rx[3:size])
                del self.rx[:size]
                return event, payload
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.fd], [], [], remaining)[0]:
                raise TimeoutError("HCI response deadline exceeded")
            try:
                data = os.read(self.fd, 1024)
            except BlockingIOError:
                continue
            if not data:
                raise RuntimeError("UART EOF")
            self.rx_total += len(data)
            if self.rx_total > 65536:
                raise RuntimeError("Unexpected initialization RX volume")
            self.rx.extend(data)

    def send(self, opcode, parameters, deadline):
        if len(parameters) > 255:
            raise RuntimeError("Oversized HCI command")
        self.probe.write_all(self.fd, b"\x01" + struct.pack("<HB", opcode, len(parameters)) + parameters, deadline)

    def complete(self, opcode, deadline, subcommand=None):
        while True:
            event, payload = self.event(deadline)
            if event != 0x0E or len(payload) < 4 or payload[1:3] != struct.pack("<H", opcode):
                log("unexpected-event", event=hex(event), payload_hex=payload.hex())
                raise RuntimeError("Unexpected HCI event during ordered initialization")
            if subcommand is not None:
                if len(payload) != 5 or payload[3:] != bytes((0, subcommand)):
                    raise RuntimeError("QCA segment rejected: " + payload.hex())
            elif payload[3] != 0:
                raise RuntimeError("HCI command rejected: " + payload.hex())
            return event, payload

    def version(self):
        deadline = time.monotonic() + 2
        self.send(0xFC00, b"\x19", deadline)
        event, payload = self.event(deadline)
        version = self.probe.decode_version(event, payload, "qca-version")
        if not version or version["status"] or version.get("reply_kind") != "QCA_modern_EDL_version":
            raise RuntimeError("Unexpected QCA6390 version reply: " + payload.hex())
        if (version["product_id"], version["rom_version"], version["soc_id"]) != ("0x10", "0x200", "0x400a0200"):
            raise RuntimeError("Controller is not the verified product10/ROM0200/SOC400a0200")
        return version

    def download(self, data, patch):
        deadline = time.monotonic() + (60 if patch else 20)
        count = (len(data) + 242) // 243
        log("download-begin", kind="patch" if patch else "nvm", bytes=len(data), segments=count)
        for index, offset in enumerate(range(0, len(data), 243)):
            if time.monotonic() >= deadline:
                raise TimeoutError("Firmware phase deadline exceeded")
            chunk = data[offset:offset + 243]
            self.send(0xFC00, bytes((0x1E, len(chunk))) + chunk, min(deadline, time.monotonic() + 2))
            if not patch or index == count - 1:
                self.complete(0xFC00, min(deadline, time.monotonic() + 8), 0x1E)
            if index % 64 == 0 or index == count - 1:
                log("download-progress", kind="patch" if patch else "nvm", completed_segments=index + 1, total_segments=count)
        log("download-complete", kind="patch" if patch else "nvm")


def check_bluez_objects(objects, controller):
    """Do not interrupt a scan or an established Bluetooth connection."""
    adapter = "/org/bluez/" + controller
    properties = objects.get(adapter, {}).get("org.bluez.Adapter1", {})
    if properties.get("Discovering") is not False:
        raise RuntimeError("Bluetooth is discovering or its adapter state is unavailable")
    for path, interfaces in objects.items():
        if path.startswith(adapter + "/") and "org.bluez.Device1" in interfaces:
            if interfaces["org.bluez.Device1"].get("Connected") is not False:
                raise RuntimeError("Bluetooth device is connected or its connection state is unavailable")


def bluez_idle(controller):
    # This runs inside the existing root Ubuntu owner. It reads properties only:
    # no Set(Powered), StopDiscovery, disconnect, reset or rfkill operation.
    from gi.repository import Gio, GLib
    bus = Gio.bus_get_sync(Gio.BusType.SYSTEM, None)
    flags = Gio.DBusCallFlags.NO_AUTO_START
    owner = bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus",
                          "org.freedesktop.DBus", "GetNameOwner",
                          GLib.Variant("(s)", ("org.bluez",)),
                          GLib.VariantType.new("(s)"), flags, 1000, None).unpack()[0]
    objects = bus.call_sync(owner, "/", "org.freedesktop.DBus.ObjectManager",
                            "GetManagedObjects", None,
                            GLib.VariantType.new("(a{oa{sa{sv}}})"),
                            flags, 1000, None).unpack()[0]
    check_bluez_objects(objects, controller)
    return owner


class ClockRestoreError(RuntimeError):
    """A failed vote restoration must terminate the supervised owner."""


class SuspendClock:
    """Balance only this bootstrap's clock vote, keeping UART/H4 attached."""
    def __init__(self, fd, get, put, controller, runtime_status, *, guard=None,
                 ioctl=fcntl.ioctl, timeout=2):
        self.fd, self.get, self.put, self.controller = fd, get, put, controller
        self.runtime_status, self.ioctl, self.timeout = runtime_status, ioctl, timeout
        self.guard = guard or (lambda: bluez_idle(controller))
        self.held = True

    def snapshot(self):
        return {"controller": self.controller, "clock_vote_held": self.held,
                "runtime_status": self.runtime_status(), "owner_pid": os.getpid()}

    def confirm(self, active):
        expected = "active" if active else "suspended"
        deadline = time.monotonic() + self.timeout
        while True:
            if (self.runtime_status() == expected
                    and self.ioctl(self.fd, TIOCPMACT, 0) == int(active)):
                return self.snapshot()
            if STOP or time.monotonic() >= deadline:
                raise RuntimeError("UART did not confirm runtime " + expected)
            time.sleep(0.01)

    def resume(self):
        if not self.held:
            self.ioctl(self.fd, self.get)
            self.held = True
        return self.confirm(True)

    def prepare(self):
        if not self.held:
            raise RuntimeError("Bluetooth clock is already prepared")
        owner = self.guard()
        self.ioctl(self.fd, self.put)
        self.held = False
        try:
            self.confirm(False)
            if self.guard() != owner:
                raise RuntimeError("BlueZ owner changed while preparing suspend")
            return self.confirm(False)
        except BaseException:
            try:
                self.resume()
            except Exception as error:
                raise ClockRestoreError("Failed to restore UART clock after prepare failure") from error
            raise


def private_directory(path):
    path.mkdir(mode=0o700, parents=False, exist_ok=True)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
        raise PermissionError("Bluetooth suspend directory must be root-owned and private")


def root_peer(peer):
    pid, uid, _gid = struct.unpack("3i", peer.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
    if uid != 0 or pid <= 0:
        raise PermissionError("Bluetooth suspend control requires a root peer")
    return pid


def receive_request(peer):
    data = bytearray()
    while not data.endswith(b"\n"):
        chunk = peer.recv(1)
        if not chunk:
            return None
        data.extend(chunk)
        if len(data) > 128:
            raise ValueError("Oversized suspend request")
    value = json.loads(data)
    if not isinstance(value, dict) or set(value) != {"op"}:
        raise ValueError("Expected exactly one suspend control operation")
    return value["op"]


def send_reply(peer, **reply):
    peer.sendall(json.dumps(reply, ensure_ascii=False).encode() + b"\n")


class SuspendServer:
    """One root connection holds the prepare lease across system suspend.

    EOF or twenty seconds of awake time restores the vote. CLOCK_MONOTONIC
    excludes system sleep, so a long successful suspend does not expire it.
    """
    def __init__(self, control, path=SUSPEND_SOCKET, *, lease_seconds=20):
        self.control, self.path, self.lease_seconds = control, Path(path), lease_seconds
        self.client, self.deadline, self.socket_id = None, None, None
        private_directory(self.path.parent)
        self.listener = socket.socket(socket.AF_UNIX)
        try:
            self.listener.bind(str(self.path))  # Never unlink another owner.
            info = self.path.lstat()
            self.socket_id = (info.st_dev, info.st_ino)
            os.chmod(self.path, 0o600)
            self.listener.listen(2)
        except BaseException:
            self.listener.close()
            if self.socket_id is not None:
                info = self.path.lstat()
                if (info.st_dev, info.st_ino) == self.socket_id:
                    self.path.unlink()
            raise RuntimeError("Cannot create private Bluetooth suspend socket")

    def recover(self, reason, *, reply=False):
        try:
            try:
                result = self.control.resume()
            except Exception as error:
                raise ClockRestoreError("Failed to restore the leased UART clock") from error
            if reply:
                send_reply(self.client, ok=True, prepared=False, **result)
            log("suspend-clock-restored", reason=reason, **result)
        finally:
            if self.client:
                self.client.close()
            self.client, self.deadline = None, None

    def poll(self, timeout=0.2):
        if self.client and time.monotonic() >= self.deadline:
            self.recover("lease-expired")
        readers = [self.listener] + ([self.client] if self.client else [])
        for ready in select.select(readers, [], [], timeout)[0]:
            if ready is self.listener:
                peer, _address = self.listener.accept()
                lease = False
            else:
                peer, lease = ready, True
            peer.settimeout(0.3)
            try:
                root_peer(peer)
                operation = receive_request(peer)
                if operation is None:
                    if lease:
                        self.recover("client-disconnected")
                    continue
                if operation == "status":
                    send_reply(peer, ok=True, prepared=self.client is not None,
                               **self.control.snapshot())
                elif operation == "prepare" and not self.client:
                    result = self.control.prepare()
                    self.client = peer
                    self.deadline = time.monotonic() + self.lease_seconds
                    lease = True
                    send_reply(peer, ok=True, prepared=True,
                               awake_lease_seconds=self.lease_seconds, **result)
                    log("suspend-clock-prepared", **result)
                elif operation == "resume" and lease:
                    self.recover("client-resume", reply=True)
                else:
                    raise RuntimeError("Unknown operation or a suspend lease is already pending")
            except ClockRestoreError:
                raise
            except Exception as error:
                if lease and self.client is peer:
                    self.recover("request-failed")
                else:
                    try:
                        send_reply(peer, ok=False, error=str(error))
                    except OSError:
                        pass
            finally:
                if self.client is not peer:
                    peer.close()

    def close(self):
        try:
            if self.client:
                self.recover("owner-stopping")
        finally:
            self.listener.close()
            try:
                info = self.path.lstat()
                if (info.st_dev, info.st_ino) == self.socket_id:
                    self.path.unlink()
            except FileNotFoundError:
                pass


def bootstrap(probe, patch, original_nvm, tags, address_bytes):
    global STOP
    restored_core()
    facts, rdev = probe.inspect()
    if os.geteuid() != 0 or facts["query_refusal_reasons"]:
        raise RuntimeError("UART unsafe: " + "; ".join(facts["query_refusal_reasons"] or ["root required"]))
    nvm = bytearray(original_nvm)
    nvm[tags[17][0] + 1] = 0  # 115200, confirmed in original qca_baudrate enum.
    nvm[tags[27][0] + 1] &= 0xFE  # OEM UpdateNewNvmFormat, not old Rome bit locations.
    nvm[tags[2][0]:tags[2][0] + 6] = address_bytes
    allowed = {tags[17][0] + 1, tags[27][0] + 1, *range(tags[2][0], tags[2][0] + 6)}
    if any(a != b and i not in allowed for i, (a, b) in enumerate(zip(original_nvm, nvm))):
        raise RuntimeError("Unexpected NVM change outside baud/SIBS/factory address")
    log("ram-nvm-prepared", original_sha256=NVM_SHA, ram_sha256=sha(nvm), baud=115200,
        sibs_enabled=False, original_blob_written=False, board_tags_preserved=True)
    fd = None
    old = None
    exclusive = clock = hci_discipline = False
    suspend_control = suspend_server = None
    cleanup = []

    def stop(_signum, _frame):
        global STOP
        STOP = True
        if not hci_discipline:
            raise RuntimeError("Initialization terminated")

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    try:
        log("before-open")
        fd = os.open(probe.UART, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW)
        if os.fstat(fd).st_rdev != rdev:
            raise RuntimeError("UART node changed")
        fcntl.ioctl(fd, probe.TIOCEXCL)
        exclusive = True
        if probe.owners(rdev, fd):
            raise RuntimeError("Another UART owner appeared")
        line = array.array("i", [0])
        fcntl.ioctl(fd, probe.TIOCGETD, line, True)
        if line[0] != 0:
            raise RuntimeError("UART is not N_TTY")
        old = termios.tcgetattr(fd)
        raw = list(old)
        raw[6] = list(old[6])
        raw[0] = raw[1] = raw[3] = 0
        raw[2] = (raw[2] & ~(termios.CSIZE | termios.PARENB | termios.CSTOPB | termios.HUPCL)) | termios.CS8 | termios.CLOCAL | termios.CREAD | termios.CRTSCTS
        raw[4] = raw[5] = termios.B115200
        raw[6][termios.VMIN] = raw[6][termios.VTIME] = 0
        termios.tcsetattr(fd, termios.TCSANOW, raw)
        log("before-clock-get")
        fcntl.ioctl(fd, probe.TIOCPMGET)
        clock = True
        log("clock-get-complete")
        transport = Transport(fd, probe)
        before = transport.version()
        log("version-before", **before)
        if before["patch_version"] != "0xd2b":
            raise RuntimeError("Controller is not at the measured fresh ROM baseline; a controlled BT-only power cycle is required")
        transport.download(patch, True)
        time.sleep(0.01)  # Official QCA setup settling interval before NVM.
        transport.download(bytes(nvm), False)
        deadline = time.monotonic() + 5
        log("before-bt-hci-reset", affects="Bluetooth controller only", wifi_reset=False)
        transport.send(0x0C03, b"", deadline)
        transport.complete(0x0C03, deadline)
        log("bt-hci-reset-complete")
        after = transport.version()
        log("version-after", **after)
        if after["patch_version"] != "0x5def":
            raise RuntimeError("Firmware patch version did not match the verified TLV")
        if transport.rx:
            raise RuntimeError("Unconsumed UART bytes before H4 handoff")
        log("before-h4-handoff")
        fcntl.ioctl(fd, TIOCSETD, array.array("i", [N_HCI]))
        hci_discipline = True
        fcntl.ioctl(fd, HCIUARTSETPROTO, 0)  # HCI_UART_H4; ioctl takes protocol value.
        index = fcntl.ioctl(fd, HCIUARTGETDEVICE, 0)
        expected = Path("/sys/class/bluetooth") / ("hci" + str(index))
        deadline = time.monotonic() + 3
        while not expected.is_dir() and time.monotonic() < deadline and not STOP:
            time.sleep(0.05)
        if STOP or not expected.is_dir():
            raise RuntimeError("No HCI controller appeared after handoff")
        suspend_control = SuspendClock(fd, probe.TIOCPMGET, probe.TIOCPMPUT,
                                       expected.name,
                                       lambda: (probe.SYS / "device/power/runtime_status").read_text().strip())
        suspend_server = SuspendServer(suspend_control)
        log("hci-ready", controller=expected.name, owner_pid=os.getpid(), uart_fd_held=True,
            uart_clock_vote_held=True, baud=115200, sibs_enabled=False,
            suspend_control_socket=str(SUSPEND_SOCKET),
            gross_serial_bytes_per_second=11520, power_scan_pairing_tested=False)
        while not STOP:
            # Do not read from N_HCI: the kernel exclusively owns H4 RX now.
            suspend_server.poll()
    finally:
        if suspend_server is not None:
            try:
                suspend_server.close()
            except (OSError, RuntimeError) as error:
                cleanup.append("suspend-clock-restore: " + str(error))
        if fd is not None:
            if hci_discipline:
                try:
                    fcntl.ioctl(fd, TIOCSETD, array.array("i", [0]))
                except OSError as error:
                    cleanup.append("detach-H4: " + str(error))
            vote_held = suspend_control.held if suspend_control is not None else clock
            if vote_held:
                try:
                    fcntl.ioctl(fd, probe.TIOCPMPUT)
                except OSError as error:
                    cleanup.append("clock-put: " + str(error))
            if old is not None:
                try:
                    termios.tcsetattr(fd, termios.TCSANOW, old)
                except OSError as error:
                    cleanup.append("restore-termios: " + str(error))
            if exclusive:
                try:
                    fcntl.ioctl(fd, probe.TIOCNXCL)
                except OSError as error:
                    cleanup.append("release-exclusive: " + str(error))
            try:
                os.close(fd)
            except OSError as error:
                cleanup.append("close: " + str(error))
            log("closed", cleanup_errors=cleanup)
        if cleanup:
            raise RuntimeError("Cleanup incomplete: " + "; ".join(cleanup))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bootstrap", action="store_true", help="initialize volatile firmware, then hold UART/H4 until stopped")
    parser.add_argument("--patch", type=Path, default=Path("/mnt/vendor-reference/firmware/htbtfw20.tlv"))
    parser.add_argument("--nvm", type=Path, default=Path("/mnt/vendor-reference/firmware/htnv20.bin"))
    parser.add_argument("--address-file", type=Path, help="verified original EFS colon-separated bt_addr file")
    args = parser.parse_args()
    try:
        probe = load_probe()
        patch, nvm, tags = firmware(args.patch, args.nvm)
        log("firmware-verified", patch_sha256=PATCH_SHA, nvm_sha256=NVM_SHA,
            product="0x10", rom="0x200", patch_version="0x5def", nvm_tag_count=len(tags))
        if not args.bootstrap:
            facts, _ = probe.inspect()
            log("inspect", facts=facts, writes_performed=False)
            return 0
        if not args.address_file:
            raise RuntimeError("A verified original EFS bt_addr file is required")
        address_bytes, digest = address(args.address_file)
        log("factory-address-validated", source=str(args.address_file), file_sha256=digest,
            address_printed=False)
        bootstrap(probe, patch, nvm, tags, address_bytes)
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        log("error", error=str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
