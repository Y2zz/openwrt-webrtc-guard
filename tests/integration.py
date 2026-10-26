#!/usr/bin/env python3
"""Run only against disposable fixtures documented in tests/README.md."""
import argparse
import json
from pathlib import Path
import subprocess
import time

HELPER = "/usr/libexec/webrtc-guard"


class Suite:
    def __init__(self, args):
        self.args = args
        self.results = []
        self.skipped = []

    def router(self, *command, success=True):
        result = subprocess.run(["docker", "exec", self.args.router, *command], text=True, capture_output=True)
        if success and result.returncode:
            raise AssertionError(result.stderr or result.stdout)
        return result

    def check(self, description, condition):
        assert condition, description
        self.results.append(description)
        print("PASS", description, flush=True)

    def state(self):
        return json.loads(self.router(HELPER, "status").stdout)

    def configure(self, mode="balanced", enabled="1"):
        self.router("uci", "set", f"webrtc-guard.main.mode={mode}")
        self.router("uci", "set", f"webrtc-guard.main.enabled={enabled}")
        self.router("uci", "commit", "webrtc-guard")
        self.router(HELPER, "apply")

    def packet(self, name, expected, version=4, port=40000, protocol="udp", payload="normal", raw=None):
        data = dict(family=version, address=self.args.ipv4 if version == 4 else self.args.ipv6,
                    port=port, protocol=protocol, payload=payload, raw=raw, mac=self.args.mac)
        result = subprocess.run(["docker", "exec", self.args.client, "python3", "/src/tests/traffic.py",
                                 "probe", json.dumps(data)], text=True, capture_output=True, check=True)
        self.check(name, json.loads(result.stdout)["delivered"] == expected)

    def run(self):
        self.configure(enabled="0")
        self.check("关闭后无防护表", not self.state()["active"])
        for version in (4, 6):
            self.packet(f"IPv{version} 基线 UDP STUN 可达", True, version, payload="stun")
            self.packet(f"IPv{version} 基线 TCP 3478 可达", True, version, port=3478, protocol="tcp")
            self.packet(f"IPv{version} 基线分片 UDP STUN 可达", True, version, payload="stun", raw="fragment")
        self.packet("IPv6 扩展头基线可达", True, 6, payload="stun", raw="extension")

        self.configure()
        for version in (4, 6):
            self.packet(f"IPv{version} 非标准端口 STUN 被拦截", False, version, payload="stun")
            self.packet(f"IPv{version} UDP 3478 被拦截", False, version, port=3478)
            self.packet(f"IPv{version} TCP 5349 被拦截", False, version, port=5349, protocol="tcp")
            self.packet(f"IPv{version} 普通 UDP 保留", True, version)
            self.packet(f"IPv{version} DNS 端口保留", True, version, port=53)
            self.packet(f"IPv{version} 同 cookie 非 STUN 类型保留", True, version, payload="not-stun")
            self.packet(f"IPv{version} 短报文保留", True, version, payload="short")
            self.packet(f"IPv{version} TCP 443 保留（防护边界）", True, version, port=443, protocol="tcp")
            self.packet(f"IPv{version} 分片 STUN 被拦截", False, version, payload="stun", raw="fragment")
        self.packet("IPv6 扩展头 STUN 被拦截", False, 6, payload="stun", raw="extension")
        self.check("STUN 命中计数增加", "packets 0 bytes 0 drop comment \"WebRTC Guard: STUN UDP\"" not in self.state()["rules"])

        # Ensure fw4 include restores a deleted guard table, not merely preserves it.
        self.router("nft", "delete", "table", "inet", "webrtc_guard")
        self.router("/etc/init.d/firewall", "reload")
        self.check("防火墙重载重建防护表", self.state()["active"])

        # LuCI Save & Apply emits this event after committing UCI.
        self.router("uci", "set", "webrtc-guard.main.mode=strict")
        self.router("uci", "commit", "webrtc-guard")
        self.router("ubus", "call", "service", "event", '{"type":"config.change","data":{"package":"webrtc-guard"}}')
        for _ in range(30):
            if "strict UDP" in self.state()["rules"]:
                break
            time.sleep(0.2)
        self.check("UCI 应用事件触发严格模式", "strict UDP" in self.state()["rules"])
        for version in (4, 6):
            self.packet(f"IPv{version} 严格模式拦截 UDP 443", False, version, port=443)
            self.packet(f"IPv{version} 严格模式放行 DNS", True, version, port=53)
            self.packet(f"IPv{version} 严格模式放行 NTP", True, version, port=123)
            self.packet(f"IPv{version} STUN 优先于 DNS 白名单", False, version, port=53, payload="stun")
            self.packet(f"IPv{version} 严格模式保留 TCP 443", True, version, port=443, protocol="tcp")

        for bad in ("0", "65536", "3478;drop", "10-1", "01", "1-2-3", "999999999999999999"):
            old = self.state()["rules"]
            self.router("uci", "add_list", f"webrtc-guard.main.blocked_ports={bad}")
            self.router("uci", "commit", "webrtc-guard")
            result = self.router(HELPER, "apply", success=False)
            self.check(f"非法端口 {bad!r} 拒绝且旧规则保留", result.returncode != 0 and self.state()["rules"] == old)
            self.router("uci", "del_list", f"webrtc-guard.main.blocked_ports={bad}")
            self.router("uci", "commit", "webrtc-guard")
        self.router("uci", "set", 'webrtc-guard.main.interfaces=br-lan";drop')
        self.router("uci", "commit", "webrtc-guard")
        self.check("接口注入拒绝且保留旧规则", self.router(HELPER, "apply", success=False).returncode != 0 and self.state()["active"])
        self.router("uci", "delete", "webrtc-guard.main.interfaces")
        self.router("uci", "add_list", "webrtc-guard.main.interfaces=br-lan")
        self.router("uci", "commit", "webrtc-guard")

        self.router("uci", "set", "firewall.@defaults[0].flow_offloading=1")
        self.router("uci", "commit", "firewall")
        self.check("流量卸载启用时拒绝应用并报告错误", self.router(HELPER, "apply", success=False).returncode != 0 and bool(self.state()["error"]))
        self.router("uci", "set", "firewall.@defaults[0].flow_offloading=0")
        self.router("uci", "commit", "firewall")
        self.router("nft", "add", "table", "inet", "wg_offload_test")
        flowtable = self.router("nft", "add", "flowtable", "inet", "wg_offload_test", "ft", "{ hook ingress priority 0; devices = { br-lan }; }", success=False)
        if flowtable.returncode == 0:
            self.check("活动 flowtable 被检测", self.router(HELPER, "apply", success=False).returncode != 0 and "flowtable" in self.state()["error"])
        else:
            self.skipped.append("真实 flowtable 检测：容器宿主内核不支持创建 nft flowtable")
            print("SKIP", self.skipped[-1], flush=True)
        self.router("nft", "delete", "table", "inet", "wg_offload_test")
        self.configure()

        # Select a different interface; packets on br-lan must not be intercepted.
        self.router("uci", "delete", "webrtc-guard.main.interfaces")
        self.router("uci", "add_list", "webrtc-guard.main.interfaces=lo")
        self.router("uci", "commit", "webrtc-guard")
        self.router(HELPER, "apply")
        self.packet("未受控接口不拦截", True, payload="stun")
        self.router("uci", "delete", "webrtc-guard.main.interfaces")
        self.router("uci", "add_list", "webrtc-guard.main.interfaces=br-lan")
        self.router("uci", "commit", "webrtc-guard")
        self.configure()

        # Repeat an installation to exercise conffile preservation and idempotent hooks.
        self.router("opkg", "install", "--force-reinstall", "/src/dist/luci-app-webrtc-guard_0.1.0-1_all.ipk")
        self.check("重复安装保留已启用配置", self.state()["configured"] and self.state()["active"])
        # Exercise OpenWrt SDK's default_postinst ordering with the real library.
        self.router("sh", "-c", "cp /usr/lib/opkg/info/luci-app-webrtc-guard.postinst /usr/lib/opkg/info/luci-app-webrtc-guard.postinst-pkg; cp /src/package/luci-app-webrtc-guard/root/etc/uci-defaults/90-webrtc-guard /etc/uci-defaults/90-webrtc-guard; . /lib/functions.sh; default_postinst /usr/lib/opkg/info/luci-app-webrtc-guard.postinst")
        self.check("SDK postinst 先执行 uci-defaults 的顺序兼容", self.state()["active"] and self.router("test", "!", "-f", "/etc/uci-defaults/90-webrtc-guard", success=False).returncode == 0)
        self.router("rm", "-f", "/usr/lib/opkg/info/luci-app-webrtc-guard.postinst-pkg")
        self.router("opkg", "remove", "luci-app-webrtc-guard")
        self.check("卸载清除独立规则表", self.router("nft", "list", "table", "inet", "webrtc_guard", success=False).returncode != 0)
        self.check("卸载清除专属 firewall include", self.router("uci", "get", "firewall.webrtc_guard", success=False).returncode != 0)
        self.check("卸载保留 fw4", self.router("nft", "list", "table", "inet", "fw4").returncode == 0)
        self.packet("卸载后 STUN 恢复可达", True, payload="stun")
        self.router("opkg", "install", "/src/dist/luci-app-webrtc-guard_0.1.0-1_all.ipk")
        self.configure(enabled="0")
        return self.results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--router", default="webrtc-guard-openwrt-test")
    parser.add_argument("--client", default="webrtc-guard-client")
    parser.add_argument("--ipv4", required=True)
    parser.add_argument("--ipv6", default="fd42:7767::1")
    parser.add_argument("--mac", required=True)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()
    suite = Suite(args)
    results = suite.run()
    if args.report:
        args.report.write_text(json.dumps({"passed": len(results), "checks": results, "skipped": suite.skipped}, ensure_ascii=False, indent=2) + "\n")
    print(f"{len(results)} checks passed")
