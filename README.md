# OpenWrt WebRTC 防护插件

[![CI](https://github.com/Y2zz/openwrt-webrtc-guard/actions/workflows/ci.yml/badge.svg)](https://github.com/Y2zz/openwrt-webrtc-guard/actions/workflows/ci.yml)

面向 **OpenWrt 24.10.5 / firewall4 / LuCI 24.10** 的首版插件，目标是减少 WebRTC 的 STUN 探测暴露出口 IP。包内只有 Shell、LuCI JavaScript 和配置文件，`Architecture: all`，不依赖 CPU 指令集。其他系统版本尚未验证。

**路由器无法彻底关闭浏览器 WebRTC，也不能保证完全防止 IP 泄露。**

## 已实现

- 中文 LuCI 页面：服务 → WebRTC 防护。
- 在指定入口接口拦截客户端流量，使用同一组 `inet` 规则覆盖 IPv4 和 IPv6。
- 根据 UDP STUN 报文的类型字段和 magic cookie 识别探测，不局限于 3478 端口。
- 补充封禁可配置的 TCP/UDP 目标端口，默认 3478、5349。
- 普通模式保留其他 UDP；严格模式额外封禁非白名单 UDP。
- 严格模式默认放行 UDP 53、67–68、123、546–547。STUN 特征和封禁端口优先于白名单。
- 防火墙重载后重新应用；独立 nftables 表；检查通过后原子替换；配置错误保留上一版规则。
- 显示实际规则、命中计数和应用错误；关闭或卸载时只移除本插件的规则。
- 默认关闭。检测到软件/硬件 flow offload 配置或活动 flowtable 时拒绝启用，避免误报防护已生效。

## 安装

适用于使用 **opkg** 的 OpenWrt 24.10.5。可以直接从 [GitHub Releases](https://github.com/Y2zz/openwrt-webrtc-guard/releases/latest) 下载 IPK 和 SHA256SUMS，并核对校验和；也可以从源码仓库获取项目后，在电脑上的项目根目录生成安装包：

```sh
python3 tools/build_ipk.py
```

把下载或本地生成的 `luci-app-webrtc-guard_0.1.0-1_all.ipk` 上传到路由器 `/tmp/`，在路由器执行：

```sh
opkg update
opkg install /tmp/luci-app-webrtc-guard_0.1.0-1_all.ipk
```

依赖 `luci-base`、`firewall4`、`nftables-json`、`flock`，由 opkg 从与你的固件匹配的软件源安装。不要跨版本安装内核模块。若第三方固件使用 apk，请使用相应 SDK 从源码构建，不要直接安装此 IPK。

安装后刷新 LuCI 页面；菜单未出现时重新登录。首次安装默认不拦截流量。

## 使用

1. 在“网络 → 防火墙”关闭软件和硬件流量卸载，保存并应用。插件不会自行修改这些设置。
2. 在“服务 → WebRTC 防护”选择受控入口 Linux 接口。常见值为 `br-lan`，访客网桥需额外选择；此处不是 UCI 的 `lan` 网络名称。
3. 先选择普通模式，启用并“保存并应用”。检查运行状态是否显示规则已加载；需要时点击“重新应用已保存的配置”。
4. 检查网页、DNS、IPv6 和实际代理/VPN场景，再评估是否使用严格模式。

普通模式也会影响使用 STUN/TURN 的通话。严格模式还可能影响 HTTP/3、游戏、UDP VPN、NTP 以外的 UDP 服务。白名单不是应用识别，使用白名单端口的非 STUN 数据仍会放行。

规则在 prerouting 优先级 -310 执行，早于常见透明代理的重定向和 fw4 的 established 放行。关闭防护后原有防火墙继续决定流量去向，插件不为其他业务增加 accept 规则。

## 防泄露范围

- **可以拦截**：经所选接口进入路由器 IP 协议栈的可见 UDP STUN 报文，以及配置的常用端口流量。
- **无法保证拦截**：TCP/TLS TURN，特别是 TCP 443；VPN、加密代理或其他隧道内的 WebRTC；未经过所选接口的流量；同网桥内二层直通；第三方 SFE/NSS/厂商硬件加速路径；已经通过其他路径建立的会话。
- **无法控制**：浏览器在本机枚举 host candidate/内网地址、WebRTC API 是否存在、终端其他网卡或移动网络。此插件不处理路由器自身发起的 OUTPUT 流量。
- UDP STUN 识别使用标准头部特征，属于报文匹配，不是完整协议解析；存在误匹配和绕过的可能。不要把计数为零、某个检测站点通过，或页面显示已加载当作无泄露证明。

对严格防泄露需求，建议同时在终端浏览器配置 WebRTC IP 处理策略，并核对代理/VPN 的 IPv4、IPv6 出口。终端策略应按实际浏览器和管理方式设置；本插件不修改终端。

## 命令行

```sh
# 查看真实运行状态和规则
/usr/libexec/webrtc-guard status
nft list table inet webrtc_guard

# 启用并应用
uci set webrtc-guard.main.enabled='1'
uci commit webrtc-guard
/etc/init.d/webrtc-guard reload

# 手动应用会返回明确的成功/失败退出码
/usr/libexec/webrtc-guard apply

# 配置检查并打印候选规则；不安装规则
/usr/libexec/webrtc-guard render
```

`render` 只检查配置，`apply` 还会执行 `nft -c` 和实际安装。流量卸载关闭后，如旧 flowtable 仍在，请重载防火墙再应用。第三方加速模块需要自行关闭；插件无法完整发现这些模块。

## 关闭、恢复与卸载

```sh
# 持久关闭；防火墙重载后也不会再次启用
uci set webrtc-guard.main.enabled='0'
uci commit webrtc-guard
/usr/libexec/webrtc-guard apply

# 卸载（配置文件可能由 opkg 保留）
opkg remove luci-app-webrtc-guard
```

`/etc/init.d/webrtc-guard stop` 只临时移除规则；若配置仍为启用，后续防火墙重载会再次加载。

## 源码和构建

```text
package/luci-app-webrtc-guard/   OpenWrt SDK 标准软件包
tools/build_ipk.py              纯 Python、可复现的 all 架构 IPK 打包工具
tests/                         隔离容器中的安装及真实报文测试
dist/                          可安装包
VALIDATION.md                  本次实际验证结果与限制
```

本地生成 IPK，无第三方 Python 依赖：

```sh
python3 tools/build_ipk.py
```

也可将整个 `package/luci-app-webrtc-guard` 目录复制到 **与你固件匹配的 OpenWrt SDK** 的 `package/` 下，然后执行：

```sh
make defconfig
make package/luci-app-webrtc-guard/compile V=s
```

SDK 构建使用标准 `package.mk`，无需把此项目放进 LuCI feeds；本地 Python 工具生成传统 opkg 格式包，不是 SDK 编译结果。升级安装保留 `/etc/config/webrtc-guard`。

## GitHub Actions 与发布

推送 `main`、创建 Pull Request 或手动触发 [CI](https://github.com/Y2zz/openwrt-webrtc-guard/actions/workflows/ci.yml) 后，自动检查源码语法、ShellCheck、IPK 内容及可复现性，并运行官方 OpenWrt 24.10.5 用户态容器的真实 IPv4/IPv6 报文测试。安装包、校验和及测试证据作为 Actions artifacts 保留 14 天。容器测试使用运行器宿主内核，不代替实体路由器验证。

发布新版本时更新 `Makefile` 的 `PKG_VERSION`，添加对应的 `docs/releases/v版本号.md`，提交后推送 `v版本号` 标签。CI 会核对标签与版本号；全部必需检查通过后，自动发布 GitHub Release，附上 IPK 和 SHA256SUMS。普通分支和 Pull Request 不会发布版本。第三方 Actions 固定到提交 SHA，只有发布任务具有仓库内容写权限。

```sh
python3 tools/verify.py --tag v0.1.0
git tag -a v0.1.0 -m 'WebRTC Guard v0.1.0'
git push origin v0.1.0
```

## 依据

- [RFC 8489：STUN 报文格式](https://www.rfc-editor.org/rfc/rfc8489.html#section-5)
- [RFC 8835：WebRTC 传输及 TCP/TLS TURN](https://www.rfc-editor.org/rfc/rfc8835.html#section-3.5)
- [OpenWrt firewall4 的规则和 include 执行方式](https://github.com/openwrt/firewall4)
- [LuCI JavaScript 应用示例](https://github.com/openwrt/luci/tree/openwrt-24.10/applications/luci-app-example)

许可证：GPL-2.0-only。
