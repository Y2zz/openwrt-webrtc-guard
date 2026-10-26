#!/usr/bin/env python3
"""Create disposable Docker fixtures and run the OpenWrt traffic suite."""
import argparse
import hashlib
import ipaddress
import json
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
VERSION = "24.10.5"


def main(report):
    report = report.resolve()
    report.parent.mkdir(parents=True, exist_ok=True)
    transcript = report.parent / "integration.log"
    log = transcript.open("w")
    prefix = "wg-test-" + uuid.uuid4().hex[:8]
    router, client, echo = [prefix + suffix for suffix in ("-router", "-client", "-echo")]
    router_image, client_image = prefix + "-openwrt:local", prefix + "-client:local"
    containers = []
    images = []
    network_created = False

    def run(*command, check=True, timeout=240):
        print("+", " ".join(command), flush=True)
        log.write("+ " + " ".join(command) + "\n")
        log.flush()
        result = subprocess.run(command, text=True, capture_output=True, timeout=timeout)
        output = result.stdout + result.stderr
        log.write(output)
        log.flush()
        if output:
            print(output.rstrip(), flush=True)
        if check and result.returncode:
            raise RuntimeError(f"Command failed ({result.returncode}): {' '.join(command)}")
        return result

    def docker(*command, **kwargs):
        return run("docker", *command, **kwargs)

    def wait_for(*command):
        for _ in range(60):
            if docker("exec", router, *command, check=False, timeout=15).returncode == 0:
                return
            time.sleep(1)
        raise RuntimeError("OpenWrt fixture did not become ready")

    try:
        architecture = docker("info", "--format", "{{.Architecture}}").stdout.strip()
        if architecture in ("aarch64", "arm64"):
            target, filename = "armsr/armv8", f"openwrt-{VERSION}-armsr-armv8-rootfs.tar.gz"
        elif architecture in ("x86_64", "amd64"):
            target, filename = "x86/64", f"openwrt-{VERSION}-x86-64-rootfs.tar.gz"
        else:
            raise ValueError(f"Unsupported Docker architecture: {architecture}")
        base_url = f"https://downloads.openwrt.org/releases/{VERSION}/targets/{target}"
        with tempfile.TemporaryDirectory(prefix="webrtc-guard-") as scratch:
            scratch = Path(scratch)
            rootfs, sums = scratch / filename, scratch / "sha256sums"
            for url, destination in [(f"{base_url}/{filename}", rootfs), (f"{base_url}/sha256sums", sums)]:
                run("curl", "--fail", "--location", "--silent", "--show-error", "--retry", "3", "--max-time", "120", url, "-o", str(destination))
            expected = next(line.split()[0] for line in sums.read_text().splitlines() if line.split()[-1].lstrip("*") == filename)
            actual = hashlib.sha256(rootfs.read_bytes()).hexdigest()
            if actual != expected:
                raise ValueError("Official OpenWrt rootfs SHA256 mismatch")
            print("Verified official rootfs SHA256:", actual, flush=True)
            docker("import", str(rootfs), router_image)
            images.append(router_image)

        docker("build", "-f", str(ROOT / "tests/Dockerfile"), "-t", client_image, str(ROOT / "tests"))
        images.append(client_image)
        docker("network", "create", prefix)
        network_created = True
        mount = str(ROOT) + ":/src:ro"
        docker("run", "-d", "--name", router, "--network", prefix,
               "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW",
               "-v", mount, router_image, "/sbin/procd")
        containers.append(router)
        info = json.loads(docker("inspect", router).stdout)[0]["NetworkSettings"]["Networks"][prefix]
        address, gateway, length = info["IPAddress"], info["Gateway"], info["IPPrefixLen"]
        netmask = str(ipaddress.ip_network(f"{address}/{length}", strict=False).netmask)
        docker("exec", router, "sh", "-c", "mkdir -p /var/run/ubus; /sbin/ubusd -s /var/run/ubus/ubus.sock >/tmp/ubusd.log 2>&1 &")
        wait_for("ip", "link", "show", "dev", "br-lan")
        for key, value in (("ipaddr", address), ("netmask", netmask), ("gateway", gateway), ("dns", "127.0.0.11")):
            docker("exec", router, "uci", "set", f"network.lan.{key}={value}")
        docker("exec", router, "uci", "commit", "network")
        docker("exec", router, "/etc/init.d/network", "reload")
        wait_for("sh", "-c", f"ip -4 addr show dev br-lan | grep -F 'inet {address}/'")
        docker("exec", router, "opkg", "update")
        packages = list((ROOT / "dist").glob("*.ipk"))
        if len(packages) != 1:
            raise ValueError("Build exactly one IPK in dist before running integration tests")
        docker("exec", router, "opkg", "install", "/src/dist/" + packages[0].name)
        docker("exec", router, "ip", "-6", "addr", "add", "fd42:7767::1/64", "dev", "br-lan")
        docker("exec", router, "/etc/init.d/dnsmasq", "stop")
        wait_for("sh", "-c", "netstat -ln | grep -q ':443 '")

        docker("run", "-d", "--name", client, "--cap-add", "NET_ADMIN", "--cap-add", "NET_RAW",
               "--network", f"name={prefix},driver-opt=com.docker.network.endpoint.sysctls=net.ipv6.conf.IFNAME.disable_ipv6=0",
               "-v", mount, client_image, "sleep", "infinity")
        containers.append(client)
        docker("exec", client, "ip", "-6", "addr", "add", "fd42:7767::2/64", "dev", "eth0")
        time.sleep(2)  # Allow IPv6 duplicate address detection to complete.
        docker("exec", client, "ping", "-6", "-c", "1", "-W", "3", "fd42:7767::1")
        docker("run", "-d", "--name", echo, "--network", "container:" + router,
               "-v", mount, client_image, "python3", "/src/tests/traffic.py", "server", address, "fd42:7767::1")
        containers.append(echo)
        for _ in range(20):
            if "ready" in docker("logs", echo).stdout:
                break
            time.sleep(0.5)
        else:
            raise RuntimeError("Traffic echo fixture failed to start")
        mac = docker("exec", router, "cat", "/sys/class/net/br-lan/address").stdout.strip()
        run(sys.executable, str(ROOT / "tests/integration.py"), "--router", router, "--client", client,
            "--ipv4", address, "--mac", mac, "--ipk", "/src/dist/" + packages[0].name, "--report", str(report))
        evidence = json.loads(report.read_text())
        evidence["environment"] = {
            "openwrt": VERSION, "architecture": architecture, "rootfs_sha256": actual,
            "host_kernel": platform.release(),
            "openwrt_release": docker("exec", router, "cat", "/etc/openwrt_release").stdout,
            "luci": docker("exec", router, "opkg", "status", "luci-base").stdout,
        }
        report.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    finally:
        for container in reversed(containers):
            docker("logs", container, check=False)
            docker("rm", "-f", container, check=False)
        if network_created:
            docker("network", "rm", prefix, check=False)
        if images:
            docker("image", "rm", *images, check=False)
        log.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=ROOT / "dist/integration-results.json")
    main(parser.parse_args().report)
