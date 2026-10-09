# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Elapsed-window and worker-runtime budgeting for a supervised invocation."""

import hashlib
import json
import math
import os
from pathlib import Path
import stat
import time

from supervise.kernel import journal as _journal


PINS = ('pending.json', 'acceptance.json', 'elapsed-checkpoint.json', 'contract.json', 'binding.json')
__all__ = ('PINS', 'BudgetError', 'sha', 'remaining', 'worker_allocation', 'fit_test_run', 'authorize')


class BudgetError(ValueError):
    """A budget or continuation record is invalid or has been tampered with."""


def sha(path):
    """Return the SHA-256 digest of a file's exact bytes."""
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _fail(message):
    raise BudgetError(message)


def _number(value, name, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail(name + ' must be a finite number')
    try:
        valid = math.isfinite(value) and (value > 0 if positive else value >= 0)
    except (OverflowError, TypeError):
        valid = False
    if not valid:
        _fail(name + ' must be a finite ' + ('positive' if positive else 'nonnegative') + ' number')
    return float(value)


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail('duplicate JSON field')
        result[key] = value
    return result


def _bad_constant(value):
    _fail('non-finite JSON value: ' + value)


def _json_value(path):
    try:
        with Path(path).open('r', encoding='utf-8') as stream:
            return json.load(stream, object_pairs_hook=_json_object, parse_constant=_bad_constant)
    except BudgetError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise BudgetError('invalid JSON in ' + Path(path).name) from error


def _json(path):
    value = _json_value(path)
    if not isinstance(value, dict):
        _fail(Path(path).name + ' must contain an object')
    return value


def _user_limits(acceptance):
    if isinstance(acceptance, dict):
        limits = acceptance.get('user_limits', {})
    elif isinstance(acceptance, list):
        if any(not isinstance(row, dict) for row in acceptance):
            _fail('acceptance rows must be objects')
        limits = {}
    else:
        _fail('acceptance.json must contain an object or acceptance rows')
    if not isinstance(limits, dict):
        _fail('acceptance user_limits must be an object')
    return limits


def _real_file(path, *, immutable=False):
    path = Path(path)
    try:
        info = path.lstat()
    except OSError as error:
        raise BudgetError('missing or unreadable file: ' + path.name) from error
    if not stat.S_ISREG(info.st_mode):
        _fail('expected a regular non-symlink file: ' + path.name)
    if immutable and info.st_mode & 0o222:
        _fail('writable immutable file: ' + path.name)
    return info


def _home(home):
    path = Path(home)
    try:
        info = path.lstat()
    except OSError as error:
        raise BudgetError('invocation home must be private') from error
    if (not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode)
            or stat.S_IMODE(info.st_mode) != 0o700):
        _fail('invocation home must be private (mode 0700)')
    return path


def _pins(home):
    hashes = {}
    for name in PINS:
        _real_file(home / name, immutable=True)
        try:
            hashes[name] = sha(home / name)
        except OSError as error:
            raise BudgetError('cannot read pinned file: ' + name) from error
    return hashes


def _base(home):
    pins = _pins(home)
    pending = _json(home / 'pending.json')
    acceptance = _json_value(home / 'acceptance.json')
    checkpoint = _json(home / 'elapsed-checkpoint.json')
    invocation = pending.get('invocation_id')
    if not isinstance(invocation, str) or not invocation:
        _fail('pending invocation_id is invalid')
    cwd = pending.get('cwd')
    if not isinstance(cwd, str) or not cwd:
        _fail('pending cwd is invalid')
    project = Path(cwd)
    if not project.is_absolute():
        _fail('pending cwd must be absolute')
    try:
        project = project.resolve(strict=True)
    except OSError as error:
        raise BudgetError('pending cwd is unavailable') from error
    if not project.is_dir():
        _fail('pending cwd is not a directory')
    opened = _number(checkpoint.get('opened_at'), 'opened_at')
    maximum = _number(checkpoint.get('max_seconds'), 'max_seconds', positive=True)
    _user_limits(acceptance)
    return pins, pending, acceptance, opened, maximum, project


def _journal_stats(home):
    path = home / 'STATE.jsonl'
    if not os.path.lexists(path):
        return 0, 0, 0.0
    _real_file(path)
    try:
        events = _journal.read_events(path)
    except (_journal.JournalError, OSError, ValueError) as error:
        raise BudgetError('invalid parent journal') from error
    starts = sum(event.get('kind') == 'start' for event in events)
    accounted = [event for event in events if event.get('kind') in ('finish', 'reconcile')]
    try:
        seconds = math.fsum(_number(event.get('seconds'), 'journal seconds') for event in accounted)
    except OverflowError as error:
        raise BudgetError('journal accounting overflow') from error
    if not math.isfinite(seconds):
        _fail('journal accounting is not finite')
    return starts, len(accounted), seconds


def _strict_readonly(path):
    info = _real_file(path, immutable=True)
    if stat.S_IMODE(info.st_mode) != 0o444:
        _fail('continuation record must be mode 0444: ' + Path(path).name)
    try:
        return Path(path).read_bytes()
    except OSError as error:
        raise BudgetError('cannot read continuation file: ' + Path(path).name) from error


def _continuation_names(home):
    records, snapshots = {}, {}
    try:
        names = [entry.name for entry in home.iterdir()]
    except OSError as error:
        raise BudgetError('cannot inspect invocation home') from error
    for name in names:
        if not name.startswith('continuation-'):
            continue
        if name.endswith('.json'):
            number_text = name[len('continuation-'):-len('.json')]
            if len(number_text) != 4 or not number_text.isdigit():
                _fail('invalid continuation sequence name')
            number = int(number_text)
            if number in records:
                _fail('duplicate continuation sequence')
            records[number] = home / name
        elif name.endswith('-authority.txt'):
            number_text = name[len('continuation-'):-len('-authority.txt')]
            if len(number_text) != 4 or not number_text.isdigit():
                _fail('invalid authority snapshot name')
            number = int(number_text)
            if number in snapshots:
                _fail('duplicate authority snapshot')
            snapshots[number] = home / name
        else:
            _fail('unrecognized continuation artifact')
    if set(records) != set(snapshots):
        _fail('continuation authority snapshot is missing')
    numbers = sorted(records)
    if numbers != list(range(1, len(numbers) + 1)):
        _fail('continuation sequence gap')
    return [(number, records[number], snapshots[number]) for number in numbers]


def _outside_project(path, project):
    try:
        path.relative_to(project)
        return False
    except ValueError:
        return True


def _verify_chain(home, base):
    pins, pending, _acceptance, opened, maximum, project = base
    previous_digest = None
    prior_window_end = opened + maximum
    expected_pins = None
    previous_starts = 0
    previous_seconds = 0.0
    entries = _continuation_names(home)
    for number, record_path, snapshot_path in entries:
        record_bytes = _strict_readonly(record_path)
        snapshot_bytes = _strict_readonly(snapshot_path)
        try:
            record = json.loads(record_bytes.decode('utf-8'), object_pairs_hook=_json_object,
                                parse_constant=_bad_constant)
        except BudgetError:
            raise
        except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
            raise BudgetError('invalid continuation record') from error
        if not isinstance(record, dict):
            _fail('continuation record must be an object')
        required = {'kind', 'number', 'previous_sha256', 'invocation_id', 'original_pins',
                    'authority_source', 'authority_snapshot', 'authority_sha256', 'opened_at',
                    'max_seconds', 'starts', 'accounted_seconds', 'additional_starts',
                    'additional_seconds'}
        if set(record) != required:
            _fail('invalid continuation record fields')
        if (record['kind'] != 'same_objective_continuation' or type(record['number']) is not int
                or record['number'] != number or record['invocation_id'] != pending['invocation_id']):
            _fail('continuation identity mismatch')
        if record['previous_sha256'] != previous_digest:
            _fail('broken continuation chain')
        item_pins = record['original_pins']
        if not isinstance(item_pins, dict) or set(item_pins) != set(PINS):
            _fail('invalid original pin set')
        if any(not isinstance(value, str) or len(value) != 64 for value in item_pins.values()):
            _fail('invalid original pin digest')
        if expected_pins is None:
            expected_pins = item_pins
            if item_pins != pins:
                _fail('original pinned file changed')
        elif item_pins != expected_pins:
            _fail('continuation changed original pins')
        expected_snapshot = 'continuation-%04d-authority.txt' % number
        if record['authority_snapshot'] != expected_snapshot or snapshot_path.name != expected_snapshot:
            _fail('authority snapshot name mismatch')
        if not isinstance(record['authority_source'], str) or not Path(record['authority_source']).is_absolute():
            _fail('invalid authority source')
        source_path = Path(record['authority_source'])
        if source_path.resolve() != source_path or not _outside_project(source_path, project):
            _fail('authority source must be outside the project')
        if record['authority_sha256'] != hashlib.sha256(snapshot_bytes).hexdigest():
            _fail('authority snapshot digest mismatch')
        new_opened = _number(record['opened_at'], 'continuation opened_at')
        new_maximum = _number(record['max_seconds'], 'continuation max_seconds', positive=True)
        if new_maximum > 7200:
            _fail('continuation window exceeds bound')
        if new_opened < prior_window_end:
            _fail('continuation was opened before the previous window expired')
        if type(record['starts']) is not int or record['starts'] < previous_starts:
            _fail('continuation start counter was reset')
        seconds = _number(record['accounted_seconds'], 'accounted_seconds')
        if seconds < previous_seconds:
            _fail('continuation accounting was reset')
        extra_seconds = _number(record['additional_seconds'], 'additional_seconds')
        if type(record['additional_starts']) is not int or record['additional_starts'] != 0 or extra_seconds != 0:
            _fail('continuation cannot add starts or native seconds')
        current_digest = hashlib.sha256(record_bytes).hexdigest()
        previous_digest = current_digest
        prior_window_end = new_opened + new_maximum
        previous_starts, previous_seconds = record['starts'], seconds
    if entries:
        live_starts, _live_accounted, live_seconds = _journal_stats(home)
        if live_starts < previous_starts or live_seconds < previous_seconds:
            _fail('journal counters do not preserve continuation accounting')
        return _record_window(entries[-1][1])
    return opened, maximum


def _record_window(path):
    try:
        data = json.loads(_strict_readonly(path).decode('utf-8'), object_pairs_hook=_json_object,
                          parse_constant=_bad_constant)
    except BudgetError:
        raise
    except (UnicodeError, json.JSONDecodeError, TypeError, ValueError) as error:
        raise BudgetError('invalid continuation record') from error
    return data['opened_at'], data['max_seconds']


def remaining(home):
    """Seconds left in the current immutable elapsed window; may be negative."""
    home = _home(home)
    base = _base(home)
    opened, maximum = _verify_chain(home, base)
    left = opened + maximum - time.time()
    if not math.isfinite(left):
        _fail('elapsed window is not finite')
    return left


def _admitted(admitted):
    if not isinstance(admitted, dict):
        _fail('admitted intent must be an object')
    timeout = _number(admitted.get('timeout_seconds'), 'timeout_seconds', positive=True)
    tests = admitted.get('tests')
    checkpoint = admitted.get('checkpoint')
    if not isinstance(tests, dict) or not isinstance(checkpoint, dict):
        _fail('admitted tests and checkpoint must be objects')
    test_seconds = _number(tests.get('timeout_seconds'), 'tests.timeout_seconds', positive=True)
    max_seconds = _number(checkpoint.get('max_seconds'), 'checkpoint.max_seconds', positive=True)
    max_starts = checkpoint.get('max_starts')
    if type(max_starts) is not int or max_starts < 0:
        _fail('checkpoint.max_starts must be a nonnegative integer')
    return timeout, test_seconds, max_seconds, max_starts


def worker_allocation(home, admitted, starts, finishes, *, test_timeout_reserve=5.0):
    """Compute the next worker allowance without exceeding admitted or elapsed bounds."""
    timeout, test_seconds, max_seconds, max_starts = _admitted(admitted)
    reserve = _number(test_timeout_reserve, 'test_timeout_reserve')
    try:
        count = len(starts)
    except TypeError as error:
        raise BudgetError('starts must be a sized sequence') from error
    if count >= max_starts:
        return 0.0
    if not isinstance(finishes, dict):
        _fail('finishes must be a mapping')
    accounted = []
    for event in finishes.values():
        if not isinstance(event, dict):
            _fail('finish event must be an object')
        if event.get('accounted') is True:
            accounted.append(_number(event.get('seconds'), 'accounted seconds'))
    try:
        native_seconds = math.fsum(accounted)
    except OverflowError as error:
        raise BudgetError('accounted seconds overflow') from error
    if not math.isfinite(native_seconds):
        _fail('accounted seconds are not finite')
    available = min(timeout, max_seconds - native_seconds,
                    remaining(home) - (test_seconds + reserve))
    return max(0.0, available)


def fit_test_run(home, timeout_seconds, *, cleanup_seconds=5.0):
    """Return the requested test timeout only when cleanup also fits the window."""
    timeout = _number(timeout_seconds, 'timeout_seconds')
    cleanup = _number(cleanup_seconds, 'cleanup_seconds')
    if remaining(home) < timeout + cleanup:
        _fail('test run cannot fit in the remaining elapsed window')
    return timeout


def _source_bytes(source, project):
    path = Path(source)
    try:
        absolute = Path(os.path.abspath(os.fspath(path)))
        info = absolute.lstat()
    except (OSError, TypeError, ValueError) as error:
        raise BudgetError('authority source must be a regular file outside the project') from error
    if not stat.S_ISREG(info.st_mode) or absolute.resolve() != absolute:
        _fail('authority source must be a regular non-symlink file outside the project')
    if not _outside_project(absolute, project):
        _fail('authority source must be outside the project')
    flags = os.O_RDONLY
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(absolute, flags)
        with os.fdopen(descriptor, 'rb') as stream:
            before = os.fstat(stream.fileno())
            if not stat.S_ISREG(before.st_mode) or (before.st_ino, before.st_dev) != (info.st_ino, info.st_dev):
                _fail('authority source must be a regular non-symlink file outside the project')
            data = stream.read()
            after = os.fstat(stream.fileno())
    except BudgetError:
        raise
    except OSError as error:
        raise BudgetError('cannot read authority source') from error
    if (before.st_ino != after.st_ino or before.st_dev != after.st_dev
            or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns or len(data) != after.st_size):
        _fail('authority source changed while being read')
    return absolute, data


def _journal_for_authorization(home):
    starts, accounted, seconds = _journal_stats(home)
    if starts != accounted:
        _fail('unaccounted journal start blocks continuation')
    return starts, seconds


def _write_immutable(path, data):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, 'O_NOFOLLOW'):
        flags |= os.O_NOFOLLOW
    created = False
    try:
        descriptor = os.open(path, flags, 0o444)
        created = True
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(path, 0o444)
    except OSError as error:
        if created:
            try:
                Path(path).unlink()
            except OSError:
                pass
        raise BudgetError('cannot write immutable continuation artifact') from error


def authorize(home, source, seconds):
    """Authorize an operator-issued, same-objective continuation of an expired window."""
    if _journal.WORKER_ENV in os.environ:
        _fail('worker cannot authorize a continuation')
    maximum = _number(seconds, 'continuation seconds', positive=True)
    if maximum > 7200:
        _fail('continuation seconds exceed the 7200 second bound')
    home = _home(home)
    base = _base(home)
    pins, pending, acceptance, _opened, _initial_maximum, project = base
    if _user_limits(acceptance):
        _fail('outer limit prevents continuation')
    current_left = remaining(home)
    if current_left > 0:
        _fail('active window cannot be continued')
    starts, native_seconds = _journal_for_authorization(home)
    source_path, authority_bytes = _source_bytes(source, project)
    entries = _continuation_names(home)
    number = len(entries) + 1
    if number > 9999:
        _fail('continuation sequence limit exceeded')
    previous_digest = None
    if entries:
        previous_digest = sha(entries[-1][1])
    opened_at = time.time()
    snapshot_name = 'continuation-%04d-authority.txt' % number
    record = {
        'kind': 'same_objective_continuation',
        'number': number,
        'previous_sha256': previous_digest,
        'invocation_id': pending['invocation_id'],
        'original_pins': pins,
        'authority_source': str(source_path),
        'authority_snapshot': snapshot_name,
        'authority_sha256': hashlib.sha256(authority_bytes).hexdigest(),
        'opened_at': opened_at,
        'max_seconds': maximum,
        'starts': starts,
        'accounted_seconds': native_seconds,
        'additional_starts': 0,
        'additional_seconds': 0,
    }
    record_bytes = json.dumps(record, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
    snapshot_path = home / snapshot_name
    record_path = home / ('continuation-%04d.json' % number)
    _write_immutable(snapshot_path, authority_bytes)
    try:
        _write_immutable(record_path, record_bytes)
    except BudgetError:
        try:
            snapshot_path.unlink()
        except OSError:
            pass
        raise
    return record
