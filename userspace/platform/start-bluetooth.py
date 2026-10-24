#!/usr/bin/python3
"""Own the verified Tab S7 Bluetooth bootstrap and BlueZ lifecycle.

Usage: start-bluetooth.py {start,stop,status} [--config PATH]
The optional root-owned JSON config overrides asset paths only. This controller
uses the existing shared system D-Bus; the network startup owns that bus.
"""
import argparse
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import signal
import stat
import struct
import subprocess
import sys
import time

RUN = Path('/run/tabs7-bluetooth')
STATE = RUN / 'state.json'
BUS_SOCKET = Path('/run/dbus/system_bus_socket')
CONFIG = Path('/etc/tabs7linux/bluetooth/config.json')
RELEASE = '4.19.325-cip136-st20-perf-g4478511c4b37'
CONFIG_SHA = '02267534fad767e591c1c809523b8e589bb131c21d25c0731ade662a554d9117'
STEAMOS_IPC_CONFIG_SHA = '7b6104ec713df7192b62f3fccbbd955aa4d7c575985179906c72dcfbbb069916'
STEAMOS_IPC_MODULE_SHA = '60bf0936d04bc1efd1251aecb09b8e3e7b1a1390dcd8b80217ae450504c1d30a'
STEAMOS_SYSTEMD_CONFIG_SHA = 'c4ca8fa1621234598ad80bf71be6263c603a36f6d6484f91a6c425472a0bb808'
STEAMOS_SYSTEMD_MODULE_SHA = '42360eb2bf7ee3e7a3bfcd287bc970f69f998288d9fbaec79042ad67892df05b'
MODULE_DIR = '/usr/local/lib/tabs7linux/bluetooth/module/'
# Each reviewed kernel config changes module CRCs; select only the H4 module
# rebuilt and import-checked against that exact config. Never force one.
KERNEL_MODULES = {
    CONFIG_SHA: ('c70a7c1105789be7f3679ec149108312d94d016dd2ff5d298cb8e8e0e9a5aef8', MODULE_DIR + 'tabs7_hci_uart.ko'),
    STEAMOS_IPC_CONFIG_SHA: (STEAMOS_IPC_MODULE_SHA, MODULE_DIR + 'tabs7_hci_uart-steamos-ipc.ko'),
    STEAMOS_SYSTEMD_CONFIG_SHA: (STEAMOS_SYSTEMD_MODULE_SHA, MODULE_DIR + 'tabs7_hci_uart-steamos-systemd.ko'),
}
HASHES = {
    'loader': '8b417f6ce57109ada90663be14ba32c6d32287afa6fecb4a9f6eda1de7ae828b',
    'probe': '0a4922963fc616f2b9ecc00cfe520956f941381b8802db29007cda380ce30d4b',
    'module': 'c70a7c1105789be7f3679ec149108312d94d016dd2ff5d298cb8e8e0e9a5aef8',
    'patch': '40f6f13b397999e6e43dfbe0ea6016b9d84eab6e5090345fb443248004a3c3dd',
    'nvm': 'b3ee40fd0063061d872e8459c084c0343b8c1a42702dd7dfcfc2044dd73d6ee6',
}
DEFAULTS = {
    'loader': '/usr/local/lib/tabs7linux/bluetooth/bootstrap-qca6390-h4.py',
    'probe': '/usr/local/lib/tabs7linux/bluetooth/probe-ttyhs0-version.py',
    'module': '/usr/local/lib/tabs7linux/bluetooth/module/tabs7_hci_uart.ko',
    'patch': '/usr/local/lib/tabs7linux/bluetooth/firmware/htbtfw20.tlv',
    'nvm': '/usr/local/lib/tabs7linux/bluetooth/firmware/htnv20.bin',
    'address': '/etc/tabs7linux/bluetooth/bt_addr',
    'python': '/usr/bin/python3',
    'bluetoothd': '/usr/libexec/bluetooth/bluetoothd',
    'dbus_send': '/usr/bin/dbus-send',
    'dbus_daemon': '/usr/bin/dbus-daemon',
    'insmod': '/sbin/insmod',
    'rmmod': '/sbin/rmmod',
}
ENV = {'PATH': '/usr/sbin:/usr/bin:/sbin:/bin', 'LANG': 'C.UTF-8',
       'DBUS_SYSTEM_BUS_ADDRESS': 'unix:path=' + str(BUS_SOCKET)}
STOP = False


def digest(data):
    return hashlib.sha256(data).hexdigest()


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def trusted(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute():
        raise RuntimeError('Asset paths must be absolute: ' + str(path))
    resolved = path.resolve(strict=True)
    for item in (path, *path.parents, resolved, *resolved.parents):
        info = item.stat()
        if info.st_uid != 0 or info.st_mode & 0o022:
            raise RuntimeError('Path must be root-owned and not writable by others: ' + str(item))
    info = resolved.stat()
    if not (stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)):
        raise RuntimeError('Unexpected path type: ' + str(path))
    return resolved


def load_paths(config):
    paths = dict(DEFAULTS)
    source = Path(config) if config else CONFIG
    if source.exists() or config:
        values = json.loads(trusted(source).read_text())
        if not isinstance(values, dict) or set(values) - set(paths):
            raise RuntimeError('Bluetooth config must contain known asset path keys only')
        if any(not isinstance(value, str) for value in values.values()):
            raise RuntimeError('Bluetooth config values must be absolute path strings')
        paths.update(values)
    return paths


def prepare_runtime():
    RUN.mkdir(mode=0o700, parents=True, exist_ok=True)
    if RUN.is_symlink():
        raise RuntimeError('Bluetooth runtime directory must not be a symlink')
    trusted(RUN, directory=True)


def log_file(name):
    fd = os.open(RUN / (name + '.log'), os.O_WRONLY | os.O_APPEND | os.O_CREAT |
                 os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    if os.fstat(fd).st_uid != 0 or os.fstat(fd).st_mode & 0o077:
        os.close(fd)
        raise RuntimeError('Bluetooth log permissions are unsafe')
    os.lseek(fd, 0, os.SEEK_END)
    return os.fdopen(fd, 'ab', buffering=0)


def save(state):
    state['updated'] = time.time()
    temporary = RUN / ('.state-' + str(os.getpid()) + '-' + secrets.token_hex(6))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL |
                 os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(state, output, sort_keys=True, indent=2)
            output.write('\n')
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, STATE)
        directory = os.open(RUN, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def load_state():
    if not STATE.exists():
        return None
    if STATE.is_symlink() or STATE.stat().st_size > 65536:
        raise RuntimeError('Bluetooth ownership record is unsafe')
    trusted(STATE)
    state = json.loads(STATE.read_text())
    if state.get('version') != 1 or not isinstance(state.get('services'), dict):
        raise RuntimeError('Unknown Bluetooth ownership record; inspect ' + str(STATE))
    return state


def process(pid, *, require_root=True):
    proc = Path('/proc') / str(int(pid))
    fields = (proc / 'stat').read_text().rsplit(') ', 1)[1].split()
    if fields[0] == 'Z':
        return None
    uids = re.search(r'^Uid:\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$',
                     (proc / 'status').read_text(), re.M)
    if not uids or (require_root and any(int(value) != 0 for value in uids.groups())):
        raise RuntimeError('Bluetooth process is not entirely root-owned')
    command = [part.decode() for part in (proc / 'cmdline').read_bytes().split(b'\0') if part]
    return {'pid': int(pid), 'start': fields[19], 'sid': int(fields[3]),
            'uid': int(uids.group(2)), 'command': command}


def live(record):
    if not record or record.get('pid') is None:
        return False
    try:
        current = process(record['pid'])
    except (FileNotFoundError, ProcessLookupError):
        return False
    if current is None or current['start'] != record['start']:
        return False
    if any(current[key] != record[key] for key in ('sid', 'uid', 'command')):
        raise RuntimeError('Live process differs from its Bluetooth ownership record')
    return True


def pending_alive(record):
    """Check a recorded process birth without accepting its transitional argv."""
    if not record:
        return False
    try:
        current = process(record['pid'])
    except (FileNotFoundError, ProcessLookupError):
        return False
    if current is None:
        return False
    if not all(key in record for key in ('start', 'sid', 'uid')):
        raise RuntimeError('Live pending Bluetooth child has no verified birth identity')
    if current['start'] != record['start']:
        return False
    if any(current[key] != record[key] for key in ('sid', 'uid')):
        raise RuntimeError('Pending Bluetooth child changed session or UID')
    return True


def await_identity(child, command, sid, pending, persist, *, timeout=5):
    """Commit only two identical post-exec samples of this unreaped child."""
    deadline = time.monotonic() + timeout
    previous = None
    while True:
        if child.poll() is not None:
            raise RuntimeError('Bluetooth child exited before its executable identity was ready')
        try:
            current = process(child.pid)
        except (FileNotFoundError, ProcessLookupError):
            current = None
        if current is not None:
            if current['sid'] != sid or current['uid'] != 0:
                raise RuntimeError('Bluetooth child did not enter its expected root session')
            identity = {key: current[key] for key in ('pid', 'start', 'sid', 'uid')}
            if 'start' not in pending:
                if pending.get('pid', child.pid) != child.pid:
                    raise RuntimeError('Pending Bluetooth child PID changed')
                pending.update(identity)
                persist()
            elif identity != pending:
                raise RuntimeError('Bluetooth child birth identity changed during exec')
            if STOP:
                raise RuntimeError('Bluetooth startup stopped while waiting for child identity')
            if current['command'] == command:
                if current == previous and child.poll() is None:
                    return current
                previous = current
            else:
                previous = None
        if STOP:
            raise RuntimeError('Bluetooth startup stopped while waiting for child identity')
        if time.monotonic() >= deadline:
            raise RuntimeError('Bluetooth child executable identity did not stabilize before deadline')
        time.sleep(0.02)


def abort_spawn(child, pending, sid, *, grace=8):
    """Only the held Popen child may be signalled before argv is committed."""
    if child.poll() is not None:
        return False
    if not pending or pending['pid'] != child.pid or pending.get('sid') != sid:
        raise RuntimeError('Unverified pending child retained without signalling')
    # Popen still owns the unreaped birth; a saved record alone is insufficient.
    if not pending_alive(pending):
        if child.poll() is not None:
            return False
        raise RuntimeError('Pending child identity unavailable; retained without signalling')
    child.send_signal(signal.SIGTERM)
    try:
        child.wait(timeout=grace)
        return False
    except subprocess.TimeoutExpired:
        if child.poll() is not None:
            return False
        if not pending_alive(pending):
            raise RuntimeError('Pending child identity changed; retained without SIGKILL')
        child.send_signal(signal.SIGKILL)
        child.wait(timeout=2)
        return True


def session_members(sid):
    result = []
    for item in Path('/proc').iterdir():
        if not item.name.isdecimal():
            continue
        try:
            fields = (item / 'stat').read_text().rsplit(') ', 1)[1].split()
            if int(fields[3]) == sid and fields[0] != 'Z':
                result.append(process(int(item.name)))
        except (FileNotFoundError, ProcessLookupError):
            pass
    return result


def run(paths, key, arguments):
    return subprocess.run([paths[key], *arguments], env=ENV, stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=4,
                          text=True)


def bus_call(paths, method, argument=None):
    arguments = ['--system', '--print-reply', '--reply-timeout=2000',
                 '--dest=org.freedesktop.DBus', '/org/freedesktop/DBus',
                 'org.freedesktop.DBus.' + method]
    if argument is not None:
        arguments.append('string:' + argument)
    result = run(paths, 'dbus_send', arguments)
    if result.returncode:
        if method == 'GetNameOwner' and 'org.freedesktop.DBus.Error.NameHasNoOwner' in result.stderr:
            return None
        raise RuntimeError('Shared system D-Bus query failed: ' + result.stderr.strip())
    return result.stdout


def name_pid(paths, name):
    owner = bus_call(paths, 'GetNameOwner', name)
    if owner is None:
        return None
    match = re.search(r'\bstring "([^"]+)"', owner)
    if not match:
        raise RuntimeError('D-Bus returned an invalid name owner')
    answer = bus_call(paths, 'GetConnectionUnixProcessID', match.group(1))
    pid = re.search(r'\buint32 (\d+)\b', answer)
    if not pid:
        raise RuntimeError('D-Bus returned an invalid owner PID')
    return int(pid.group(1))


def shared_bus(paths):
    try:
        info = BUS_SOCKET.lstat()
    except FileNotFoundError as error:
        raise RuntimeError('Start the shared system D-Bus with native network startup before Bluetooth') from error
    if not stat.S_ISSOCK(info.st_mode):
        raise RuntimeError('Shared system D-Bus path is not a socket')
    pid = name_pid(paths, 'org.freedesktop.DBus')
    owner = process(pid, require_root=False)
    expected = trusted(paths['dbus_daemon'])
    if not owner or (Path('/proc') / str(pid) / 'exe').resolve() != expected:
        raise RuntimeError('Shared system bus must be owned by the verified dbus-daemon executable')
    if info.st_uid not in (0, owner['uid']):
        raise RuntimeError('Shared system D-Bus socket UID differs from its verified daemon')
    return owner


def probe_module(paths):
    if digest(trusted(paths['probe']).read_bytes()) != HASHES['probe']:
        raise RuntimeError('Reviewed Bluetooth probe changed since startup')
    spec = importlib.util.spec_from_file_location('tabs7_bluetooth_probe', paths['probe'])
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def assets(paths):
    for key, value in paths.items():
        resolved = trusted(value)
        if key in HASHES and digest(resolved.read_bytes()) != HASHES[key]:
            raise RuntimeError('Reviewed Bluetooth asset hash mismatch: ' + key)
        if key in ('python', 'bluetoothd', 'dbus_send', 'dbus_daemon', 'insmod', 'rmmod') and not os.access(value, os.X_OK):
            raise RuntimeError('Bluetooth executable is not executable: ' + key)
    if Path(paths['loader']).parent != Path(paths['probe']).parent:
        raise RuntimeError('Reviewed loader and probe must share one directory')
    raw = Path(paths['address']).read_bytes().strip()
    if not re.fullmatch(rb'[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}', raw) or raw.lower() in (b'00:00:00:00:00:00', b'ff:ff:ff:ff:ff:ff'):
        raise RuntimeError('Verified EFS bt_addr must contain one valid Bluetooth address')


def kernel_guard(paths=None):
    if os.uname().release != RELEASE or os.uname().machine not in ('aarch64', 'arm64'):
        raise RuntimeError('Bluetooth requires the reviewed native ARM64 vendor kernel')
    config_sha = digest(gzip.decompress(Path('/proc/config.gz').read_bytes()))
    if config_sha not in KERNEL_MODULES:
        raise RuntimeError('Bluetooth requires the exact reviewed native kernel config')
    names = {fields[2] for line in Path('/proc/kallsyms').read_text().splitlines()
             if len(fields := line.split()) == 3}
    for key in ('hci_sock_ops', 'hci_sock_destruct', 'hci_sock_bind'):
        if not any(name == key or name.startswith(key + '.') for name in names):
            raise RuntimeError('Repaired built-in HCI socket core is absent')
    # SYSVIPC and the namespaces change task_struct and the module symbol CRCs.
    # The original artifact stays for rollback.
    HASHES['module'], module_path = KERNEL_MODULES[config_sha]
    if paths is not None and paths['module'] == DEFAULTS['module']:
        paths['module'] = module_path


def elf_sections(data):
    if data[:6] != b'\x7fELF\x02\x01':
        raise RuntimeError('H4 artifact must be an ARM64 little-endian ELF module')
    if struct.unpack_from('<H', data, 18)[0] != 183:
        raise RuntimeError('H4 artifact is not ARM64')
    offset = struct.unpack_from('<Q', data, 40)[0]
    size, count, names_index = struct.unpack_from('<HHH', data, 58)
    if size != 64 or count == 0 or names_index >= count or offset + size * count > len(data):
        raise RuntimeError('Invalid H4 ELF section table')
    headers = [struct.unpack_from('<IIQQQQIIQQ', data, offset + size * index) for index in range(count)]
    names = data[headers[names_index][4]:headers[names_index][4] + headers[names_index][5]]
    result = {}
    for header in headers:
        end = names.find(b'\0', header[0])
        if end == -1:
            raise RuntimeError('Invalid H4 ELF section name')
        if header[1] != 8 and header[4] + header[5] > len(data):
            raise RuntimeError('Invalid H4 ELF section bounds')
        result[names[header[0]:end].decode()] = data[header[4]:header[4] + header[5]]
    return result


def module_guard(paths):
    data = trusted(paths['module']).read_bytes()
    if digest(data) != HASHES['module']:
        raise RuntimeError('Reviewed H4 module artifact changed since startup')
    sections = elf_sections(data)
    metadata = dict(part.decode().split('=', 1) for part in sections.get('.modinfo', b'').split(b'\0') if b'=' in part)
    if metadata.get('name') != 'tabs7_hci_uart' or metadata.get('vermagic', '').split()[:1] != [RELEASE]:
        raise RuntimeError('H4 module name or kernel vermagic does not match')
    loaded = Path('/sys/module/tabs7_hci_uart')
    if not loaded.is_dir():
        return False
    source = loaded / 'srcversion'
    note = loaded / 'notes/.note.gnu.build-id'
    same_source = bool(metadata.get('srcversion') and source.is_file() and source.read_text().strip() == metadata['srcversion'])
    expected_note = sections.get('.note.gnu.build-id')
    same_note = bool(expected_note and note.is_file() and note.read_bytes() == expected_note)
    if not (same_note if expected_note else same_source):
        raise RuntimeError('Cannot verify already-loaded H4 identity; start with it unloaded or provide its matching exported identity')
    if not re.search(r'^n_hci\s+15\s*$', Path('/proc/tty/ldiscs').read_text(), re.M):
        raise RuntimeError('Verified H4 module did not register N_HCI 15')
    return True


def radio():
    found = []
    for entry in Path('/sys/class/rfkill').glob('rfkill*'):
        if (entry / 'name').read_text().strip() != 'bt_power' or (entry / 'type').read_text().strip() != 'bluetooth':
            continue
        device = entry / 'device'
        compatible = (device / 'of_node/compatible').read_bytes().rstrip(b'\0').split(b'\0')
        if compatible != [b'qca,qca6390', b'qca,qca6490'] or (device / 'of_node').resolve().name != 'bt_qca6390' or (device / 'driver').resolve().name != 'bt_power':
            raise RuntimeError('bt_power is not the verified QCA6390 board device')
        found.append({'path': str(entry.resolve()), 'soft': (entry / 'soft').read_text().strip(),
                      'hard': (entry / 'hard').read_text().strip()})
    if len(found) != 1 or found[0]['soft'] not in ('0', '1') or found[0]['hard'] != '0':
        raise RuntimeError('Exactly one verified, hardware-unblocked bt_power radio is required')
    return found[0]


def write_radio(record, value):
    current = radio()
    if current['path'] != record['path']:
        raise RuntimeError('Bluetooth rfkill identity changed')
    (Path(current['path']) / 'soft').write_text(value + '\n')
    deadline = time.monotonic() + 2
    while radio()['soft'] != value:
        if time.monotonic() >= deadline:
            raise RuntimeError('Bluetooth rfkill state did not change')
        time.sleep(0.05)


def uart_unused(probe, *, blocked=False):
    facts, rdev = probe.inspect()
    ignored = {'bt_power must already be unblocked by the operator'} if blocked else set()
    reasons = [reason for reason in facts['query_refusal_reasons'] if reason not in ignored]
    if reasons:
        raise RuntimeError('Bluetooth UART refused: ' + '; '.join(reasons))
    return rdev


def prepare_radio(state):
    """Start from ROM while preserving the radio state owned before startup."""
    state['radio_owned'] = True
    state['radio_operation'] = 'temporary-cycle' if state['radio']['soft'] == '0' else 'owned-unblock'
    save(state)  # Both cycle directions are covered before any rfkill write.
    if state['radio']['soft'] == '0':
        write_radio(state['radio'], '1')
        time.sleep(0.2)
        if STOP:
            raise RuntimeError('Bluetooth startup stopped during its temporary power cycle')
        write_radio(state['radio'], '0')
        time.sleep(0.2)
        state['radio_owned'] = False
        state['radio_operation'] = 'temporary-cycle-complete'
        save(state)
    else:
        write_radio(state['radio'], '0')
    if STOP:
        raise RuntimeError('Bluetooth startup stopped')


def children(state):
    owner = state.get('owner')
    if not owner:
        return {}
    expected = {tuple(service['command']): name for name, service in state['services'].items()}
    found = {}
    for member in session_members(owner['sid']):
        if member['pid'] == owner['pid']:
            if member != owner:
                raise RuntimeError('Bluetooth session leader changed identity')
            continue
        name = expected.get(tuple(member['command']))
        if name is None or name in found:
            raise RuntimeError('Unmanaged live process in the recorded Bluetooth session')
        recorded = state['services'][name].get('process')
        if recorded and member != recorded:
            raise RuntimeError('Bluetooth child differs from its ownership record')
        if not recorded:
            pending = state['services'][name].get('pending')
            if not pending or any(member[key] != pending.get(key) for key in ('pid', 'start', 'sid', 'uid')):
                raise RuntimeError('Bluetooth child lacks a matching recorded birth identity')
        found[name] = member
    return found


def launch(state, role, command):
    service = {'command': command, 'process': None, 'pending': {}}
    state['services'][role] = service
    save(state)
    with log_file(role) as log:
        service['log_offset'] = log.tell()
        save(state)
        child = subprocess.Popen(command, env=ENV, stdin=subprocess.DEVNULL, stdout=log,
                                 stderr=log, close_fds=True)
    service['pending']['pid'] = child.pid
    save(state)
    try:
        record = await_identity(child, command, state['owner']['sid'], service['pending'],
                                lambda: save(state))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        service['launch_error'] = str(error)
        try:
            if abort_spawn(child, service['pending'], state['owner']['sid']):
                service['launch_cleanup_error'] = 'Pending child required SIGKILL; cleanup is unverified'
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as cleanup_error:
            service['launch_cleanup_error'] = str(cleanup_error)
        save(state)
        raise RuntimeError(role + ': ' + str(error)) from error
    service['process'] = record
    service.pop('pending')
    save(state)
    return child


def loader_events(state):
    service = state['services'].get('bootstrap', {})
    with (RUN / 'bootstrap.log').open('rb') as source:
        source.seek(service.get('log_offset', 0))
        data = source.read(1024 * 1024)
    events = []
    for line in data.splitlines():
        try:
            value = json.loads(line)
            if isinstance(value, dict):
                events.append(value)
        except (ValueError, UnicodeDecodeError):
            pass
    return events


def terminate(record, grace):
    if not live(record):
        return False
    os.kill(record['pid'], signal.SIGTERM)
    deadline = time.monotonic() + grace
    while live(record) and time.monotonic() < deadline:
        time.sleep(0.1)
    forced = live(record)
    if forced:
        os.kill(record['pid'], signal.SIGKILL)
        deadline = time.monotonic() + 2
        while live(record) and time.monotonic() < deadline:
            time.sleep(0.1)
        if live(record):
            raise RuntimeError('Bluetooth process did not exit after SIGKILL')
    return forced


def cleanup(state):
    state['state'] = 'stopping'
    save(state)
    errors = []
    for service in state['services'].values():
        if service.get('launch_cleanup_error'):
            errors.append(service['launch_cleanup_error'])
    try:
        managed = children(state)
    except (OSError, ValueError, RuntimeError) as error:
        managed = {}
        errors.append(str(error))
    for role, grace in (('bluetoothd', 5), ('bootstrap', 8)):
        try:
            recorded = state['services'].get(role, {}).get('process')
            target = managed.get(role) or recorded
            if target:
                forced = terminate(target, grace)
                if role == 'bootstrap' and forced:
                    errors.append('Bootstrap required SIGKILL; its cleanup is unverified')
            if role == 'bootstrap' and role in state['services']:
                events = loader_events(state)
                opened = any(event.get('phase') == 'before-open' for event in events)
                closed = [event for event in events if event.get('phase') == 'closed']
                if opened and (not closed or closed[-1].get('cleanup_errors') != []):
                    errors.append('Bootstrap UART cleanup completion is missing or reported errors')
        except (OSError, ValueError, RuntimeError) as error:
            errors.append(role + ': ' + str(error))
    try:
        if pending_alive(state.get('owner_pending')):
            raise RuntimeError('Uncommitted Bluetooth supervisor is still alive; ownership retained')
        remaining = children(state)
        if remaining:
            raise RuntimeError('Bluetooth children remain alive')
        paths = state['paths']
        probe = probe_module(paths)
        _, rdev = probe.inspect()
        if probe.owners(rdev) or list(Path('/sys/class/bluetooth').glob('hci*')):
            raise RuntimeError('External UART/HCI ownership prevents module or radio rollback')
        if name_pid(paths, 'org.bluez') is not None:
            raise RuntimeError('External BlueZ ownership prevents module or radio rollback')
        if state.get('module_owned'):
            if Path('/sys/module/tabs7_hci_uart').is_dir():
                module_guard(paths)
                result = run(paths, 'rmmod', ['tabs7_hci_uart'])
                if result.returncode or Path('/sys/module/tabs7_hci_uart').is_dir():
                    raise RuntimeError('Owned H4 module could not unload: ' + result.stderr.strip())
            state['module_owned'] = False
            save(state)
        if state.get('radio_owned'):
            if radio()['soft'] != state['radio']['soft']:
                write_radio(state['radio'], state['radio']['soft'])
            state['radio_owned'] = False
            save(state)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        errors.append(str(error))
    state['state'] = 'pending-cleanup' if errors else 'stopped'
    state['cleanup_errors'] = errors
    save(state)
    return not errors


def supervise():
    global STOP
    token = sys.stdin.readline().strip()
    state = load_state()
    current = process(os.getpid())
    if not state or token != state.get('nonce') or current != state.get('owner') or state['boot_id'] != boot_id():
        raise RuntimeError('Supervisor ownership gate refused startup')
    sys.stdin.close()

    def stop(_number, _frame):
        global STOP
        STOP = True

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    failed = None
    try:
        paths = state['paths']
        kernel_guard(paths)
        assets(paths)
        shared = shared_bus(paths)
        if shared['sid'] == state['owner']['sid'] or name_pid(paths, 'org.bluez') is not None:
            raise RuntimeError('Shared system bus or BlueZ has conflicting ownership')
        if radio() != state['radio']:
            raise RuntimeError('Bluetooth radio state changed before its owner began startup')
        probe = probe_module(paths)
        uart_unused(probe, blocked=state['radio']['soft'] == '1')
        if not module_guard(paths):
            state['module_owned'] = True  # Record intent before insmod can succeed.
            save(state)
            result = run(paths, 'insmod', [paths['module']])
            if result.returncode or not module_guard(paths):
                raise RuntimeError('Verified H4 module could not load: ' + result.stderr.strip())
        if STOP:
            raise RuntimeError('Bluetooth startup stopped')
        prepare_radio(state)
        command = [paths['python'], paths['loader'], '--bootstrap', '--patch', paths['patch'],
                   '--nvm', paths['nvm'], '--address-file', paths['address']]
        bootstrap = launch(state, 'bootstrap', command)
        deadline = time.monotonic() + 95
        while True:
            if STOP or bootstrap.poll() is not None:
                raise RuntimeError('Bluetooth bootstrap stopped before readiness; inspect bootstrap.log')
            ready = [event for event in loader_events(state) if event.get('phase') == 'hci-ready']
            if ready:
                event = ready[-1]
                if event.get('owner_pid') != bootstrap.pid or not event.get('uart_fd_held') or not event.get('uart_clock_vote_held') or event.get('baud') != 115200 or event.get('sibs_enabled') is not False or not re.fullmatch(r'hci\d+', event.get('controller', '')):
                    raise RuntimeError('Loader readiness does not prove the reviewed UART owner profile')
                controllers = [item.name for item in Path('/sys/class/bluetooth').glob('hci*')]
                if controllers != [event['controller']]:
                    raise RuntimeError('Actual HCI controllers differ from the loader readiness event')
                state['controller'] = event['controller']
                save(state)
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Bluetooth bootstrap readiness timed out; inspect bootstrap.log')
            time.sleep(0.1)
        if name_pid(paths, 'org.bluez') is not None:
            raise RuntimeError('An external BlueZ daemon acquired the shared bus during bootstrap')
        bluez = launch(state, 'bluetoothd', [paths['bluetoothd'], '--nodetach'])
        deadline = time.monotonic() + 10
        while True:
            if STOP or bootstrap.poll() is not None or bluez.poll() is not None:
                raise RuntimeError('Managed Bluetooth services exited before BlueZ readiness')
            owner = name_pid(paths, 'org.bluez')
            if owner is not None:
                if owner != bluez.pid or not live(state['services']['bluetoothd']['process']):
                    raise RuntimeError('org.bluez belongs to an unmanaged process')
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('BlueZ did not acquire org.bluez; inspect bluetoothd.log')
            time.sleep(0.1)
        state['state'] = 'ready'
        save(state)
        while not STOP:
            if bootstrap.poll() is not None or bluez.poll() is not None:
                raise RuntimeError('Managed Bluetooth service exited; inspect its retained log')
            if name_pid(paths, 'org.bluez') != bluez.pid:
                raise RuntimeError('org.bluez ownership changed')
            time.sleep(1)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        failed = str(error)
        state['error'] = failed
        save(state)
    finally:
        clean = cleanup(state)
    return 1 if failed or not clean else 0


def report(state):
    if not state:
        return {'state': 'stopped', 'managed': False}
    result = {key: state[key] for key in ('state', 'controller', 'error', 'cleanup_errors') if key in state}
    result.update(managed=True, ownership_record=str(STATE), log_directory=str(RUN))
    result['same_boot'] = state['boot_id'] == boot_id()
    result['supervisor_alive'] = live(state.get('owner')) if result['same_boot'] else False
    result['pending_supervisor_alive'] = pending_alive(state.get('owner_pending')) if result['same_boot'] else False
    result['services_alive'] = {name: live(service.get('process')) for name, service in state['services'].items()}
    if state['state'] in ('starting', 'stopping') and not result['supervisor_alive']:
        result['state'] = 'pending-cleanup'
    if result['pending_supervisor_alive']:
        result['state'] = 'pending-cleanup'
    if state['state'] == 'ready':
        expected = (state['services'].get('bluetoothd', {}).get('process') or {}).get('pid')
        if (not result['supervisor_alive'] or set(result['services_alive']) != {'bootstrap', 'bluetoothd'}
                or not all(result['services_alive'].values()) or name_pid(state['paths'], 'org.bluez') != expected
                or not (Path('/sys/class/bluetooth') / state.get('controller', '')).is_dir()):
            result['state'] = 'pending-cleanup'
    return result


def start(paths):
    previous = load_state()
    if previous:
        status = report(previous)
        if status['state'] == 'ready':
            if name_pid(previous['paths'], 'org.bluez') != previous['services']['bluetoothd']['process']['pid']:
                raise RuntimeError('org.bluez no longer belongs to the recorded Bluetooth owner')
            return status
        if previous['state'] != 'stopped' or status['supervisor_alive'] or status['pending_supervisor_alive'] or any(status['services_alive'].values()):
            raise RuntimeError('Recorded Bluetooth ownership needs cleanup; run stop and inspect ' + str(STATE))
        if previous.get('owner') and children(previous):
            raise RuntimeError('Stale Bluetooth record has live unmanaged session members')
    kernel_guard(paths)
    assets(paths)
    shared_bus(paths)
    if name_pid(paths, 'org.bluez') is not None:
        raise RuntimeError('An external BlueZ daemon already owns org.bluez; leave it untouched')
    probe = probe_module(paths)
    initial_radio = radio()
    uart_unused(probe, blocked=initial_radio['soft'] == '1')
    module_guard(paths)
    state = {'version': 1, 'state': 'starting', 'boot_id': boot_id(), 'nonce': secrets.token_hex(24),
             'paths': paths, 'radio': initial_radio, 'radio_owned': False,
             'module_owned': False, 'services': {}, 'owner': None}
    save(state)
    command = [paths['python'], str(Path(__file__).resolve()), '_run']
    trusted(command[1])
    with log_file('controller') as log:
        child = subprocess.Popen(command, env=ENV, stdin=subprocess.PIPE, stdout=log, stderr=log,
                                 start_new_session=True, close_fds=True, text=True)
    state['owner_pending'] = {'pid': child.pid}
    save(state)
    try:
        state['owner'] = await_identity(child, command, child.pid, state['owner_pending'],
                                        lambda: save(state))
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        child.stdin.close()
        state['error'] = 'Bluetooth supervisor: ' + str(error)
        state['state'] = 'pending-cleanup'
        state['cleanup_errors'] = []
        try:
            abort_spawn(child, state['owner_pending'], child.pid)
            state['state'] = 'stopped'  # The unopened startup gate prevented device operations.
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as cleanup_error:
            state['cleanup_errors'].append(str(cleanup_error))
        save(state)
        raise RuntimeError(state['error']) from error
    state.pop('owner_pending')
    save(state)  # No radio/module/firmware operation occurs until this owner is durable.
    child.stdin.write(state['nonce'] + '\n')
    child.stdin.close()
    deadline = time.monotonic() + 145
    while True:
        current = load_state()
        if current['state'] == 'ready':
            return report(current)
        if current['state'] in ('stopped', 'pending-cleanup') or child.poll() is not None:
            raise RuntimeError(current.get('error', 'Bluetooth supervisor exited; inspect controller.log'))
        if time.monotonic() >= deadline:
            terminate(state['owner'], 40)
            current = load_state()
            if current['state'] not in ('stopped', 'pending-cleanup'):
                cleanup(current)
            raise RuntimeError('Bluetooth startup exceeded its controller deadline; inspect retained ownership state')
        time.sleep(0.1)


def stop():
    state = load_state()
    if not state:
        return report(None)
    if state['boot_id'] != boot_id():
        raise RuntimeError('Ownership record belongs to another boot; inspect before any cleanup')
    if live(state.get('owner')):
        terminate(state['owner'], 40)
        current = load_state()
        if current['state'] not in ('stopped', 'pending-cleanup'):
            cleanup(current)
        return report(load_state())
    if state['state'] != 'stopped' or pending_alive(state.get('owner_pending')):
        cleanup(state)
    elif children(state):
        raise RuntimeError('Stopped ownership record has live unmanaged session members')
    return report(load_state())


def main():
    if sys.argv[1:] == ['_run']:
        if os.geteuid() != 0 or os.getuid() != 0:
            raise RuntimeError('Bluetooth supervisor requires root inside native Ubuntu')
        prepare_runtime()
        return supervise()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('start', 'stop', 'status'))
    parser.add_argument('--config', help='root-owned JSON containing asset path overrides')
    args = parser.parse_args()
    if os.geteuid() != 0 or os.getuid() != 0:
        raise RuntimeError('Run Bluetooth lifecycle commands as root inside native Ubuntu')
    prepare_runtime()
    if args.action == 'status':
        print(json.dumps(report(load_state()), sort_keys=True))
        return 0
    lock = os.open(RUN / 'lifecycle.lock', os.O_WRONLY | os.O_CREAT | os.O_CLOEXEC | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another Bluetooth lifecycle command is running; status remains available') from error
        result = start(load_paths(args.config)) if args.action == 'start' else stop()
        print(json.dumps(result, sort_keys=True))
        return 1 if result['state'] == 'pending-cleanup' else 0
    finally:
        os.close(lock)


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        print(json.dumps({'state': 'error', 'error': str(error), 'ownership_record': str(STATE)}), file=sys.stderr)
        raise SystemExit(1)
