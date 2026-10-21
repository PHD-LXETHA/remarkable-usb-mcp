# reMarkable USB MCP

An independent Python module and standard MCP stdio server for read-only reMarkable access
on **Windows10/11**. Reading applications can use the Python API directly; MCP hosts can
use the same connection logic through three tools.

## What it does

| Tool | Behaviour |
| --- | --- |
| `check_connection` | Check the USB route and authenticated SSH connection. |
| `list_documents` | Return paginated document/folder metadata. |
| `export_rmdoc` | Export one explicit document UUID to a new local RMDOC. |

The device library is read-only. The module does not change network settings, restart
device services, upload documents, remove documents or execute model-supplied commands.
Exports preserve the original members; this module does **not** decode handwriting,
reconstruct annotated PDFs or certify rendering fidelity.

## USB-only transport

Before every SSH operation, the module verifies that Windows routes `10.11.99.1` through
an active adapter whose Plug-and-Play identity is USB, with an on-link source address.
If no verified USB route is available, it stops **before** opening SSH.

The OpenSSH bridge checks the route again, binds the source address and sets Windows
`IP_UNICAST_IF` to the selected USB interface. All device traffic stays on that interface,
with no alternate transport or automatic retry. Existing SSH configuration is bypassed;
host-key validation is strict and uses only the operator's preverified known-hosts file. Private key bytes
are read by OpenSSH, not by the Python module.

The standard device target is `10.11.99.1:22`. This release intentionally supports Windows
only; other operating systems stop rather than silently weakening interface binding.
It does not install software on the tablet. SSH/developer access and USB networking must
already be configured on the device.

## Install and configure

Requires Python3.11+, Windows PowerShell and Windows OpenSSH Client. The Python package
uses the official MCP Python SDK2.x. Linux/macOS are suitable for offline tests only.

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install .
Copy-Item config.example.toml config.local.toml
```

Edit `config.local.toml` with your existing identity file, an **already verified**
known-hosts file, and your export directory. The module never auto-enrols a host key or
prompts for a password. Confirm the device fingerprint through a trusted channel first;
an unverified key scan is not sufficient. Unlock passphrase-protected keys in your SSH
agent before starting the server. Run as your normal Windows user, with access to these
files and the USB network; administrator rights are not required.

If USB is detected but has only an automatic `169.254.*` address, reconnect the USB data
cable while the tablet is awake and check whether DHCP recovers. This server never
changes network configuration. If the route remains unsuitable, it stops.

### MCP host configuration

Add an entry to your host's existing MCP server configuration, substituting your paths:

```json
{
  "mcpServers": {
    "remarkable-usb": {
      "command": "C:/path/to/venv/Scripts/remarkable-usb-mcp.exe",
      "args": ["--config", "C:/path/to/config.local.toml"]
    }
  }
}
```

The server uses stdio and does not listen on an HTTP port. MCP protocol data is the only
stdout output. Errors go to the protocol result or stderr. The local export tool is
marked as writing a file, while device checks/listing are marked read-only.

### Python integration

```python
from remarkable_usb_mcp.config import Config
from remarkable_usb_mcp.device import UsbDevice

device = UsbDevice(Config.load("C:/path/to/config.local.toml"))
status = device.check_connection()
documents = device.list_documents()
# Select an explicit UUID from documents before calling device.export_rmdoc(uuid).
```

Export checks each member's SHA256 against manifests read before and after transfer.
Limits apply to process duration, response bytes, archive members and expanded bytes.
Unsafe paths, links, unexpected members and inconsistent snapshots are refused. A ZIP
is closed and checked before atomic publication using a no-overwrite hard link; the
export directory therefore needs a filesystem supporting hard links, such as NTFS.
Failures remove only the operation's temporary file.

## Development

```powershell
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\ruff check .
.venv\Scripts\python -m build
```

Tests use generated metadata, generated archives and mocked USB sockets. They do not
need a tablet, private key, account, real documents or machine-specific records.

This repository contains generic source, examples and synthetic tests only. Keep real
configuration, host identities, keys, documents, logs, captures and acceptance records
outside the repository. No document contents are sent to a cloud service by this module.

## References

- [Model Context Protocol: build a server](https://modelcontextprotocol.io/docs/develop/build-server)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [Microsoft: IPv4 socket options and IP_UNICAST_IF](https://learn.microsoft.com/en-us/windows/win32/winsock/ipproto-ip-socket-options)

Independent project; not an official reMarkable product. MIT licensed.

## 中文说明

这是独立的只读 USB 连接模块，同时提供 Python 接口与标准 MCP 接口，供阅读器等应用复用。
当前支持 Windows10/11；每次访问都会检查 USB 路由，并在套接字层绑定 USB 网卡。
仅使用已核实的 USB 直连；连接条件不满足就停止，不改网络设置、不自动重试。

将示例配置中的三个路径改成自己的密钥、已核实主机身份文件和导出目录，再配置 MCP 客户端。
已有设备 SSH 权限是前提，服务器不自动添加主机信任，也不上传、删除或改写设备文档。
导出只新增本地文件，不覆盖已有文件，并核对全部成员的导出前后摘要。

本仓库不附带任何本机记录、真实文档、密钥、主机身份或联调日志。
笔画解析和批注 PDF 重建属于上层应用，不包含在本连接模块中。
