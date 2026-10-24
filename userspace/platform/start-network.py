#!/usr/bin/python3
"""Start native QRTR/Wi-Fi and NetworkManager once per boot, outside the UI."""
import fcntl
import errno
import json
import os
from pathlib import Path
import select
import runpy
import subprocess
import threading
import time

prepare = runpy.run_path(str(Path(__file__).with_name('prepare-wifi.py')))['prepare']

RUN = Path('/run/network')


def start_time(pid):
    text = Path('/proc/' + str(pid) + '/stat').read_text()
    fields = text.rsplit(') ', 1)[1].split()
    if fields[0] == 'Z':
        raise ValueError('Service is a zombie')
    return fields[19]


def matches_command(pid, command):
    proc = Path('/proc/' + str(pid))
    if proc.stat().st_uid != 0:
        return False
    argv = (proc / 'cmdline').read_bytes().split(b'\0')
    live_command = [arg.decode() for arg in argv if arg]
    if command[:1] == ['/usr/sbin/chroot'] and len(command) >= 3:
        # chroot execs the Bionic linker; its argv and root then belong to the
        # isolated runtime, rather than the short-lived Ubuntu chroot binary.
        live_root, expected_root = (proc / 'root').stat(), Path(command[1]).stat()
        return (live_root.st_dev, live_root.st_ino) == (expected_root.st_dev, expected_root.st_ino) and live_command == command[2:]
    return live_command == command


def service(name, command, *, environment=None, adopt=True):
    record = RUN / (name + '.json')
    if record.exists():
        try:
            previous = json.loads(record.read_text())
            if start_time(previous['pid']) == previous['start']:
                if not matches_command(previous['pid'], command):
                    raise RuntimeError(name + ' is running with a different command; restart the recorded service first')
                return previous['pid']
        except (OSError, KeyError, ValueError):
            pass
    # Adopt only a live root-owned process of the exact executable we need.
    for proc in Path('/proc').iterdir() if adopt else ():
        try:
            if not proc.name.isdigit() or proc.stat().st_uid != 0:
                continue
            if matches_command(int(proc.name), command):
                pid = int(proc.name)
                record.write_text(json.dumps({'pid': pid, 'start': start_time(pid)}))
                return pid
        except (OSError, UnicodeDecodeError):
            continue
    with (RUN / (name + '.log')).open('ab', buffering=0) as log:
        child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                 env=environment, start_new_session=True)
    time.sleep(0.3)
    if child.poll() is not None:
        raise RuntimeError(name + ' exited; inspect its log')
    record.write_text(json.dumps({'pid': child.pid, 'start': start_time(child.pid)}))
    return child.pid


def start_cnss():
    # The live DT enables cnss-daemon-support: firmware expects the matching
    # second WLFW client in addition to the Linux CNSS driver. Its stock Bionic
    # libraries live in an isolated verified runtime, never the Ubuntu search
    # path or Android's private data tree.
    helper = Path(__file__).with_name('prepare-cnss.py')
    runtime = runpy.run_path(str(helper))['prepare']()
    environment = os.environ.copy()
    environment['LD_LIBRARY_PATH'] = (
        '/vendor/lib64:/system/lib64:/apex/com.android.runtime/lib64/bionic:'
        '/apex/com.android.vndk.v33/lib64')
    return service('cnss-daemon', [
        '/usr/sbin/chroot', str(runtime), '/system/bin/linker64',
        '/vendor/bin/cnss-daemon', '-n', '-dd'],
        environment=environment, adopt=False)


def wait_qrtr_ready(pid):
    # qrtr-lookup sends NEW_LOOKUP; qrtr-ns replies with cached servers followed
    # by an all-zero NEW_SERVER, even when no remote services are present yet.
    # A zero exit therefore verifies the local name server, not WLAN readiness.
    expected_start = start_time(pid)
    deadline = time.monotonic() + 5
    last_error = 'lookup did not complete'
    with (RUN / 'qrtr-lookup.log').open('ab', buffering=0) as log:
        while True:
            try:
                if start_time(pid) != expected_start:
                    raise RuntimeError('QRTR name service process changed')
            except (OSError, ValueError) as exc:
                raise RuntimeError('QRTR name service exited before readiness') from exc
            try:
                subprocess.run(['/usr/bin/qrtr-lookup'], check=True, timeout=2,
                               stdout=log, stderr=log)
                return
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                last_error = str(exc)
            if time.monotonic() >= deadline:
                raise RuntimeError('QRTR name service is not ready: ' + last_error)
            time.sleep(0.1)


def enable_wifi_radio():
    with (RUN / 'radio.log').open('ab', buffering=0) as log:
        subprocess.run(['/usr/bin/nmcli', '--wait', '5', 'radio', 'wifi', 'on'],
                       check=True, stdout=log, stderr=log, timeout=8)


def capture_kernel(stop):
    try:
        fd = os.open('/dev/kmsg', os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
        os.lseek(fd, 0, os.SEEK_END)
        with (RUN / 'wifi-kernel.log').open('ab', buffering=0) as log:
            while not stop.is_set():
                if not select.select([fd], [], [], 0.3)[0]:
                    continue
                try:
                    line = os.read(fd, 65536)
                except BlockingIOError:
                    continue
                except OSError as exc:
                    if exc.errno == errno.EPIPE:
                        # printk resets this fd to the oldest available record.
                        log.write(b'# /dev/kmsg records lost; continuing capture\n')
                        continue
                    break
                text = line.lower()
                if any(token in text for token in (b'cnss', b'mhi', b'wlan', b'qmi')):
                    log.write(line)
        os.close(fd)
    except OSError as exc:
        (RUN / 'capture-error.txt').write_text(str(exc))


def write_status(state, **details):
    report = {'state': state, 'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(), **details}
    temp = RUN / 'wifi-status.new'
    temp.write_text(json.dumps(report, indent=2) + '\n')
    temp.replace(RUN / 'wifi-status.json')


def main():
    if os.getuid() != 0:
        raise SystemExit('Run as root inside native Ubuntu')
    if not Path('/etc/tabs7linux/native-wifi-enabled').is_file():
        print('Native Wi-Fi profile is not enabled.')
        return
    RUN.mkdir(mode=0o755, exist_ok=True)
    lock = (RUN / 'startup.lock').open('w')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    stop = threading.Event()
    thread = None
    services = {}
    try:
        if (RUN / 'wifi-attempted').exists() and not Path('/sys/class/net/wlan0').is_dir():
            print('Wi-Fi was already attempted this boot; inspect wifi-status.json.')
            return
        count = prepare()
        qrtr = service('qrtr-ns', ['/usr/bin/qrtr-ns', '-f'])
        wait_qrtr_ready(qrtr)
        # The matching APQ init writes /dev/ipa before WLAN initialization.
        # Until IPA attaches its SMMU, QDF rejects the translation mismatch
        # even after WLFW has reached FW_READY.
        ipa_prepare = runpy.run_path(str(Path(__file__).with_name('prepare-ipa.py')))['prepare']
        ipa = ipa_prepare(verify_only=Path('/sys/class/net/wlan0').is_dir())
        cnss = start_cnss()
        manager_executable = Path('/usr/sbin/NetworkManager')
        if Path('/etc/tabs7linux/networkmanager-private-enabled').is_file():
            manager_executable = runpy.run_path(str(
                Path(__file__).with_name('prepare-networkmanager.py')))['prepare']()
        manager = service('NetworkManager', [str(manager_executable), '--no-daemon',
                                             '--debug', '--log-level=INFO'])
        services = {'firmware_files': count, 'ipa_status': ipa['status'],
                    'qrtr_pid': qrtr, 'cnss_pid': cnss,
                    'network_manager_pid': manager,
                    'network_manager_executable': str(manager_executable)}
        if Path('/sys/class/net/wlan0').is_dir():
            enable_wifi_radio()
            write_status('ready', **services)
            return
        marker = RUN / 'wifi-attempted'
        try:
            fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        except FileExistsError:
            print('Wi-Fi was already attempted this boot; inspect wifi-status.json.')
            return
        node = Path('/dev/wlan')
        expected = Path('/sys/class/wlan/wlan/dev').read_text().strip().split(':')
        expected_rdev = os.makedev(*(int(n) for n in expected))
        if not node.is_char_device() or node.stat().st_rdev != expected_rdev:
            raise RuntimeError('WLAN control node does not match the live driver')
        thread = threading.Thread(target=capture_kernel, args=(stop,), daemon=True)
        thread.start()
        write_status('starting', **services)
        fd = os.open(node, os.O_RDWR | os.O_CLOEXEC)
        try:
            size = os.write(fd, b'ON\n')
        finally:
            os.close(fd)
        if size != 3:
            raise RuntimeError('WLAN enable command was incomplete')
        deadline = time.monotonic() + 10
        while not Path('/sys/class/net/wlan0').is_dir():
            if time.monotonic() >= deadline:
                raise RuntimeError('WLAN interface did not appear')
            time.sleep(0.1)
        enable_wifi_radio()
        write_status('ready', **services)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        write_status('failed', error=str(exc), **services)
        print('Native network startup failed:', exc, flush=True)
    finally:
        stop.set()
        if thread:
            thread.join(timeout=2)


if __name__ == '__main__':
    main()
