# 验证记录

日期：2026-10-26。交付版本：0.1.0-1。

## 环境与结果

- 用户目标：OpenWrt 24.10.5 r29087-d9c5716d1d，LuCI openwrt-24.10 branch 26.052.42948~5f0d7d5。
- 实测用户态：官方 OpenWrt 24.10.5 armsr/armv8 rootfs，修订号 r29087-d9c5716d1d；rootfs SHA256 与官方校验文件一致。
- 实测 LuCI：openwrt-24.10 branch 25.340.26705~d88390b，与用户提供的补丁版本不同。
- 实测运行环境：ARM64 Docker 容器，宿主 Linux 6.12.76-linuxkit。并非实体路由器，也不是 OpenWrt 6.6 内核。
- IPK 通过真实 opkg 安装，依赖解析正常。安装、启用、配置重载、重复安装、卸载、恢复均有实测。
- `tests/integration.py`：**56 项通过，1 项跳过**，明细见 `tests/results-24.10.5.json`。
- Node JavaScript 语法、Python 语法、Shell 语法、JSON 格式检查通过。
- ShellCheck 检查通过，参数和框架相关豁免见 `tests/README.md`。
- IPK 重复生成的 SHA256 相同。

## 报文与生命周期覆盖

客户端、路由器和回显服务分属隔离容器。每组拦截测试前验证关闭防护时同类报文能到达，随后再验证开启后的变化。

已验证 IPv4/IPv6 下的非标准端口 UDP STUN、常用 TCP/UDP 端口拦截、普通 UDP 保留、短报文与非 STUN 类型保留；验证了 IPv4/IPv6 真实分片、IPv6 Hop-by-Hop 扩展头。严格模式下验证 UDP 443 被拦截，DNS/NTP 端口保留，STUN 特征优先于 UDP 白名单。

TCP 443 使用 uhttpd 完成真实 TLS/HTTPS 访问，两种模式均保持可达。这证明插件不会阻断普通 HTTPS，也说明共享该传输方式的加密 TURN 不在可靠识别范围内；没有把该项当作成功阻断 TURN 的证据。

已验证删除插件表后通过 fw4 重载重建、UCI 应用事件触发模式变更、非法配置保留旧规则、流量卸载配置的拒绝处理、未受控接口保留流量、重复安装保留配置、SDK 默认 postinst 先运行 uci-defaults 的执行顺序、卸载移除专属表和 include 且保留 fw4。

DNS/NTP 项是相应端口的真实 UDP 收发验证，不是对完整 DNS/NTP 服务、DHCP、DHCPv6 或互联网连通性的完整验收。

## LuCI

使用实际 LuCI 登录页面进入“服务 → WebRTC 防护”，页面正常加载，状态查询正常。通过浏览器从普通模式改为严格模式并点击“Save & Apply”，确认 UCI 配置和内核规则均已更新。最终重新安装并关闭防护，截图见 `docs/luci.png`。

## 尚未验证

- 真实 nft flowtable 的运行时检测：容器宿主内核拒绝创建 flowtable，已明确跳过。UCI 软件/硬件卸载配置的检查已覆盖软件卸载启用的真实场景。
- 用户实体设备、用户 LuCI 补丁版本、OpenWrt 6.6 内核、设备重启后的完整启动过程。
- 厂商 SFE/NSS 加速、其他代理/防火墙插件组合、二层直通、复杂 VLAN/桥接和硬件卸载。
- 使用真实浏览器 ICE candidate 与真实公网 STUN/TURN 服务的泄露验收；没有证明完全不泄露真实 IP。
- OpenWrt SDK 实际构建。当前 IPK 由随附 Python 工具打包；已使用真实 OpenWrt `default_postinst` 验证与 SDK 安装钩子顺序的兼容性。

所有测试只在本地隔离容器运行，没有连接或修改用户路由器。
