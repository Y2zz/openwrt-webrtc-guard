'use strict';
'require view';
'require form';
'require fs';
'require ui';
'require poll';
'require tools.widgets as widgets';

var helper = '/usr/libexec/webrtc-guard';

function getStatus() {
	return fs.exec(helper, ['status']).then(function(result) {
		if (result.code !== 0)
			throw new Error(result.stderr || '读取防护状态失败');
		return JSON.parse(result.stdout);
	});
}

return view.extend({
	render: function() {
		var m, s, o;
		var state = E('p', {}, '正在读取状态…');
		var details = E('pre', { 'style': 'white-space:pre-wrap;max-height:22em;overflow:auto' });
		var refresh = function() {
			return getStatus().then(function(data) {
				var message = data.active ? '防护规则已加载' : '防护规则未加载';
				if (data.configured && !data.active)
					message += '；配置已启用，但尚未成功应用';
				if (!data.configured && data.active)
					message += '；配置已关闭，旧规则尚未移除';
				if (data.error)
					message += '。最近一次操作失败：' + data.error;
				state.textContent = message;
				state.style.color = data.error || (data.configured && !data.active) ? '#b42318' : '';
				details.textContent = data.rules || '没有活动规则。计数只反映拦截命中，不能证明不存在 IP 泄露。';
			}).catch(function(error) {
				state.textContent = '无法读取状态：' + error.message;
				state.style.color = '#b42318';
			});
		};

		m = new form.Map('webrtc-guard', 'WebRTC 防护',
			'通过拦截经过路由器的 STUN 探测和常用 TURN 端口，降低 WebRTC 暴露出口 IP 的风险。' +
			'本插件无法关闭浏览器 WebRTC API、阻止本机地址收集，或识别 TCP 443 上的加密 TURN。' +
			'代理/VPN 隧道内的流量也可能无法识别。需要严格防泄露时，请同时配置浏览器。');
		s = m.section(form.NamedSection, 'main', 'guard', '防护设置');
		s.addremove = false;

		o = s.option(form.Flag, 'enabled', '启用防护');
		o.rmempty = false;
		o.default = '0';

		o = s.option(form.ListValue, 'mode', '防护模式');
		o.value('balanced', '普通：STUN 特征 + 常用端口');
		o.value('strict', '严格：额外拦截非白名单 UDP');
		o.default = 'balanced';
		o.rmempty = false;
		o.description = '普通模式也会影响使用 STUN/TURN 的通话。严格模式还可能影响 HTTP/3、游戏、UDP VPN 和其他 UDP 应用；仍无法保证完全防泄露。';

		o = s.option(widgets.DeviceSelect, 'interfaces', '受控入口接口');
		o.multiple = true;
		o.noaliases = true;
		o.rmempty = false;
		o.description = '选择客户端流量进入路由器的 Linux 接口，通常为 br-lan；访客网桥需单独加入。不要填 UCI 网络名称 lan。仅覆盖经过本路由器 IP 协议栈的流量。';
		o.validate = function(section, value) {
			if (!value || !value.length)
				return '至少选择一个受控入口接口';
			var values = Array.isArray(value) ? value : [value];
			if (values.length > 32 || values.some(function(device) {
				return !/^[a-zA-Z0-9_.:@-]{1,15}$/.test(device);
			})) return '接口名称无效，或数量超过 32 个';
			return true;
		};

		function validatePort(section, value) {
			if (!value) return true;
			if (!/^[1-9][0-9]{0,4}(-[1-9][0-9]{0,4})?$/.test(value))
				return '填写端口或范围，例如 3478、19302-19309';
			var parts = value.split('-').map(Number);
			return parts[0] <= 65535 && (parts.length === 1 ||
				(parts[1] <= 65535 && parts[0] <= parts[1])) ? true : '端口范围必须在 1–65535 内且升序';
		}

		o = s.option(form.DynamicList, 'blocked_ports', '封禁的 TCP/UDP 目标端口');
		o.validate = validatePort;
		o.description = '默认 3478、5349。UDP STUN 特征识别不依赖端口；此列表用于补充封禁，可能影响其他使用相同端口的服务。';

		o = s.option(form.DynamicList, 'udp_allow_ports', '严格模式 UDP 放行端口');
		o.depends('mode', 'strict');
		o.retain = true;
		o.validate = validatePort;
		o.description = '默认保留 DNS、DHCP、NTP、DHCPv6。STUN 特征和封禁端口优先于此白名单；白名单中的加密流量仍可能绕过。';

		var button = E('button', {
			'class': 'cbi-button cbi-button-action',
			'click': ui.createHandlerFn(this, function() {
				return fs.exec(helper, ['apply']).then(function(result) {
					if (result.code !== 0)
						ui.addNotification(null, E('p', {}, result.stderr || '应用失败'), 'error');
					return refresh();
				});
			})
		}, '重新应用已保存的配置');

		return m.render().then(function(map) {
			poll.add(refresh, 5);
			refresh();
			return E('div', {}, [map,
				E('div', { 'class': 'cbi-section' }, [
					E('h3', {}, '运行状态'), state,
					E('p', {}, '启用前请先在“网络 → 防火墙”关闭软件和硬件流量卸载。修改配置后点击“保存并应用”，再检查这里的结果。'),
					button,
					E('details', {}, [E('summary', {}, '查看规则及拦截计数'), details])
				])]);
		});
	}
});
