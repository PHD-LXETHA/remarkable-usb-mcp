"""Generated metadata/archives and mocked sockets only; no machine records."""

import hashlib
import io
import socket
import struct
import tarfile
from dataclasses import replace
from pathlib import Path

import pytest

from remarkable_usb_mcp.config import Config, UsbError
from remarkable_usb_mcp.device import UsbDevice, _bounded_command, document_id
from remarkable_usb_mcp.proxy import connect_usb_socket
from remarkable_usb_mcp.route import UsbRoute, parse_usb_route

DOC = "11111111-2222-4333-8444-555555555555"


def config(tmp_path):
    return Config(
        tmp_path / "synthetic-identity",
        tmp_path / "synthetic-hosts",
        tmp_path / "out",
        tmp_path / "local.toml",
        Path("ssh.exe"),
    )


def tar_payload(members):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name, data in members:
            item = tarfile.TarInfo(name)
            item.size = len(data)
            archive.addfile(item, io.BytesIO(data))
    return buffer.getvalue()


def test_active_usb_on_link_route_is_accepted():
    route = parse_usb_route(
        dict(
            interface_index=7,
            prefix_length=27,
            source_address="10.11.99.2",
            adapter_id="USB\\SYNTHETIC",
            status="Up",
        )
    )
    assert route == UsbRoute(7, "10.11.99.2")


@pytest.mark.parametrize(
    "change",
    [
        dict(adapter_id="ROOT\\NON_USB"),
        dict(status="Down"),
        dict(source_address="169.254.1.2", prefix_length=16),
        dict(interface_index=0),
        dict(interface_index=True),
        dict(source_address="10.11.99.1"),
        dict(prefix_length=33),
    ],
)
def test_non_usb_down_or_invalid_route_is_refused(change):
    data = dict(
        interface_index=7,
        prefix_length=27,
        source_address="10.11.99.2",
        adapter_id="USB\\SYNTHETIC",
        status="Up",
    )
    data.update(change)
    with pytest.raises(UsbError, match="no connection attempted"):
        parse_usb_route(data)


def test_non_usb_route_prevents_even_starting_ssh(tmp_path, monkeypatch):
    device = UsbDevice(config(tmp_path))
    monkeypatch.setattr(
        "remarkable_usb_mcp.device.check_usb_route",
        lambda: (_ for _ in ()).throw(UsbError("Non-USB route selected")),
    )
    monkeypatch.setattr(
        "remarkable_usb_mcp.device._bounded_command",
        lambda *a, **k: pytest.fail("SSH must not start"),
    )
    with pytest.raises(UsbError, match="Non-USB route selected"):
        device.check_connection()


def test_outgoing_socket_is_bound_to_usb_before_connect(monkeypatch):
    calls = []

    class FakeSocket:
        def setsockopt(self, *args):
            calls.append(("option", args))

        def bind(self, value):
            calls.append(("bind", value))

        def settimeout(self, value):
            calls.append(("timeout", value))

        def connect(self, value):
            calls.append(("connect", value))

        def close(self):
            calls.append(("close",))

    monkeypatch.setattr(socket, "socket", lambda *args: FakeSocket())
    connect_usb_socket(UsbRoute(257, "10.11.99.2"), 5)
    assert calls[0] == ("option", (socket.IPPROTO_IP, 31, struct.pack("!I", 257)))
    assert calls[1] == ("bind", ("10.11.99.2", 0))
    assert calls[3] == ("connect", ("10.11.99.1", 22))


@pytest.mark.parametrize(
    "identifier",
    [
        "../escape",
        "AAAAAAAA-2222-4333-8444-555555555555",
        "00000000-0000-0000-0000-000000000000",
        DOC + "; reboot",
        "-oProxyCommand=anything",
    ],
)
def test_invalid_uuid_never_reaches_remote_operations(tmp_path, monkeypatch, identifier):
    device = UsbDevice(config(tmp_path))
    monkeypatch.setattr(device, "_manifest", lambda *a: pytest.fail("no remote operation allowed"))
    with pytest.raises(UsbError):
        device.export_rmdoc(identifier)


@pytest.mark.parametrize("path", ["C:/bad%PATH%/local.toml", "C:/bad&command/local.toml"])
def test_proxy_shell_metacharacters_are_refused(tmp_path, path):
    device = UsbDevice(replace(config(tmp_path), config_file=Path(path)))
    with pytest.raises(UsbError, match="shell-safe"):
        device._ssh_args()


def fake_export(tmp_path, monkeypatch, *, members=None, changed=False):
    members = members or [(DOC + ".metadata", b"{}"), (DOC + ".content", b'{"pages":[]}')]
    expected = {name: hashlib.sha256(data).hexdigest() for name, data in members}
    device = UsbDevice(config(tmp_path))
    manifests = iter([expected, {} if changed else expected])
    monkeypatch.setattr(device, "_manifest", lambda _: next(manifests))
    monkeypatch.setattr(device, "_run", lambda *a, **k: tar_payload(members))
    return device


def test_export_preserves_all_bytes_and_creates_complete_archive(tmp_path, monkeypatch):
    import zipfile

    device = fake_export(tmp_path, monkeypatch)
    result = device.export_rmdoc(DOC)
    with zipfile.ZipFile(result["path"]) as archive:
        assert archive.read(DOC + ".metadata") == b"{}"
        assert archive.testzip() is None
    assert result["all_member_hashes_verified"] and not result["device_modified"]
    assert not list(device.config.export_dir.glob("*.tmp"))


def test_changing_device_version_publishes_nothing(tmp_path, monkeypatch):
    device = fake_export(tmp_path, monkeypatch, changed=True)
    with pytest.raises(UsbError, match="changed"):
        device.export_rmdoc(DOC)
    assert not device.config.export_dir.exists()


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        DOC + "/../escape",
        DOC + "/private.key",
        DOC + "/.env",
        "unrelated.pdf",
        DOC + "/x\\y",
    ],
)
def test_unsafe_or_credential_like_members_publish_nothing(tmp_path, monkeypatch, name):
    device = fake_export(tmp_path, monkeypatch, members=[(name, b"SYNTHETIC")])
    with pytest.raises(UsbError):
        device.export_rmdoc(DOC)
    assert not list(device.config.export_dir.iterdir())


def test_late_competing_file_is_preserved_and_temporary_removed(tmp_path, monkeypatch):
    device = fake_export(tmp_path, monkeypatch)

    def competitor(source, destination):
        destination.write_bytes(b"other application")
        raise FileExistsError()

    monkeypatch.setattr("remarkable_usb_mcp.device.os.link", competitor)
    with pytest.raises(FileExistsError):
        device.export_rmdoc(DOC)
    files = list(device.config.export_dir.iterdir())
    assert len(files) == 1 and files[0].read_bytes() == b"other application"


def test_subprocess_response_limit_stops_large_output():
    import sys

    with pytest.raises(UsbError, match="byte limit"):
        _bounded_command([sys.executable, "-c", "print('x'*100000)"], limit=64, timeout=5)


def test_subprocess_timeout_has_no_retry():
    import sys

    with pytest.raises(UsbError, match="timed out"):
        _bounded_command([sys.executable, "-c", "import time; time.sleep(10)"], limit=64, timeout=1)


def test_empty_document_uuid_is_refused():
    with pytest.raises(UsbError):
        document_id("")


def test_closed_process_input_never_consumes_protocol_stdin():
    import sys

    result = _bounded_command(
        [sys.executable, "-c", "import sys; print(len(sys.stdin.read()))"], limit=64, timeout=5
    )
    assert result.strip() == b"0"


def test_windows_mcp_minimal_environment_restores_system_shell(monkeypatch):
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows OpenSSH environment integration")
    monkeypatch.delenv("COMSPEC", raising=False)
    monkeypatch.delenv("WINDIR", raising=False)
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    result = _bounded_command(
        [
            sys.executable,
            "-c",
            "import os; print(os.path.isfile(os.environ['COMSPEC']) "
            "and os.path.isdir(os.environ['WINDIR']))",
        ],
        limit=64,
        timeout=5,
    )
    assert result.strip() == b"True"


def test_windows_openssh_starts_without_host_programdata(monkeypatch):
    import os
    import sys

    if sys.platform != "win32":
        pytest.skip("Windows OpenSSH startup integration")
    ssh = Path(os.environ["SystemRoot"]) / "System32/OpenSSH/ssh.exe"
    if not ssh.is_file():
        pytest.skip("Windows OpenSSH is not installed")
    monkeypatch.delenv("PROGRAMDATA", raising=False)
    # -G evaluates configuration without any network connection or key loading.
    result = _bounded_command(
        [str(ssh), "-G", "-F", "NUL", "root@10.11.99.1"], limit=16384, timeout=5
    )
    assert b"hostname 10.11.99.1" in result
