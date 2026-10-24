#!/usr/bin/env python3
"""Root lifecycle controller for the panel broker and GNOME Power.Screen bridge."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import pwd
import signal
import socket
import stat
import subprocess
import sys
import time

from backlight_rpc import SOCKET, rpc, secure_directory

BASE = Path(__file__).resolve().parent
RUN = SOCKET.parent
BROKER = BASE / "backlight_rpc.py"
BRIDGE = BASE / "gnome_backlight_bridge.py"
GNOME = Path("/run/user/1000/tabs7linux-gnome-preview")
BROKER_RECORD = RUN / "session.json"
BRIDGE_RECORD = GNOME / "backlight-bridge.json"
NAME = "org.gnome.SettingsDaemon.Power"
USER_ENV = dict(HOME="/home/linux", USER="linux", LOGNAME="linux",
                PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                XDG_RUNTIME_DIR="/run/user/1000", LANG="zh_CN.UTF-8", LC_ALL="zh_CN.UTF-8",
                TZ="Asia/Shanghai")


def identity(pid):
    try:
        path = Path("/proc") / str(pid)
        fields = (path / "stat").read_text().rsplit(") ", 1)[1].split()
        return dict(starttime=int(fields[19]), pgrp=int(fields[2]), sid=int(fields[3]),
                    state=fields[0], uid=path.stat().st_uid)
    except (OSError, ValueError, IndexError):
        return None


def load(path, uid):
    descriptor = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != uid or info.st_mode & 0o022:
            raise RuntimeError(f"untrusted service record: {path}")
        data = os.read(descriptor, 16385)
        if len(data) > 16384:
            raise RuntimeError("service record exceeds limit")
        return json.loads(data)
    finally:
        os.close(descriptor)


def record(path, uid, script):
    try:
        data = load(path, uid)
    except FileNotFoundError:
        return None
    pid, born = data["pid"], data["starttime"]
    if type(pid) is not int or pid <= 1 or type(born) is not int:
        raise RuntimeError("invalid helper process identity")
    info = identity(pid)
    if not info or info["state"] == "Z":
        return None
    if (info["uid"] != uid or info["starttime"] != born or info["sid"] != pid or
        info["pgrp"] != pid or data.get("sid") != pid or data.get("profile") != "persistent"):
        raise RuntimeError("helper PID/starttime/SID identity changed; preserving it")
    arguments = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
    after = identity(pid)
    if not after or after["state"] == "Z":
        return None
    if any(after[key] != info[key] for key in ("uid", "starttime", "pgrp", "sid")):
        raise RuntimeError("helper changed while checking identity")
    if os.fsencode(str(script)) not in arguments:
        raise RuntimeError("helper executable changed; preserving it")
    return data


def socket_info():
    info = SOCKET.lstat()
    if not stat.S_ISSOCK(info.st_mode) or info.st_uid != 0 or info.st_gid != 1000 or stat.S_IMODE(info.st_mode) != 0o660:
        raise RuntimeError("backlight socket owner/type/mode is invalid")
    return [info.st_dev, info.st_ino]


def broker_ready():
    data = record(BROKER_RECORD, 0, BROKER)
    if not data:
        return None
    before = socket_info()
    if before != data.get("socket_inode"):
        raise RuntimeError("backlight socket inode changed")
    state = rpc({"op": "get"}, expected_pid=data["pid"])
    if socket_info() != before or record(BROKER_RECORD, 0, BROKER) != data:
        raise RuntimeError("backlight broker changed while checking readiness")
    return dict(process=data, brightness=state)


def drop_user():
    account = pwd.getpwnam("linux")
    if account.pw_uid != 1000:
        raise RuntimeError("linux must be UID1000")
    os.initgroups(account.pw_name, account.pw_gid)
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)


def bus_owner(address):
    code = """import json,sys
from gi.repository import Gio,GLib
c=Gio.DBusConnection.new_for_address_sync(sys.argv[1],Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT|Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION,None,None)
def call(method,argument,kind):
 return c.call_sync('org.freedesktop.DBus','/org/freedesktop/DBus','org.freedesktop.DBus',method,GLib.Variant('(s)',(argument,)),GLib.VariantType.new(kind),Gio.DBusCallFlags.NONE,1000,None).unpack()[0]
name=call('GetNameOwner',sys.argv[2],'(s)')
print(json.dumps(dict(name=name,pid=call('GetConnectionUnixProcessID',name,'(u)'))))
"""
    result = subprocess.run(["/usr/bin/python3", "-c", code, address, NAME],
        env=USER_ENV, preexec_fn=drop_user, cwd="/home/linux", timeout=4,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if result.returncode:
        return None
    return json.loads(result.stdout)


def gnome_session():
    if GNOME.is_symlink() or GNOME.stat().st_uid != 1000 or GNOME.stat().st_mode & 0o022:
        raise RuntimeError("untrusted GNOME runtime")
    data = load(GNOME / "session.json", 1000)
    info = identity(data["pid"])
    if (data.get("profile") != "persistent" or not info or info["uid"] != 1000 or
        info["state"] == "Z" or info["starttime"] != data["starttime"] or
        info["sid"] != data["pid"] or info["pgrp"] != data["pid"]):
        raise RuntimeError("persistent GNOME session is not live")
    address = GNOME / "bus-address"
    details = address.lstat()
    if not stat.S_ISREG(details.st_mode) or details.st_uid != 1000 or details.st_mode & 0o022:
        raise RuntimeError("untrusted GNOME bus address")
    return data, address.read_text().strip()


def bridge_ready():
    data = record(BRIDGE_RECORD, 1000, BRIDGE)
    if not data:
        return None
    gnome, address = gnome_session()
    if (data.get("gnome_pid") != gnome["pid"] or
        data.get("gnome_starttime") != gnome["starttime"]):
        raise RuntimeError("brightness bridge belongs to another GNOME session")
    owner = bus_owner(address)
    if not owner or owner["pid"] != data["pid"] or owner["name"] != data.get("bus_unique_name"):
        raise RuntimeError("GNOME Power name is not owned by the recorded bridge")
    return data


def stop_owned(path, uid, script):
    data = record(path, uid, script)
    if not data:
        path.unlink(missing_ok=True)
        return
    def alive():
        current = record(path, uid, script)
        if current is not None and current != data:
            raise RuntimeError("helper record was replaced while stopping; preserving it")
        info = identity(data["pid"])
        if not info or info["state"] == "Z":
            return False
        if (info["uid"] != uid or info["starttime"] != data["starttime"] or
            info["sid"] != data["pid"] or info["pgrp"] != data["pid"]):
            raise RuntimeError("helper identity changed while stopping; preserving it")
        return True
    for sig, timeout in ((signal.SIGTERM, 3), (signal.SIGKILL, 1)):
        if not alive():
            break
        os.killpg(data["pid"], sig)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and alive():
            time.sleep(0.05)
    if alive():
        raise RuntimeError("owned helper did not stop")
    path.unlink(missing_ok=True)


def log_file(path, uid):
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        descriptor = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CLOEXEC | os.O_NOFOLLOW)
        details = os.fstat(descriptor)
        if details.st_uid != uid or not stat.S_ISREG(details.st_mode) or details.st_nlink != 1:
            os.close(descriptor)
            raise RuntimeError("untrusted helper log")
    else:
        os.fchown(descriptor, uid, uid)
    return os.fdopen(descriptor, "a")


def wait_started(child, uid, script, ready, label):
    """Clean up this exact child on any failure, even before its record exists."""
    started = identity(child.pid)
    if not started:
        child.wait(timeout=1)
        raise RuntimeError(f"{label} exited before readiness")
    def alive():
        current = identity(child.pid)
        if not current or current["state"] == "Z":
            return False
        if (current["uid"] != uid or current["sid"] != child.pid or current["pgrp"] != child.pid or
            current["starttime"] != started["starttime"]):
            raise RuntimeError("new helper identity changed; preserving it")
        arguments = Path(f"/proc/{child.pid}/cmdline").read_bytes().split(b"\0")
        after = identity(child.pid)
        if not after or after["state"] == "Z":
            return False
        if any(after[key] != current[key] for key in ("uid", "starttime", "pgrp", "sid")):
            raise RuntimeError("new helper changed while checking identity")
        if os.fsencode(str(script)) not in arguments:
            # /proc clears cmdline during exit, sometimes just before state=Z.
            if child.poll() is not None:
                return False
            if not any(arguments):
                try:
                    child.wait(timeout=0.05)
                    return False
                except subprocess.TimeoutExpired:
                    pass
            raise RuntimeError("new helper executable changed; preserving it")
        return True
    try:
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            if child.poll() is not None:
                raise RuntimeError(f"{label} exited; inspect its log")
            if ready():
                return
            time.sleep(0.05)
        raise RuntimeError(f"{label} readiness timed out")
    except BaseException:
        for sig, timeout in ((signal.SIGTERM, 3), (signal.SIGKILL, 1)):
            if not alive():
                break
            os.killpg(child.pid, sig)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline and alive():
                time.sleep(0.05)
        if alive():
            raise RuntimeError("new owned helper did not stop")
        child.wait(timeout=1)
        raise


def start_broker(initial):
    if broker_ready():
        return
    # Refuse an unrecorded live socket before a new broker can touch hardware.
    if SOCKET.exists() or SOCKET.is_symlink():
        socket_info()
        probe = socket.socket(socket.AF_UNIX)
        try:
            probe.settimeout(0.2)
            probe.connect(str(SOCKET))
        except (FileNotFoundError, ConnectionRefusedError):
            pass
        else:
            raise RuntimeError("an unrecorded backlight socket is active")
        finally:
            probe.close()
    with log_file(RUN / "broker.log", 0) as log:
        child = subprocess.Popen(["/usr/bin/python3", str(BROKER), "serve", "--initial-level", str(initial)],
            env={"PATH": USER_ENV["PATH"], "LANG": "C.UTF-8", "TZ": "Asia/Shanghai"},
            start_new_session=True, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    wait_started(child, 0, BROKER, broker_ready, "backlight broker")


def start_bridge():
    if not broker_ready():
        raise RuntimeError("start the root backlight broker first")
    if bridge_ready():
        return
    _, address = gnome_session()
    if bus_owner(address):
        raise RuntimeError("another GNOME Power service already owns the private bus name")
    BRIDGE_RECORD.unlink(missing_ok=True)
    with log_file(GNOME / "backlight-bridge.log", 1000) as log:
        child = subprocess.Popen(["/usr/bin/python3", str(BRIDGE)], env=USER_ENV,
            preexec_fn=drop_user, cwd="/home/linux", start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
    wait_started(child, 1000, BRIDGE, bridge_ready, "GNOME brightness bridge")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("start", "start-bridge", "start-all", "stop-bridge", "stop", "status"))
    parser.add_argument("--initial-level", type=int, choices=range(4, 256), default=255)
    args = parser.parse_args()
    if os.getuid() != 0:
        parser.error("run inside native Ubuntu as root")
    secure_directory(RUN, 0, 0o755)
    descriptor = os.open(RUN / "controller.lock", os.O_CREAT | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.command in ("start", "start-all"):
            start_broker(args.initial_level)
        if args.command in ("start-bridge", "start-all"):
            start_bridge()
        if args.command == "stop-bridge":
            stop_owned(BRIDGE_RECORD, 1000, BRIDGE)
        if args.command == "stop":
            if record(BRIDGE_RECORD, 1000, BRIDGE):
                raise RuntimeError("stop the GNOME brightness bridge before its broker")
            owned = record(BROKER_RECORD, 0, BROKER)
            expected_socket = owned.get("socket_inode") if owned else None
            stop_owned(BROKER_RECORD, 0, BROKER)
            if SOCKET.exists():
                if socket_info() != expected_socket:
                    raise RuntimeError("broker socket changed; preserving its inode")
                probe = socket.socket(socket.AF_UNIX)
                try:
                    probe.settimeout(0.2)
                    probe.connect(str(SOCKET))
                except (FileNotFoundError, ConnectionRefusedError):
                    SOCKET.unlink(missing_ok=True)
                else:
                    raise RuntimeError("broker socket is still active; preserving it")
                finally:
                    probe.close()
        if args.command == "status":
            broker = broker_ready()
            bridge = bridge_ready()
            print(json.dumps(dict(broker=broker, bridge=bridge)))
            if not broker or not bridge:
                raise RuntimeError("brightness subsystem is not fully ready")
        else:
            print(f"Backlight {args.command}: complete", flush=True)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.SubprocessError) as error:
        print(f"Backlight: {error}", file=sys.stderr)
        sys.exit(1)
