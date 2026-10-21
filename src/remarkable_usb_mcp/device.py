"""Fixed read-only operations; no arbitrary remote command tool is exposed."""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import threading
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from .config import TARGET, Config, UsbError
from .route import check_usb_route

LIBRARY = "/home/root/.local/share/remarkable/xochitl"
CANONICAL_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z")


def document_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not CANONICAL_UUID.fullmatch(value)
        or not uuid.UUID(value).int
    ):
        raise UsbError("Expected an explicit, nonzero canonical document UUID")
    return value


def _shell_path(value: str) -> str:
    # OpenSSH ProxyCommand uses a shell on Windows. All substituted paths are local
    # operator configuration, never tool arguments; refuse shell metacharacters.
    if any(c in value for c in '\r\n"%&|<>^!'):
        raise UsbError("Move the executable/configuration to a shell-safe local path")
    return f'"{value}"'


def _bounded_command(args: list[str], *, limit: int, timeout: int) -> bytes:
    output, errors = bytearray(), bytearray()
    overflow = threading.Event()
    expired = threading.Event()
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    environment = dict(os.environ)
    if sys.platform == "win32":
        # MCP hosts omit standard Windows environment entries. In particular,
        # OpenSSH exits before logging when PROGRAMDATA is missing, even with -F.
        # Resolve the platform folder instead of assuming its drive/location.
        if not environment.get("PROGRAMDATA"):
            import ctypes

            folder = ctypes.create_unicode_buffer(32768)
            if ctypes.windll.shell32.SHGetFolderPathW(None, 0x23, None, 0, folder) != 0:
                raise UsbError("Cannot resolve the Windows common application-data directory")
            environment["PROGRAMDATA"] = folder.value
        system_root = Path(
            environment.get("SystemRoot", environment.get("SYSTEMROOT", "C:/Windows"))
        )
        environment["COMSPEC"] = str(system_root / "System32/cmd.exe")
        environment["WINDIR"] = str(system_root)
    with subprocess.Popen(
        args,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        creationflags=flags,
        env=environment,
    ) as process:
        # Never let SSH consume the MCP protocol input or request interactive input.
        process.stdin.close()

        def collect(stream, buffer, maximum):
            while chunk := stream.read(65536):
                available = maximum - len(buffer)
                buffer.extend(chunk[: max(0, available)])
                if len(chunk) > available:
                    overflow.set()
                    process.kill()
                    return

        def expire():
            expired.set()
            if process.poll() is None:
                process.kill()

        readers = [
            threading.Thread(target=collect, args=(process.stdout, output, limit), daemon=True),
            threading.Thread(target=collect, args=(process.stderr, errors, 16384), daemon=True),
        ]
        timer = threading.Timer(timeout, expire)
        timer.daemon = True
        try:
            timer.start()
            for reader in readers:
                reader.start()
            process.wait()
            for reader in readers:
                reader.join(3)
            if any(reader.is_alive() for reader in readers):
                raise UsbError("SSH output did not close within the operation limit")
        finally:
            timer.cancel()
        if expired.is_set():
            raise UsbError("USB operation timed out; no retry was made")
        if overflow.is_set():
            raise UsbError("USB response exceeded its byte limit")
        if process.returncode:
            detail = errors.decode("utf-8", "replace").strip()[-1024:]
            raise UsbError(detail or f"USB SSH operation failed (exit {process.returncode})")
    return bytes(output)


def safe_member(name: str, identifier: str) -> str:
    parts = name.removeprefix("./").split("/")
    if any(
        not part
        or part in {".", ".."}
        or part.endswith((".", " "))
        or ":" in part
        or "\\" in part
        or any(ord(c) < 32 for c in part)
        for part in parts
    ):
        raise UsbError("Unsafe archive member")
    value = "/".join(parts)
    if not (
        value in {identifier + suffix for suffix in (".metadata", ".content", ".pdf", ".epub")}
        or value.startswith(identifier + "/")
    ):
        raise UsbError("Unexpected archive member")
    if any(
        re.fullmatch(r"(?i)(?:\.env.*|credentials.*|id_rsa|id_ed25519)", part)
        or re.search(r"(?i)\.(?:key|pem|p12|pfx)$", part)
        for part in parts
    ):
        raise UsbError("Credential-like member refused")
    return value


class UsbDevice:
    def __init__(self, config: Config):
        self.config = config
        self._lock = threading.Lock()

    def _ssh_args(self) -> list[str]:
        proxy = (
            f"{_shell_path(sys.executable)} -m remarkable_usb_mcp.proxy --config "
            f"{_shell_path(str(self.config.config_file))}"
        )
        return [
            str(self.config.ssh_executable),
            "-F",
            "NUL",
            "-T",
            "-o",
            f"ProxyCommand={proxy}",
            "-o",
            "ProxyJump=none",
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=yes",
            "-o",
            "GlobalKnownHostsFile=NUL",
            "-o",
            f"UserKnownHostsFile={self.config.known_hosts_file}",
            "-o",
            "KnownHostsCommand=none",
            "-o",
            "PasswordAuthentication=no",
            "-o",
            "KbdInteractiveAuthentication=no",
            "-o",
            "ForwardAgent=no",
            "-o",
            "ClearAllForwardings=yes",
            "-o",
            "PermitLocalCommand=no",
            "-o",
            f"ConnectTimeout={self.config.connect_timeout}",
            "-o",
            "ConnectionAttempts=1",
            "-o",
            "ServerAliveInterval=5",
            "-o",
            "ServerAliveCountMax=2",
            "-i",
            str(self.config.identity_file),
            f"root@{TARGET}",
        ]

    def _run(self, command: str, *, limit: int = 16 * 1024 * 1024) -> bytes:
        check_usb_route()
        return _bounded_command(
            [*self._ssh_args(), command], limit=limit, timeout=self.config.operation_timeout
        )

    def check_connection(self) -> dict:
        with self._lock:
            route = check_usb_route()
            output = self._run('printf "%s\\n" "$SSH_CONNECTION"; uname -m', limit=4096)
            lines = output.decode("utf-8").splitlines()
            return {
                "connected": True,
                "transport": "USB",
                "interface_index": route.interface_index,
                "source_address": route.source_address,
                "target": TARGET,
                "architecture": lines[-1] if lines else "unknown",
                "network_configuration_changed": False,
            }

    def list_documents(self, *, include_trash: bool = False) -> list[dict]:
        with self._lock:
            raw = self._run(f"set -eu; cd {LIBRARY}; tar -cf - ./*.metadata")
        entries = []
        with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
            for number, member in enumerate(archive):
                if number >= 4096:
                    raise UsbError("Document metadata member limit exceeded")
                name = PurePosixPath(member.name).name
                if not member.isfile() or not name.endswith(".metadata"):
                    raise UsbError("Unexpected document metadata entry")
                identifier = document_id(name.removesuffix(".metadata"))
                if member.size > 1024 * 1024:
                    raise UsbError("Document metadata size limit exceeded")
                stream = archive.extractfile(member)
                if stream is None:
                    raise UsbError("Incomplete document metadata")
                metadata = json.loads(stream.read())
                if not isinstance(metadata, dict):
                    raise UsbError("Invalid document metadata")
                if not include_trash and (
                    metadata.get("parent") == "trash" or metadata.get("deleted")
                ):
                    continue
                entries.append(
                    {
                        "uuid": identifier,
                        "name": str(metadata.get("visibleName", "")),
                        "parent": str(metadata.get("parent", "")),
                        "kind": str(metadata.get("type", "")),
                    }
                )
        return sorted(entries, key=lambda entry: (entry["kind"], entry["name"].casefold()))

    def _manifest(self, identifier: str) -> dict[str, str]:
        output = self._run(
            f"set -eu; cd {LIBRARY}; id={identifier}; "
            'test -f "$id.metadata"; test -f "$id.content"; '
            "for suffix in metadata content pdf epub; do "
            'test ! -f "$id.$suffix" || sha256sum "$id.$suffix"; done; '
            r'test ! -d "$id" || find "$id" -type f -exec sha256sum {} \;'
        )
        result = {}
        for line in output.decode("utf-8").splitlines():
            digest, name = line.split(None, 1)
            name = safe_member(name.strip(), identifier)
            if not re.fullmatch("[0-9a-f]{64}", digest) or name in result:
                raise UsbError("Invalid or repeated manifest member")
            result[name] = digest
        if not 2 <= len(result) <= 4096:
            raise UsbError("Unsupported bundle member count")
        return result

    def export_rmdoc(self, identifier: str) -> dict:
        identifier = document_id(identifier)
        with self._lock:
            before = self._manifest(identifier)
            raw = self._run(
                f"set -eu; cd {LIBRARY}; id={identifier}; "
                'set -- "$id.metadata" "$id.content"; '
                'test ! -f "$id.pdf" || set -- "$@" "$id.pdf"; '
                'test ! -f "$id.epub" || set -- "$@" "$id.epub"; '
                'test ! -d "$id" || set -- "$@" "$id"; tar -cf - "$@"',
                limit=self.config.max_export_bytes,
            )
            after = self._manifest(identifier)
            if before != after:
                raise UsbError("Document changed during export; no file published")
            self.config.export_dir.mkdir(parents=True, exist_ok=True)
            destination = self.config.export_dir / f"{identifier}-{uuid.uuid4().hex}.rmdoc"
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(
                    dir=self.config.export_dir, prefix=".rmdoc-", suffix=".tmp", delete=False
                ) as output:
                    temporary = Path(output.name)
                    observed = {}
                    expanded = 0
                    with (
                        tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive,
                        zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as target,
                    ):
                        seen = set()
                        for number, member in enumerate(archive):
                            if number >= 4096:
                                raise UsbError("Bundle member limit exceeded")
                            if member.isdir():
                                if member.name.rstrip("/") != identifier:
                                    safe_member(member.name.rstrip("/"), identifier)
                                continue
                            if not member.isfile():
                                raise UsbError("Linked or special archive member refused")
                            name = safe_member(member.name, identifier)
                            if name.casefold() in seen:
                                raise UsbError("Duplicate or case-aliased bundle member")
                            seen.add(name.casefold())
                            expanded += member.size
                            if member.size < 0 or expanded > self.config.max_export_bytes:
                                raise UsbError("Bundle expansion limit exceeded")
                            stream = archive.extractfile(member)
                            if stream is None:
                                raise UsbError("Incomplete bundle member")
                            data = stream.read(member.size + 1)
                            if len(data) != member.size:
                                raise UsbError("Incomplete bundle member")
                            observed[name] = hashlib.sha256(data).hexdigest()
                            target.writestr(name, data)
                    if observed != before:
                        raise UsbError(
                            "Bundle member hashes differ from the stable device manifest"
                        )
                with zipfile.ZipFile(temporary) as verification:
                    if verification.testzip() is not None:
                        raise UsbError("Local archive integrity verification failed")
                os.link(temporary, destination)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
        digest = hashlib.sha256(destination.read_bytes()).hexdigest()
        return {
            "uuid": identifier,
            "path": str(destination),
            "sha256": digest,
            "bytes": destination.stat().st_size,
            "members": len(before),
            "all_member_hashes_verified": True,
            "device_modified": False,
        }
