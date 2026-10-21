"""Discover USB addresses independently of other network connections."""

from __future__ import annotations

import base64
import ipaddress
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .config import TARGET, UsbError

ROUTE_SCRIPT = r"""
$ErrorActionPreference='Stop'
$adapters=@(Get-NetAdapter | Where-Object {
  $_.Status -eq 'Up' -and $_.PnPDeviceID -like 'USB\*'
})
$candidates=@(foreach($adapter in $adapters){
  $parameters=@{
    InterfaceIndex=$adapter.InterfaceIndex
    AddressFamily='IPv4'
    ErrorAction='SilentlyContinue'
  }
  $addresses=@(Get-NetIPAddress @parameters |
    Where-Object { $_.AddressState -eq 'Preferred' -and -not $_.SkipAsSource })
  foreach($ip in $addresses){
    [pscustomobject]@{
      interface_index=$adapter.InterfaceIndex
      source_address=$ip.IPAddress
      prefix_length=$ip.PrefixLength
      adapter_id=$adapter.PnPDeviceID
      status=[string]$adapter.Status
    }
  }
})
ConvertTo-Json -InputObject $candidates -Compress
"""


@dataclass(frozen=True)
class UsbRoute:
    interface_index: int
    source_address: str


def parse_usb_route(data: dict) -> UsbRoute:
    try:
        index = data["interface_index"]
        prefix = data["prefix_length"]
        if type(index) is not int or not 0 < index < 2**24:
            raise ValueError("Invalid interface index")
        if type(prefix) is not int or not 1 <= prefix <= 32:
            raise ValueError("Invalid prefix")
        if data["status"] != "Up" or not data["adapter_id"].upper().startswith("USB\\"):
            raise ValueError("Selected route is not an active USB adapter")
        address = ipaddress.IPv4Address(data["source_address"])
        network = ipaddress.IPv4Network((str(address), prefix), strict=False)
        if ipaddress.IPv4Address(TARGET) not in network or str(address) == TARGET:
            raise ValueError("USB has no on-link device address")
        return UsbRoute(index, str(address))
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise UsbError("USB-only route unavailable; no connection attempted") from error


def select_usb_route(candidates: list[dict]) -> UsbRoute:
    if not isinstance(candidates, list) or len(candidates) > 256:
        raise UsbError("Invalid USB address inventory; no connection attempted")
    usable = set()
    for candidate in candidates:
        try:
            usable.add(parse_usb_route(candidate))
        except UsbError:
            continue
    if len(usable) != 1:
        raise UsbError("Expected one usable USB device address; no connection attempted")
    return usable.pop()


def check_usb_route() -> UsbRoute:
    if sys.platform != "win32":
        raise UsbError("This release supports the Windows USB route guard only")
    powershell = Path(os.environ.get("SystemRoot", "C:/Windows")) / (
        "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    encoded = base64.b64encode(ROUTE_SCRIPT.encode("utf-16-le")).decode("ascii")
    try:
        result = subprocess.run(
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-EncodedCommand",
                encoded,
            ],
            capture_output=True,
            timeout=15,
            check=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        return select_usb_route(json.loads(result.stdout.decode("utf-8-sig")))
    except (subprocess.SubprocessError, OSError, ValueError) as error:
        raise UsbError(
            "Cannot establish a verified USB-only route; no connection attempted"
        ) from error
