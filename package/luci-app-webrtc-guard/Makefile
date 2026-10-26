# SPDX-License-Identifier: GPL-2.0-only
include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-webrtc-guard
PKG_VERSION:=0.1.0
PKG_RELEASE:=1
PKG_LICENSE:=GPL-2.0-only
PKGARCH:=all

include $(INCLUDE_DIR)/package.mk

define Package/luci-app-webrtc-guard
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=WebRTC network traffic guard
  DEPENDS:=+luci-base +firewall4 +nftables-json +flock
endef

define Package/luci-app-webrtc-guard/description
  LuCI interface and nftables rules to restrict STUN/TURN and UDP traffic.
  This package does not disable browser WebRTC APIs or inspect encrypted TURN.
endef

define Package/luci-app-webrtc-guard/conffiles
/etc/config/webrtc-guard
endef

define Build/Compile
endef

define Package/luci-app-webrtc-guard/install
	$(CP) ./root/* $(1)/
	$(INSTALL_DIR) $(1)/www
	$(CP) ./htdocs/* $(1)/www/
endef

define Package/luci-app-webrtc-guard/postinst
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ]; then
	# SDK default_postinst may already have run and removed this file.
	if [ -f /etc/uci-defaults/90-webrtc-guard ]; then
		/etc/uci-defaults/90-webrtc-guard || exit 1
		rm -f /etc/uci-defaults/90-webrtc-guard
	fi
	/etc/init.d/webrtc-guard enable
	/etc/init.d/webrtc-guard start
	/etc/init.d/rpcd reload
	rm -f /tmp/luci-indexcache
fi
exit 0
endef

define Package/luci-app-webrtc-guard/prerm
#!/bin/sh
if [ -z "$${IPKG_INSTROOT}" ]; then
	/etc/init.d/webrtc-guard stop || exit 1
	/etc/init.d/webrtc-guard disable
	uci -q delete firewall.webrtc_guard
	uci commit firewall
fi
exit 0
endef

$(eval $(call BuildPackage,luci-app-webrtc-guard))
