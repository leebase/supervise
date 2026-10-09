# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Lee Harrington
"""Exclusive, hash-verified installation of portable supervise releases."""

from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
import uuid

from .schemas import validate as _validate_schema


class InstallError(Exception):
    """An installation cannot safely be completed."""


def _copy_file(source, target):
    """Copy one file; kept as a module seam for interruption testing."""
    shutil.copyfile(source, target)


def _write_pointer(path, data):
    """Atomically replace a pointer file with bytes from *data*."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, data, mode=0o600)


def _atomic_write(path, data, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def _json_bytes(value):
    return (json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def _digest(path):
    result = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()


def _package_files(package):
    """Yield regular package files by their path relative to source_root."""
    for path in sorted(package.rglob("*")):
        relative = path.relative_to(package)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_file():
            yield path, PurePosixPath("supervise", *relative.parts).as_posix()


def _release_name(value):
    if value is None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        return stamp + "-" + uuid.uuid4().hex[:12]
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise InstallError("invalid release name")
    if "/" in value or "\\" in value or Path(value).is_absolute() or "\x00" in value:
        raise InstallError("release name must be a single path component")
    return value


def _read_pointer(pointer):
    if pointer is None:
        return False, None
    try:
        return True, Path(pointer).read_bytes()
    except FileNotFoundError:
        return False, None


def _named_release(data):
    """Extract an absolute production release path from pointer text, if any."""
    if not data:
        return None
    text = data.decode("utf-8", errors="ignore")
    value = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("release ="):
            value = stripped.partition("=")[2].strip().strip("\\\"'")
            break
    if value is None:
        match = re.search(r"(/[^\s\"'<>]*?/supervise/releases/[^\s\"'<>]*)", text)
        if not match:
            return None
        value = match.group(1)
    value = value.rstrip(",;)]}")
    if "/supervise/releases/" not in value:
        return None
    try:
        return Path(value).resolve()
    except (OSError, ValueError):
        return None


def _inside(path, root):
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def _safe_manifest_path(name):
    if not isinstance(name, str) or not name or "\\" in name:
        return None
    relative = PurePosixPath(name)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in relative.parts):
        return None
    if relative.as_posix() != name:
        return None
    return relative


def _remove_release(path):
    path = Path(path)
    if not path.exists():
        return
    for current, directories, files in os.walk(path, topdown=False, followlinks=False):
        for name in files:
            item = Path(current) / name
            if not item.is_symlink():
                try:
                    item.chmod(stat.S_IMODE(item.stat().st_mode) | 0o600)
                except OSError:
                    pass
        for name in directories:
            item = Path(current) / name
            if not item.is_symlink():
                try:
                    item.chmod(stat.S_IMODE(item.stat().st_mode) | 0o700)
                except OSError:
                    pass
    try:
        path.chmod(stat.S_IMODE(path.stat().st_mode) | 0o700)
    except OSError:
        pass
    shutil.rmtree(path)


def _make_read_only(root):
    root = Path(root)
    entries = sorted(root.rglob("*"), key=lambda path: len(path.parts), reverse=True)
    for path in entries:
        if path.is_symlink():
            raise InstallError("release contains a symbolic link: %s" % path)
        mode = stat.S_IMODE(path.stat().st_mode) & ~0o222
        path.chmod(mode)
    root.chmod(stat.S_IMODE(root.stat().st_mode) & ~0o222)


def _record_release(record, data_root):
    release = record.get("release")
    if not isinstance(release, str) or not release:
        raise ValueError("installation release is missing")
    candidate = Path(release)
    if candidate.is_absolute():
        location = candidate.resolve()
        if not _inside(location, Path(data_root) / "releases"):
            raise ValueError("installation release is outside data root")
        return location, location.name
    if release in (".", "..") or "/" in release or "\\" in release:
        raise ValueError("invalid installation release")
    return Path(data_root) / "releases" / release, release


def install(source_root, *, data_root, pointer_path=None, release=None):
    """Install an immutable release and publish it through an optional pointer."""
    source = Path(source_root).resolve()
    data = Path(data_root).resolve()
    package = source / "supervise"
    if not package.is_dir():
        raise InstallError("source_root must contain the supervise package")
    if _inside(data, source):
        raise InstallError("data_root cannot be inside source_root")

    name = _release_name(release)
    releases = data / "releases"
    target = releases / name
    if target.exists() or target.is_symlink():
        raise InstallError("release already exists: %s" % name)

    pointer = Path(pointer_path).absolute() if pointer_path is not None else None
    pointer_existed, pointer_before = _read_pointer(pointer)
    pointer_release = _named_release(pointer_before)
    if pointer_release is not None and not _inside(pointer_release, data):
        raise InstallError("pointer names a release owned by another data root")

    previous_record_path = data / "INSTALLATION.json"
    previous_record_exists = previous_record_path.exists()
    previous_record_bytes = previous_record_path.read_bytes() if previous_record_exists else None
    predecessor = None
    if previous_record_bytes:
        try:
            previous = json.loads(previous_record_bytes.decode("utf-8"))
            old_name = previous.get("release")
            if isinstance(old_name, str) and old_name:
                predecessor = Path(old_name).name
        except (ValueError, UnicodeError, AttributeError):
            predecessor = None
    if predecessor is None and pointer_release is not None and _inside(pointer_release, releases):
        predecessor = pointer_release.name

    made_release = False
    backup_path = None
    pointer_attempted = False
    record_attempted = False
    try:
        data.mkdir(mode=0o700, parents=True, exist_ok=True)
        releases.mkdir(mode=0o700, parents=True, exist_ok=True)
        # mkdir without exist_ok is the exclusive ownership claim for this call.
        try:
            target.mkdir()
        except FileExistsError as error:
            raise InstallError("release already exists: %s" % name) from error
        made_release = True
        files = {}
        sources = list(_package_files(package))
        for source_file, relative in sources:
            destination = target.joinpath(*PurePosixPath(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            expected = _digest(source_file)
            _copy_file(source_file, destination)
            copied = _digest(destination)
            if copied != expected:
                raise InstallError("copied file hash differs from publication: %s" % relative)
            files[relative] = expected

        publication = {
            "release": name,
            "version": "1",
            "state": "installed",
            "files": files,
            "predecessor": predecessor,
            "python_requires": ">=3.12",
        }
        errors = _validate_schema("publication-manifest", publication)
        if errors:
            raise InstallError("invalid publication manifest: %s" % "; ".join(errors))
        publication_bytes = _json_bytes(publication)
        (target / "publication.json").write_bytes(publication_bytes)
        publication_hash = hashlib.sha256(publication_bytes).hexdigest()

        # A backup is kept as a separate file so INSTALLATION.json can name it.
        if pointer is not None and pointer_existed:
            backup_path = data / (".pointer-backup-" + uuid.uuid4().hex)
            data.mkdir(parents=True, exist_ok=True)
            backup_path.write_bytes(pointer_before)

        record = {
            "release": name,
            "publication_sha256": publication_hash,
            "pointer_path": str(pointer) if pointer is not None else None,
            "pointer_backup": str(backup_path) if backup_path is not None else None,
        }
        installation_bytes = _json_bytes(record)
        _make_read_only(target)

        # Do not overwrite a pointer that changed while the release was built.
        if pointer is not None:
            now_exists, now_bytes = _read_pointer(pointer)
            if now_exists != pointer_existed or now_bytes != pointer_before:
                raise InstallError("pointer changed during installation")
        record_attempted = True
        _atomic_write(previous_record_path, installation_bytes)
        if pointer is not None:
            pointer_data = ("release = " + str(target.resolve()) + "\n").encode("utf-8")
            pointer_attempted = True
            _write_pointer(pointer, pointer_data)
        return {**record, "path": str(target), "verdict": "PASS"}
    except BaseException:
        if pointer_attempted and pointer is not None:
            try:
                current_exists, current_bytes = _read_pointer(pointer)
                if pointer_existed:
                    if not current_exists or current_bytes != pointer_before:
                        _write_pointer(pointer, pointer_before)
                elif current_exists:
                    pointer.unlink()
            except BaseException:
                pass
        if record_attempted:
            try:
                if previous_record_exists:
                    _atomic_write(previous_record_path, previous_record_bytes)
                else:
                    previous_record_path.unlink(missing_ok=True)
            except BaseException:
                pass
        if backup_path is not None:
            try:
                backup_path.unlink(missing_ok=True)
            except OSError:
                pass
        if made_release:
            try:
                _remove_release(target)
            except OSError:
                pass
        raise


def uninstall(data_root, *, pointer_path=None):
    """Restore the pointer saved by the latest installation, retaining releases."""
    data = Path(data_root).resolve()
    record_path = data / "INSTALLATION.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise InstallError("cannot read installation record") from error
    recorded_pointer = record.get("pointer_path")
    if recorded_pointer is not None and not isinstance(recorded_pointer, str):
        raise InstallError("invalid pointer path in installation record")
    if pointer_path is not None and (
        recorded_pointer is None
        or Path(pointer_path).absolute() != Path(recorded_pointer).absolute()
    ):
        raise InstallError("pointer_path does not match the installation record")
    selected = pointer_path if pointer_path is not None else recorded_pointer
    pointer = Path(selected).absolute() if selected is not None else None
    backup_value = record.get("pointer_backup")
    backup = None
    backup_bytes = None
    if backup_value is not None:
        if not isinstance(backup_value, str):
            raise InstallError("invalid pointer backup in installation record")
        backup = Path(backup_value)
        if not _inside(backup, data):
            raise InstallError("pointer backup is outside data_root")
        try:
            if not backup.is_file():
                raise InstallError("recorded pointer backup is missing")
            backup_bytes = backup.read_bytes()
        except OSError as error:
            raise InstallError("recorded pointer backup cannot be read") from error
    old_exists, old_bytes = _read_pointer(pointer)
    foreign_release = _named_release(old_bytes)
    if foreign_release is not None and not _inside(foreign_release, data):
        raise InstallError("pointer names a release owned by another data root")
    try:
        if pointer is not None:
            if backup is not None:
                _write_pointer(pointer, backup_bytes)
            else:
                pointer.unlink(missing_ok=True)
        marker = {
            "state": "uninstalled",
            "release": record.get("release"),
            "pointer_path": str(pointer) if pointer is not None else None,
        }
        _atomic_write(data / "UNINSTALLATION.json", _json_bytes(marker))
    except BaseException:
        if pointer is not None:
            try:
                current_exists, current_bytes = _read_pointer(pointer)
                if old_exists:
                    if not current_exists or current_bytes != old_bytes:
                        _write_pointer(pointer, old_bytes)
                elif current_exists:
                    pointer.unlink()
            except BaseException:
                pass
        raise
    return marker


def _fail(path, reason):
    return {"verdict": "FAIL", "path": str(path), "reason": reason}


def verify_installation(data_root):
    """Verify INSTALLATION.json, its publication, and every declared file."""
    data = Path(data_root).resolve()
    record_path = data / "INSTALLATION.json"
    try:
        record = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return _fail("INSTALLATION.json", "missing or invalid installation record")
    if not isinstance(record, dict):
        return _fail("INSTALLATION.json", "invalid installation record")
    try:
        release_dir, release_name = _record_release(record, data)
    except (ValueError, TypeError, OSError):
        return _fail("INSTALLATION.json", "invalid release path")
    publication_path = release_dir / "publication.json"
    try:
        if publication_path.is_symlink():
            return _fail("publication.json", "publication is a symbolic link")
        publication_bytes = publication_path.read_bytes()
        publication = json.loads(publication_bytes.decode("utf-8"))
    except (OSError, ValueError, UnicodeError):
        return _fail("publication.json", "missing or invalid publication")
    expected_publication = record.get("publication_sha256")
    actual_publication = hashlib.sha256(publication_bytes).hexdigest()
    if not isinstance(expected_publication, str) or actual_publication != expected_publication:
        return _fail("publication.json", "publication hash differs from INSTALLATION.json")
    errors = _validate_schema("publication-manifest", publication)
    if errors:
        return _fail("publication.json", "publication schema validation failed")
    if publication.get("state") != "installed":
        return _fail("publication.json", "publication state is not installed")
    if publication.get("release") != release_name:
        return _fail("publication.json", "release name differs")
    files = publication.get("files")
    if not isinstance(files, dict):
        return _fail("publication.json", "files is not a mapping")
    expected_names = set()
    for name, expected in sorted(files.items()):
        relative = _safe_manifest_path(name)
        if relative is None or name == "publication.json" or "__pycache__" in PurePosixPath(name).parts or name.endswith(".pyc"):
            return _fail(name if isinstance(name, str) else "publication.json", "unsafe publication path")
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            return _fail(name, "invalid sha256")
        expected_names.add(name)
        target = release_dir.joinpath(*relative.parts)
        component = release_dir
        linked = False
        for part in relative.parts:
            component = component / part
            if component.is_symlink():
                linked = True
                break
        try:
            if linked or not target.is_file():
                return _fail(name, "installed file is missing or not regular")
            actual = _digest(target)
        except OSError:
            return _fail(name, "installed file cannot be read")
        if actual != expected:
            return _fail(name, "installed file hash differs")
    for item in sorted(release_dir.rglob("*")):
        relative = item.relative_to(release_dir).as_posix()
        parts = PurePosixPath(relative).parts
        if item.is_symlink():
            return _fail(relative, "symbolic link in installed release")
        if item.is_dir() or relative == "publication.json" or "__pycache__" in parts:
            continue
        if relative not in expected_names:
            return _fail(relative, "unpublished installed file")
    return {"verdict": "PASS", "release": release_name}
