#!/usr/bin/env python3
"""Echo fixtures and real UDP/TCP/raw-packet probes; isolated containers only."""
import argparse
import json
import os
from pathlib import Path
import socket
import ssl
import struct
import threading
import time


def checksum(data):
    if len(data) % 2:
        data += b"\0"
    total = sum(struct.unpack("!%dH" % (len(data) // 2), data))
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def serve(address4, address6):
    def udp(server):
        while True:
            data, peer = server.recvfrom(65535)
            server.sendto(data, peer)

    def tcp(server):
        while True:
            conn, _ = server.accept()
            with conn:
                conn.settimeout(1)
                try:
                    conn.sendall(conn.recv(65535))
                except OSError:
                    pass

    for family, address in [(socket.AF_INET, address4), (socket.AF_INET6, address6)]:
        for protocol, handler in [(socket.SOCK_DGRAM, udp), (socket.SOCK_STREAM, tcp)]:
            for port in [53, 123, 443, 3478, 5349, 40000]:
                if protocol == socket.SOCK_STREAM and port == 443:
                    continue  # OpenWrt's real uhttpd HTTPS endpoint is used here.
                server = socket.socket(family, protocol)
                server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                if family == socket.AF_INET6:
                    server.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
                server.bind((address, port))
                if protocol == socket.SOCK_STREAM:
                    server.listen(16)
                threading.Thread(target=handler, args=(server,), daemon=True).start()
    print("ready", flush=True)
    threading.Event().wait()


def payload(kind):
    transaction = os.urandom(12)
    if kind == "stun":
        return struct.pack("!HHI", 1, 0, 0x2112A442) + transaction
    if kind == "not-stun":
        return struct.pack("!HHI", 0x8001, 0, 0x2112A442) + transaction
    if kind == "short":
        return struct.pack("!HHI", 1, 0, 0x2112A442)
    return b"ordinary-packet-" + transaction


def raw_send(receiver, options, data):
    version = options.get("family", 4)
    destination = options["address"]
    source = receiver.getsockname()[0]
    source_port = receiver.getsockname()[1]
    udp = struct.pack("!HHHH", source_port, options["port"], len(data) + 8, 0) + data
    family = socket.AF_INET if version == 4 else socket.AF_INET6
    src = socket.inet_pton(family, source)
    dst = socket.inet_pton(family, destination)
    pseudo = (src + dst + struct.pack("!BBH", 0, 17, len(udp))) if version == 4 else (
        src + dst + struct.pack("!I3xB", len(udp), 17))
    udp = udp[:6] + struct.pack("!H", checksum(pseudo + udp) or 0xFFFF) + udp[8:]
    if version == 4:
        # Real IPv4 fragments: first fragment deliberately lacks a full STUN header.
        ident = int.from_bytes(os.urandom(2), "big")
        with socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_RAW) as wire:
            for offset, part in [(0, udp[:16]), (16, udp[16:])]:
                flags = (0x2000 if offset == 0 else 0) | (offset // 8)
                ip = struct.pack("!BBHHHBBH4s4s", 0x45, 0, 20 + len(part), ident, flags, 64, 17, 0, src, dst)
                ip = ip[:10] + struct.pack("!H", checksum(ip)) + ip[12:]
                wire.sendto(ip + part, (destination, 0))
    else:
        source_mac = bytes.fromhex(Path("/sys/class/net/eth0/address").read_text().strip().replace(":", ""))
        destination_mac = bytes.fromhex(options["mac"].replace(":", ""))
        if options["raw"] == "fragment":
            ident = int.from_bytes(os.urandom(4), "big")
            packets = []
            for offset, part in [(0, udp[:16]), (16, udp[16:])]:
                fragment = struct.pack("!BBHI", 17, 0, offset | (1 if offset == 0 else 0), ident)
                packets.append((44, fragment + part))
        else:
            # IPv6 Hop-by-Hop extension followed by UDP, exercising l4proto lookup.
            packets = [(0, bytes([17, 0, 1, 4, 0, 0, 0, 0]) + udp)]
        with socket.socket(socket.AF_PACKET, socket.SOCK_RAW) as wire:
            wire.bind(("eth0", 0))
            for next_header, packet in packets:
                ip = struct.pack("!IHBB16s16s", 6 << 28, len(packet), next_header, 64, src, dst)
                wire.send(destination_mac + source_mac + b"\x86\xdd" + ip + packet)


def probe(options):
    family = socket.AF_INET if options.get("family", 4) == 4 else socket.AF_INET6
    kind = socket.SOCK_STREAM if options.get("protocol") == "tcp" else socket.SOCK_DGRAM
    data = payload(options.get("payload", "normal"))
    with socket.socket(family, kind) as client:
        client.settimeout(0.45)
        try:
            if kind == socket.SOCK_STREAM and options["port"] == 443:
                context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                context.check_hostname = False
                context.verify_mode = ssl.CERT_NONE  # Disposable local fixture certificate.
                with context.wrap_socket(client, server_hostname="openwrt-fixture") as tls:
                    tls.connect((options["address"], options["port"]))
                    tls.sendall(b"GET / HTTP/1.0\r\nHost: fixture\r\n\r\n")
                    return tls.recv(65535).startswith(b"HTTP/")
            client.connect((options["address"], options["port"]))
            if options.get("raw"):
                raw_send(client, options, data)
            else:
                client.sendall(data)
            return client.recv(65535) == data
        except (TimeoutError, ConnectionRefusedError, ConnectionResetError):
            return False


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["server", "probe"])
    parser.add_argument("arguments", nargs="+")
    args = parser.parse_args()
    if args.action == "server":
        serve(*args.arguments)
    else:
        print(json.dumps({"delivered": probe(json.loads(args.arguments[0]))}))
