"""Explicit local configuration. Private key bytes are handled only by OpenSSH."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

TARGET = "10.11.99.1"


class UsbError(RuntimeError):
    """An operation stopped without changing the device or network configuration."""


@dataclass(frozen=True)
class Config:
    identity_file: Path
    known_hosts_file: Path
    export_dir: Path
    config_file: Path
    ssh_executable: Path
    connect_timeout: int = 5
    operation_timeout: int = 60
    max_export_bytes: int = 256 * 1024 * 1024

    @classmethod
    def load(cls, filename: str | Path) -> Config:
        path = Path(filename).expanduser().resolve(strict=True)
        with path.open("rb") as stream:
            data = tomllib.load(stream)
        allowed = {
            "identity_file",
            "known_hosts_file",
            "export_dir",
            "ssh_executable",
            "connect_timeout",
            "operation_timeout",
            "max_export_bytes",
        }
        if set(data) - allowed:
            raise UsbError("Unsupported configuration field")

        def local(name: str) -> Path:
            text = data.get(name)
            if not isinstance(text, str) or not text.strip():
                raise UsbError(f"Configure {name} explicitly")
            result = Path(text).expanduser()
            return (
                (path.parent / result).resolve() if not result.is_absolute() else result.resolve()
            )

        system_root = Path(os.environ.get("SystemRoot", "C:/Windows"))
        ssh = (
            local("ssh_executable")
            if "ssh_executable" in data
            else (system_root / "System32/OpenSSH/ssh.exe")
        )
        config = cls(
            local("identity_file"),
            local("known_hosts_file"),
            local("export_dir"),
            path,
            ssh,
            **{
                key: data[key]
                for key in ("connect_timeout", "operation_timeout", "max_export_bytes")
                if key in data
            },
        )
        for field, minimum, maximum in (
            ("connect_timeout", 1, 15),
            ("operation_timeout", 5, 120),
            ("max_export_bytes", 1024, 512 * 1024 * 1024),
        ):
            value = getattr(config, field)
            if type(value) is not int or not minimum <= value <= maximum:
                raise UsbError(f"Invalid {field}")
        if not config.identity_file.is_file() or not config.known_hosts_file.is_file():
            raise UsbError("Configured identity and known-hosts files must already exist")
        return config
