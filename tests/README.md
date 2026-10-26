# 真实网络验证

`integration.py` 会启停防护、修改 UCI、重载防火墙、重新安装及卸载插件。**只对一次性测试容器运行，不能指向生产设备。**

本次使用 macOS ARM64 + Docker Linux VM。路由器容器使用官方 OpenWrt 24.10.5 armsr/armv8 rootfs；其用户态版本和修订号与目标固件一致，内核来自 Docker VM，不等同于路由器内核。

## 测试环境准备

以下命令从项目根目录执行，使用独立的测试容器名称。下载和 Docker 构建可在项目外的临时目录进行。

```sh
python3 tools/build_ipk.py

curl -fL https://downloads.openwrt.org/releases/24.10.5/targets/armsr/armv8/openwrt-24.10.5-armsr-armv8-rootfs.tar.gz -o /tmp/openwrt-24.10.5-rootfs.tar.gz
```

本次下载文件的官方 SHA256：`f8f6bba184d691365e15bf6241a3f88afe7af47fc443c6e9ee5de15e1c9d6d43`。运行前应重新与[官方 sha256sums](https://downloads.openwrt.org/releases/24.10.5/targets/armsr/armv8/sha256sums)核对。

```sh
docker import /tmp/openwrt-24.10.5-rootfs.tar.gz webrtc-guard-openwrt-test:24.10.5
docker run -d --name webrtc-guard-openwrt-test \
  --cap-add NET_ADMIN --cap-add NET_RAW \
  -v "$PWD:/src:ro" \
  webrtc-guard-openwrt-test:24.10.5 /sbin/procd
docker exec webrtc-guard-openwrt-test sh -c 'mkdir -p /var/run/ubus; /sbin/ubusd -s /var/run/ubus/ubus.sock >/tmp/ubusd.log 2>&1 &'
```

等待 procd/netifd 启动后，通过 `docker inspect` 获取容器的 Docker IPv4、网关和网络掩码，并将容器内 `network.lan` 配置为这些地址。OpenWrt 默认配置会把 eth0 加入 br-lan 并使用 192.168.1.1，必须调整才能在容器内访问软件源。例如**仅当分配地址确实为以下值时**：

```sh
docker exec webrtc-guard-openwrt-test sh -c '
  uci set network.lan.ipaddr=172.17.0.2
  uci set network.lan.netmask=255.255.0.0
  uci set network.lan.gateway=172.17.0.1
  uci set network.lan.dns=192.168.65.7
  uci commit network
  /etc/init.d/network reload
'
docker exec webrtc-guard-openwrt-test sh -c 'opkg update && opkg install /src/dist/luci-app-webrtc-guard_0.1.0-1_all.ipk'
docker exec webrtc-guard-openwrt-test ip -6 addr add fd42:7767::1/64 dev br-lan
docker exec webrtc-guard-openwrt-test /etc/init.d/dnsmasq stop
```

制作包含 Python 3 和 iproute2 的 Alpine 测试镜像：

```dockerfile
FROM alpine:3.20
RUN apk add --no-cache python3 iproute2
```

将该镜像命名为 `webrtc-guard-test-client:local` 后：

```sh
docker run -d --name webrtc-guard-client \
  --cap-add NET_ADMIN --cap-add NET_RAW \
  --network name=bridge,driver-opt=com.docker.network.endpoint.sysctls=net.ipv6.conf.IFNAME.disable_ipv6=0 \
  -v "$PWD:/src:ro" webrtc-guard-test-client:local sleep infinity
docker exec webrtc-guard-client ip -6 addr add fd42:7767::2/64 dev eth0

# 下面的 IPv4 替换为实际路由器容器地址。
docker run -d --name webrtc-guard-echo \
  --network container:webrtc-guard-openwrt-test \
  -v "$PWD:/src:ro" webrtc-guard-test-client:local \
  python3 /src/tests/traffic.py server 172.17.0.2 fd42:7767::1

docker exec webrtc-guard-openwrt-test cat /sys/class/net/br-lan/address
```

获取 br-lan MAC 后，传入实际值运行：

```sh
python3 tests/integration.py \
  --ipv4 172.17.0.2 --mac aa:7f:7b:4a:79:dc \
  --report /tmp/webrtc-guard-results.json
```

报文测试只使用 Python 标准库，包括真实 IPv4/IPv6 分片和 IPv6 Hop-by-Hop 扩展头。TCP 443 使用容器里真实的 uhttpd HTTPS 服务；其他端口使用回显服务。DNS/NTP 项验证相应端口的 UDP 数据可达，不等同于完整 DNS/NTP 协议测试。真实 flowtable 创建取决于宿主内核支持，不支持时明确记录跳过，不计入通过项。

测试结束后会重新安装插件并关闭防护；容器清理：

```sh
docker rm -f webrtc-guard-echo webrtc-guard-client webrtc-guard-openwrt-test
```

## 静态检查

```sh
node --check package/luci-app-webrtc-guard/htdocs/luci-static/resources/view/webrtc-guard.js
shellcheck -s dash -e SC1091 \
  package/luci-app-webrtc-guard/root/usr/libexec/webrtc-guard \
  package/luci-app-webrtc-guard/root/etc/init.d/webrtc-guard \
  package/luci-app-webrtc-guard/root/etc/uci-defaults/90-webrtc-guard
```

ShellCheck 使用 dash 模式接受目标 BusyBox ash 支持的 `local`；SC1091 仅忽略测试主机上不存在的 OpenWrt 库。init 脚本对 rc.common 消费的元数据变量单独标注 SC2034。
