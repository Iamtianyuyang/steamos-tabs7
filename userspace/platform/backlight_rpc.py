#!/usr/bin/python3
"""One-panel root broker. No display modes, key handling, or fake brightness."""
import argparse
import ctypes
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import stat
import struct
import tempfile
import time

PANEL = "panel0-backlight"
MODEL = "ss_dsi_panel_NT36523_PPA957DB1_WQXGA"
MINIMUM, MAXIMUM, ADVERTISED = 4, 255, 462
SOCKET = Path("/run/tabs7linux-backlight/control.sock")
STATE = Path("/var/lib/tabs7linux/backlight.json")


def integer(value, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"expected integer in {low}..{high}")
    return value


class Backlight:
    def __init__(self, sysfs=Path("/sys"), proc=Path("/proc"), *, fixture=False):
        self.fixture = fixture
        self.owner = os.getuid() if fixture else 0
        self.root = (sysfs / "class/backlight" / PANEL).resolve(strict=True)
        devices = (sysfs / "devices").resolve(strict=True)
        if devices not in self.root.parents:
            raise RuntimeError("backlight is outside the validated sysfs devices tree")
        models = [word.split("=", 1)[1].split(":", 1)[0]
                  for word in (proc / "cmdline").read_text().split()
                  if word.startswith("msm_drm.dsi_display0=")]
        if models != [MODEL]:
            raise RuntimeError("the expected NT36523 panel is absent from the kernel command line")
        self.validate()

    def read(self, name):
        descriptor = os.open(self.root / name, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner:
                raise RuntimeError(f"untrusted backlight attribute: {name}")
            return os.read(descriptor, 4096).decode().strip()
        finally:
            os.close(descriptor)

    def validate(self):
        if self.read("max_brightness") != str(ADVERTISED) or self.read("type") != "raw":
            raise RuntimeError("backlight range/type differs from the validated hardware")
        uevent = (self.root / "device/uevent").read_text().splitlines()
        if "DRIVER=msm_drm" not in uevent:
            raise RuntimeError("unexpected backlight driver")

    def level(self):
        return int(self.read("brightness"))

    def write(self, level):
        integer(level, MINIMUM, MAXIMUM)
        self.validate()
        descriptor = os.open(self.root / "brightness", os.O_WRONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_uid != self.owner:
                raise RuntimeError("untrusted brightness attribute")
            if self.fixture:
                os.ftruncate(descriptor, 0)
            data = f"{level}\n".encode()
            if os.write(descriptor, data) != len(data):
                raise RuntimeError("short backlight write")
        finally:
            os.close(descriptor)
        current = self.level()
        if current != level:
            raise RuntimeError(f"hardware request readback mismatch: wanted {level}, got {current}")
        return current


def secure_directory(path, owner, mode):
    if path.is_symlink():
        raise RuntimeError(f"directory is a symlink: {path}")
    path.mkdir(parents=True, mode=mode, exist_ok=True)
    info = path.stat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != owner or info.st_mode & 0o022:
        raise RuntimeError(f"directory is not owned/protected: {path}")


def write_record(path, data):
    """Publish a complete private lifecycle record, never a partial JSON file."""
    descriptor, temporary = tempfile.mkstemp(prefix=".session-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            descriptor = -1
            os.fchmod(stream.fileno(), 0o600)
            stream.write(json.dumps(data) + "\n")
        os.replace(temporary, path)
        return path.stat()
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if os.path.exists(temporary):
            os.unlink(temporary)


class Broker:
    def __init__(self, hardware, state, initial=None):
        self.hardware = hardware
        self.state = state
        self.restore_at = []
        self.panel_on = True
        self.hold_until = 0
        secure_directory(state.parent, hardware.owner, 0o700)
        if state.exists() or state.is_symlink():
            descriptor = os.open(state, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
            try:
                info = os.fstat(descriptor)
                if info.st_uid != hardware.owner or not stat.S_ISREG(info.st_mode) or info.st_mode & 0o077:
                    raise RuntimeError("backlight state is not a private owned file")
                saved = json.loads(os.read(descriptor, 4096))
            finally:
                os.close(descriptor)
            if saved.get("panel") != PANEL or saved.get("model") != MODEL:
                raise RuntimeError("saved brightness belongs to different hardware")
            self.nominal = integer(saved.get("level"), MINIMUM, MAXIMUM)
            hardware.write(self.nominal)
        else:
            self.nominal = integer(initial if initial is not None else hardware.level(), MINIMUM, MAXIMUM)
            if initial is not None:
                hardware.write(self.nominal)
            self.save()

    def save(self):
        descriptor, temporary = tempfile.mkstemp(prefix=".backlight-", dir=self.state.parent)
        try:
            os.fchmod(descriptor, 0o600)
            data = json.dumps(dict(panel=PANEL, model=MODEL, level=self.nominal)) + "\n"
            os.write(descriptor, data.encode())
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.state)
            directory = os.open(self.state.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if os.path.exists(temporary):
                os.unlink(temporary)

    def snapshot(self):
        return dict(level=self.hardware.level(), nominal=self.nominal,
                    minimum=MINIMUM, maximum=MAXIMUM, panel=PANEL,
                    panel_on=self.panel_on, restore_held=time.monotonic() < self.hold_until,
                    actual_brightness_reliable=False)

    def request(self, request):
        if not isinstance(request, dict):
            raise ValueError("request must be an object")
        op = request.get("op")
        if op == "get" and set(request) == {"op"}:
            return self.snapshot()
        if op == "set" and set(request) == {"op", "level"}:
            level = integer(request["level"], MINIMUM, MAXIMUM)
            if self.panel_on and time.monotonic() >= self.hold_until:
                self.hardware.write(level)
            self.nominal = level
            self.save()
            return self.snapshot()
        if op == "reapply" and set(request) == {"op"}:
            now = time.monotonic()
            if self.panel_on and now >= self.hold_until:
                self.restore_at = [now + delay for delay in (0.1, 0.4, 1.0)]
            return dict(scheduled=bool(self.restore_at), nominal=self.nominal)
        if op == "hold" and set(request) == {"op"}:
            self.hold_until = time.monotonic() + 5
            self.restore_at = []
            return dict(held=True)
        if op == "power" and set(request) == {"op", "on"} and type(request["on"]) is bool:
            self.panel_on = request["on"]
            self.hold_until = 0
            now = time.monotonic()
            self.restore_at = [now + delay for delay in (0.1, 0.4, 1.0)] if self.panel_on else []
            return dict(panel_on=self.panel_on, scheduled=bool(self.restore_at), nominal=self.nominal)
        raise ValueError("unsupported bounded backlight operation")

    def tick(self):
        now = time.monotonic()
        if not self.panel_on or now < self.hold_until:
            return
        forced = any(deadline <= now for deadline in self.restore_at)
        self.restore_at = [deadline for deadline in self.restore_at if deadline > now]
        current = self.hardware.level()
        # Zero may be a powered-off panel. Avoid turning it on from a poll.
        if forced or (current != 0 and current != self.nominal):
            self.hardware.write(self.nominal)


def rpc(request, path=SOCKET, *, expected_uid=0, expected_pid=None):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(2)
    try:
        connection.connect(str(path))
        pid, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        if uid != expected_uid or (expected_pid is not None and pid != expected_pid):
            raise RuntimeError("the backlight RPC peer is not the expected root broker")
        connection.sendall(json.dumps(request).encode() + b"\n")
        data = b""
        while b"\n" not in data:
            chunk = connection.recv(2048 - len(data))
            if not chunk or len(data) + len(chunk) >= 2048:
                raise RuntimeError("invalid backlight RPC response")
            data += chunk
        result = json.loads(data.split(b"\n", 1)[0])
        if not result.get("ok"):
            raise RuntimeError(result.get("error", "backlight request failed"))
        return result["result"]
    finally:
        connection.close()


def serve(args):
    fixture = args.fixture_root is not None
    if fixture and os.getuid() == 0:
        raise RuntimeError("fixture mode is only for an unprivileged host test")
    if not fixture and os.getuid() != 0:
        raise RuntimeError("the native hardware broker must run as root")
    owner = os.getuid() if fixture else 0
    allowed_uid = os.getuid() if fixture else 1000
    runtime = args.socket.parent
    secure_directory(runtime, owner, 0o755)
    lock = os.open(runtime / "lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if os.getsid(0) != os.getpid():
        os.setsid()
    ctypes.CDLL(None).prctl(15, b"tabs7-bl-root", 0, 0, 0)
    root = args.fixture_root
    hardware = Backlight(root / "sys" if fixture else Path("/sys"),
                         root / "proc" if fixture else Path("/proc"), fixture=fixture)
    if args.socket.exists() or args.socket.is_symlink():
        info = args.socket.lstat()
        if not stat.S_ISSOCK(info.st_mode) or info.st_uid != owner:
            raise RuntimeError("refusing to replace a foreign runtime path")
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.settimeout(0.2)
            probe.connect(str(args.socket))
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        else:
            raise RuntimeError("refusing to replace an active backlight socket")
        finally:
            probe.close()
        args.socket.unlink()
    broker = Broker(hardware, args.state, args.initial_level)
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(str(args.socket))
    if not fixture:
        os.chown(args.socket, 0, 1000)
    os.chmod(args.socket, 0o600 if fixture else 0o660)
    socket_identity = args.socket.stat()
    server.listen(8)
    server.settimeout(0.2)
    stopping = False
    def stop(_signum, _frame):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    fields = Path("/proc/self/stat").read_text().rsplit(") ", 1)[1].split()
    record = runtime / "session.json"
    record_identity = write_record(record, dict(pid=os.getpid(), starttime=int(fields[19]),
        sid=int(fields[3]), profile="persistent", panel=PANEL,
        socket_inode=[socket_identity.st_dev, socket_identity.st_ino]))
    print(f"Backlight broker ready: {PANEL}, {MINIMUM}..{MAXIMUM}, nominal={broker.nominal}", flush=True)
    tick_at = 0
    last_error = None
    try:
        while not stopping:
            try:
                connection, _ = server.accept()
            except socket.timeout:
                connection = None
            if connection is not None:
                with connection:
                    connection.settimeout(0.2)
                    try:
                        _, uid, _ = struct.unpack("3i", connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                        data = b""
                        while b"\n" not in data and len(data) < 2048:
                            chunk = connection.recv(2048 - len(data))
                            if not chunk:
                                raise ValueError("incomplete request")
                            data += chunk
                        if b"\n" not in data:
                            raise ValueError("request exceeds limit")
                        request = json.loads(data.split(b"\n", 1)[0])
                        if uid != allowed_uid and not (uid == owner and request == {"op": "get"}):
                            raise PermissionError("only the tablet user may change brightness")
                        result = broker.request(request)
                        response = dict(ok=True, result=result)
                    except (OSError, ValueError, RuntimeError) as error:
                        response = dict(ok=False, error=str(error))
                    try:
                        connection.sendall(json.dumps(response).encode() + b"\n")
                    except OSError:
                        pass
            now = time.monotonic()
            if now >= tick_at:
                tick_at = now + 0.2
                try:
                    broker.tick()
                    last_error = None
                except (OSError, ValueError, RuntimeError) as error:
                    if str(error) != last_error:
                        print(f"Backlight restore deferred: {error}", flush=True)
                        last_error = str(error)
    finally:
        server.close()
        if args.socket.exists():
            current = args.socket.lstat()
            if (current.st_dev, current.st_ino) == (socket_identity.st_dev, socket_identity.st_ino):
                args.socket.unlink()
        try:
            current = record.lstat()
            if (current.st_dev, current.st_ino) == (record_identity.st_dev, record_identity.st_ino):
                record.unlink()
        except FileNotFoundError:
            pass
        os.close(lock)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("serve", "get", "reapply", "set"))
    parser.add_argument("level", nargs="?", type=int)
    parser.add_argument("--socket", type=Path, default=SOCKET)
    parser.add_argument("--state", type=Path, default=STATE)
    parser.add_argument("--fixture-root", type=Path)
    parser.add_argument("--initial-level", type=int,
                        help="root-selected, previously hardware-verified first-run normal level")
    args = parser.parse_args()
    if args.command == "serve":
        serve(args)
    else:
        request = dict(op=args.command)
        if args.command == "set":
            request["level"] = integer(args.level, MINIMUM, MAXIMUM)
        print(json.dumps(rpc(request, args.socket)))


if __name__ == "__main__":
    main()
