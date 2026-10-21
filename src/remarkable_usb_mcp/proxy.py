"""OpenSSH stdio bridge with the outgoing Windows socket pinned to USB."""

from __future__ import annotations

import argparse
import socket
import struct
import sys
import threading

from .config import TARGET, Config, UsbError
from .route import UsbRoute, check_usb_route


def connect_usb_socket(route: UsbRoute, timeout: int) -> socket.socket:
    connection = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        # Windows IP_UNICAST_IF: IF_INDEX must be in network byte order.
        # Unlike a source-address-only bind, this fixes the actual output interface.
        connection.setsockopt(socket.IPPROTO_IP, 31, struct.pack("!I", route.interface_index))
        connection.bind((route.source_address, 0))
        connection.settimeout(timeout)
        connection.connect((TARGET, 22))
        connection.settimeout(None)
        return connection
    except BaseException:
        connection.close()
        raise


def relay(connection: socket.socket) -> None:
    def upload():
        try:
            while data := sys.stdin.buffer.read1(65536):
                connection.sendall(data)
            connection.shutdown(socket.SHUT_WR)
        except OSError:
            connection.close()

    threading.Thread(target=upload, daemon=True).start()
    try:
        while data := connection.recv(65536):
            sys.stdout.buffer.write(data)
            sys.stdout.buffer.flush()
    finally:
        connection.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    options = parser.parse_args()
    try:
        config = Config.load(options.config)
        route = check_usb_route()
        relay(connect_usb_socket(route, config.connect_timeout))
    except (UsbError, OSError) as error:
        print(f"USB bridge stopped: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
