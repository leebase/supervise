# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Content-addressed, read-only seals for worker evidence."""

import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from collections.abc import Mapping


class SealError(ValueError):
    """Raised when evidence cannot be sealed or a seal is invalid."""


CHUNK = 2 * 1024 * 1024
MAX_LOGICAL = 128 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
BINDING_FIELDS = (
    "mission_id",
    "transaction_id",
    "worker_id",
    "worker_role",
    "attempt_id",
    "source_id",
)

_BINDING_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_HEX_DIGEST = re.compile(r"[0-9a-f]{64}\Z")
_LINE_END = re.compile(b"[\\r\\n]")
_NAME_CREDENTIALS = {"auth.json", ".credentials.json", "credentials.json", "id_rsa", "id_ed25519"}


def _fail(message="invalid evidence seal"):
    raise SealError(message)


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _canonical(record):
    return json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _validate_binding(binding):
    if not isinstance(binding, Mapping) or set(binding) != set(BINDING_FIELDS):
        _fail("binding fields do not match the seal contract")
    result = {}
    for field in BINDING_FIELDS:
        value = binding[field]
        if not isinstance(value, str) or _BINDING_VALUE.fullmatch(value) is None:
            _fail("invalid binding value")
        result[field] = value
    return result


def _validate_name(name):
    if not isinstance(name, str) or not name or "\x00" in name or name.startswith("/"):
        _fail("invalid evidence filename")
    pieces = name.split("/")
    if any(piece in ("", ".", "..") for piece in pieces):
        _fail("invalid evidence filename")
    base = pieces[-1].casefold()
    if base in _NAME_CREDENTIALS or base.endswith((".pem", ".key")):
        _fail("credential filenames cannot be sealed")
    return name


def _valid_digest(value):
    return isinstance(value, str) and _HEX_DIGEST.fullmatch(value) is not None


def _positive_limits():
    chunk = CHUNK
    logical = MAX_LOGICAL
    total = MAX_TOTAL
    if (isinstance(chunk, bool) or not isinstance(chunk, int) or chunk <= 0 or
            isinstance(logical, bool) or not isinstance(logical, int) or logical < 0 or
            isinstance(total, bool) or not isinstance(total, int) or total < 0):
        _fail("invalid seal limits")
    return chunk, logical, total


def _directory_info(path, readonly=False):
    try:
        info = os.lstat(path)
    except OSError as error:
        raise SealError("required directory is unavailable") from error
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        _fail("expected a regular directory")
    if readonly and info.st_mode & 0o222:
        _fail("sealed directory is writable")
    return info


def _open_regular(path, readonly=False):
    """Open a regular file without following a final-component symlink."""
    try:
        before = os.lstat(path)
        if not stat.S_ISREG(before.st_mode) or stat.S_ISLNK(before.st_mode):
            _fail("evidence source is not a regular file")
        if readonly and before.st_mode & 0o222:
            _fail("sealed file is writable")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        descriptor = os.open(path, flags)
        try:
            after = os.fstat(descriptor)
            if not stat.S_ISREG(after.st_mode):
                _fail("evidence source is not a regular file")
            if readonly and after.st_mode & 0o222:
                _fail("sealed file is writable")
            return os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise
    except SealError:
        raise
    except (OSError, TypeError, ValueError) as error:
        raise SealError("evidence file is unavailable") from error


class _CredentialScanner:
    """Streaming detector with fixed-size state and linear work per byte."""

    def __init__(self):
        self.begin = b"-----" + b"begin" + b" "
        self.private_key = b"private " + b"key"
        self.begin_tail = b""
        self.key_tail = b""
        self.in_header = False

    def feed(self, block):
        data = block.lower()
        was_in_header = self.in_header
        overlap = self.key_tail if was_in_header else self.begin_tail
        combined = overlap + data
        position = len(overlap) if was_in_header else 0
        header_start = 0 if was_in_header else None

        while position < len(combined):
            if self.in_header:
                line_end = _LINE_END.search(combined, position)
                end = line_end.start() if line_end is not None else len(combined)
                check_from = max(header_start, position - len(self.private_key) + 1)
                if combined.find(self.private_key, check_from, end) >= 0:
                    _fail("private-key material cannot be sealed")
                if line_end is None:
                    self.key_tail = combined[-(len(self.private_key) - 1):]
                    return
                self.in_header = False
                self.key_tail = b""
                self.begin_tail = b""
                header_start = None
                position = end + 1
                continue

            found = combined.find(self.begin, position)
            if found < 0:
                self.begin_tail = combined[-(len(self.begin) - 1):]
                return
            self.in_header = True
            self.begin_tail = b""
            self.key_tail = b""
            position = found + len(self.begin)
            header_start = position


def _write_chunk(directory, digest, data, written):
    if digest in written:
        return
    path = os.path.join(directory, digest)
    try:
        with open(path, "xb") as output:
            output.write(data)
        os.chmod(path, 0o400)
    except OSError as error:
        raise SealError("could not write seal chunk") from error
    written.add(digest)


def _remove_tree(path):
    try:
        info = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
        os.chmod(path, 0o700)
        for name in os.listdir(path):
            _remove_tree(os.path.join(path, name))
        os.rmdir(path)
    else:
        os.unlink(path)


def _read_small_regular(path, limit=None):
    with _open_regular(path, readonly=True) as source:
        info = os.fstat(source.fileno())
        if limit is not None and info.st_size > limit:
            _fail("seal metadata is too large")
        return source.read()


def _load_manifest(handle):
    if not isinstance(handle, Mapping) or set(handle) != {"path", "identity"}:
        _fail("invalid seal handle")
    identity = handle["identity"]
    if not _valid_digest(identity):
        _fail("invalid seal identity")
    try:
        bundle = os.path.abspath(os.fspath(handle["path"]))
    except (TypeError, ValueError, OSError) as error:
        raise SealError("invalid seal path") from error
    if os.path.basename(bundle) != identity:
        _fail("seal path does not match its identity")
    _directory_info(bundle, readonly=True)
    names = set(os.listdir(bundle))
    if names != {"seal.json", "chunks"}:
        _fail("sealed bundle has unexpected entries")
    seal_path = os.path.join(bundle, "seal.json")
    raw = _read_small_regular(seal_path)
    try:
        record = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise SealError("invalid seal metadata") from error
    if not isinstance(record, dict) or _canonical(record) != raw:
        _fail("seal metadata is not canonical")
    if _digest(raw) != identity:
        _fail("seal identity mismatch")
    return bundle, record


def _read_chunk(path, expected_size, chunk_limit):
    with _open_regular(path, readonly=True) as source:
        info = os.fstat(source.fileno())
        if info.st_size != expected_size or info.st_size > chunk_limit:
            _fail("seal chunk has an invalid size")
        data = source.read(chunk_limit + 1)
    if len(data) != expected_size:
        _fail("seal chunk is missing or truncated")
    return data


def _verify_loaded(handle, expected_binding, expected_identity):
    bundle, record = _load_manifest(handle)
    identity = handle["identity"]
    if expected_identity != identity or not _valid_digest(expected_identity):
        _fail("unexpected seal identity")
    binding = _validate_binding(record.get("binding")) if isinstance(record, dict) else None
    if binding != _validate_binding(expected_binding):
        _fail("unexpected seal binding")
    if set(record) != {"schema", "binding", "files"}:
        _fail("invalid seal metadata fields")
    if type(record["schema"]) is not int or record["schema"] != 1:
        _fail("unsupported seal schema")
    files = record["files"]
    if not isinstance(files, dict):
        _fail("invalid seal file table")

    chunk_limit, logical_limit, total_limit = _positive_limits()
    total_size = 0
    expected_chunks = set()
    result = {}
    chunk_directory = os.path.join(bundle, "chunks")
    _directory_info(chunk_directory, readonly=True)

    for name, entry in files.items():
        _validate_name(name)
        if not isinstance(entry, dict) or set(entry) != {"size", "sha256", "chunks"}:
            _fail("invalid file record")
        size = entry["size"]
        digest = entry["sha256"]
        chunks = entry["chunks"]
        if isinstance(size, bool) or not isinstance(size, int) or size < 0 or size > logical_limit:
            _fail("file exceeds logical size limit")
        if not _valid_digest(digest) or not isinstance(chunks, list):
            _fail("invalid file digest record")
        total_size += size
        if total_size > total_limit:
            _fail("seal exceeds total size limit")
        count = (size + chunk_limit - 1) // chunk_limit
        if len(chunks) != count:
            _fail("invalid file chunk list")
        file_hash = hashlib.sha256()
        scanner = _CredentialScanner()
        seen_size = 0
        for index, chunk_digest in enumerate(chunks):
            if not _valid_digest(chunk_digest):
                _fail("invalid chunk digest")
            expected_size = min(chunk_limit, size - seen_size)
            expected_chunks.add(chunk_digest)
            chunk_path = os.path.join(chunk_directory, chunk_digest)
            content = _read_chunk(chunk_path, expected_size, chunk_limit)
            if _digest(content) != chunk_digest:
                _fail("seal chunk digest mismatch")
            file_hash.update(content)
            scanner.feed(content)
            seen_size += len(content)
        if seen_size != size or file_hash.hexdigest() != digest:
            _fail("sealed file digest mismatch")
        result[name] = digest

    if set(os.listdir(chunk_directory)) != expected_chunks:
        _fail("sealed bundle has missing or extra chunks")
    return {"files": result}


def seal(store, *, binding, files, pins):
    """Seal named regular source files into a private content-addressed store."""
    chunk_limit, logical_limit, total_limit = _positive_limits()
    validated_binding = _validate_binding(binding)
    if not isinstance(files, Mapping) or not isinstance(pins, Mapping):
        _fail("files and pins must be mappings")
    validated_files = {}
    for name, source in files.items():
        validated_files[_validate_name(name)] = source
    validated_pins = {}
    for name, digest in pins.items():
        _validate_name(name)
        if name not in validated_files or not _valid_digest(digest):
            _fail("invalid pinned digest")
        validated_pins[name] = digest

    try:
        store_path = os.path.abspath(os.fspath(store))
    except (TypeError, ValueError, OSError) as error:
        raise SealError("invalid seal store") from error
    store_info = _directory_info(store_path)
    if store_info.st_mode & 0o077:
        _fail("seal store must be private")

    try:
        staging = tempfile.mkdtemp(prefix=".seal-", dir=store_path)
        os.chmod(staging, 0o700)
        chunk_directory = os.path.join(staging, "chunks")
        os.mkdir(chunk_directory, 0o700)
        written_chunks = set()
        total_size = 0
        file_records = {}

        for name in sorted(validated_files):
            file_hash = hashlib.sha256()
            scanner = _CredentialScanner()
            file_chunks = []
            file_size = 0
            pending = bytearray()
            with _open_regular(validated_files[name]) as source:
                while True:
                    remaining_logical = logical_limit - file_size
                    remaining_total = total_limit - total_size
                    if remaining_logical < 0 or remaining_total < 0:
                        _fail("evidence exceeds size limit")
                    request = min(chunk_limit - len(pending), remaining_logical + 1, remaining_total + 1)
                    data = source.read(request)
                    if not data:
                        break
                    if len(data) > remaining_logical or len(data) > remaining_total:
                        _fail("evidence exceeds size limit")
                    scanner.feed(data)
                    file_hash.update(data)
                    file_size += len(data)
                    total_size += len(data)
                    pending.extend(data)
                    if len(pending) == chunk_limit:
                        content = bytes(pending)
                        chunk_digest = _digest(content)
                        _write_chunk(chunk_directory, chunk_digest, content, written_chunks)
                        file_chunks.append(chunk_digest)
                        pending.clear()
            if pending:
                content = bytes(pending)
                chunk_digest = _digest(content)
                _write_chunk(chunk_directory, chunk_digest, content, written_chunks)
                file_chunks.append(chunk_digest)
            digest = file_hash.hexdigest()
            if name in validated_pins and validated_pins[name] != digest:
                _fail("pinned digest mismatch")
            file_records[name] = {"size": file_size, "sha256": digest, "chunks": file_chunks}

        record = {"schema": 1, "binding": validated_binding, "files": file_records}
        serialized = _canonical(record)
        identity = _digest(serialized)
        seal_path = os.path.join(staging, "seal.json")
        with open(seal_path, "xb") as output:
            output.write(serialized)
        os.chmod(seal_path, 0o400)
        os.chmod(chunk_directory, 0o500)
        os.chmod(staging, 0o500)

        destination = os.path.join(store_path, identity)
        handle = {"path": destination, "identity": identity}
        if os.path.lexists(destination):
            _verify_loaded(handle, validated_binding, identity)
            _remove_tree(staging)
            return handle
        try:
            os.rename(staging, destination)
            staging = None
        except OSError:
            if not os.path.lexists(destination):
                raise
            _verify_loaded(handle, validated_binding, identity)
            _remove_tree(staging)
            staging = None
        return handle
    except SealError:
        if "staging" in locals() and staging and os.path.lexists(staging):
            _remove_tree(staging)
        raise
    except Exception as error:
        if "staging" in locals() and staging and os.path.lexists(staging):
            try:
                _remove_tree(staging)
            except OSError:
                pass
        raise SealError("could not create evidence seal") from error


def verify(handle, *, expected_binding, expected_identity):
    """Verify a seal's binding, manifest, chunks, and exact directory contents."""
    try:
        return _verify_loaded(handle, expected_binding, expected_identity)
    except SealError:
        raise
    except Exception as error:
        raise SealError("invalid evidence seal") from error


def read(handle, name):
    """Return a file from a fully verified seal."""
    try:
        bundle, record = _load_manifest(handle)
        binding = _validate_binding(record.get("binding")) if isinstance(record, dict) else None
        verify(handle, expected_binding=binding, expected_identity=handle["identity"])
        _validate_name(name)
        entry = record["files"].get(name)
        if entry is None:
            _fail("file is not present in the seal")
        chunk_limit, _, _ = _positive_limits()
        chunks_directory = os.path.join(bundle, "chunks")
        output = []
        file_hash = hashlib.sha256()
        size = 0
        for chunk_digest in entry["chunks"]:
            content = _read_chunk(os.path.join(chunks_directory, chunk_digest),
                                  min(chunk_limit, entry["size"] - size), chunk_limit)
            if _digest(content) != chunk_digest:
                _fail("seal chunk digest mismatch")
            output.append(content)
            file_hash.update(content)
            size += len(content)
        data = b"".join(output)
        if size != entry["size"] or file_hash.hexdigest() != entry["sha256"]:
            _fail("sealed file digest mismatch")
        return data
    except SealError:
        raise
    except Exception as error:
        raise SealError("could not read sealed evidence") from error
