# WinVpnSplit: Windows 原生 VPN 域名级精准分流方案

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-Windows%2010%20%7C%2011-blue)](https://microsoft.com)
[![Python](https://img.shields.io/badge/python-3.8%2B-brightgreen.svg)](https://python.org)

> 一个无需安装内核驱动、不篡改系统 VPN 配置、不搞静态 IP 打地鼠的 Windows 自带 VPN（L2TP/IPsec, IKEv2, SSTP）纯应用层域名精准分流方案。

---

## 📌 背景与核心痛点

Windows 自带的虚拟专用网（VPN）是操作系统内核原生实现的经典隧道模型。但在日常使用中，用户常常陷入两难：

1. **全局 VPN 开启时**：所有流量（包括国内应用如微信、钉钉、B站、局域网协同）全被强制塞入隧道，导致国内应用访问极其迟缓、浪费宝贵出海流量，甚至频繁触发国内各类网银与业务系统的“异地登录安全风控”；
2. **用户的真实诉求**：**“只有特定网站（例如 ChatGPT, Claude, Arena, 海外研发文档）走 VPN 隧道出海，其余所有普通流量（国内网站、软件通信）一律走原生物理宽带直连。”**

市面上现有的各种教程与方案，在 Windows 原生网络栈面前往往全部碰壁。

---

## ⚠️ 踩坑归档：Windows 原生网络栈的 4 大内核陷阱

在探索本方案的过程中，我们针对 Windows 路由表与 Winsock 套接字底层进行了严格的单变量实测，证实了以下 4 个普遍存在的经典陷阱：

### 1. 陷阱一：开启“拆分隧道”等于自杀（WinError 10051 / 网络不可达）
- **误区**：很多人建议在 Windows VPN 适配器属性中取消勾选“在远程网络上使用默认网关”（即所谓的 Split Tunneling 拆分隧道），以为这样就能让微代理通过 `bind()` 虚拟网卡 IP 单独出网。
- **底层真相**：一旦开启拆分隧道，Windows 内核会**瞬间移除虚拟网卡自身的 `0.0.0.0/0` 默认路由**。此时由于虚拟网卡不存在去往公网的选路条目，即使应用层套接字显式 `bind()` 到该网卡 IP，向任何公网发起连接都会被 Windows 协议栈即时阻断，报错 `WinError 10051 (WSAENETUNREACH: 网络不可达)`。

### 2. 陷阱二：强主机模型（Strong Host Model）导致无法从全局逃逸（WinError 10065）
- **误区**：在保持全局默认路由的前提下，尝试将普通应用程序的套接字 `bind()` 到物理网卡（如 WLAN）的本地 IP，试图让其走物理出口。
- **底层真相**：Windows 严格遵守 RFC 1122 强主机模型（默认 `WeakHostSend=Disabled`, `WeakHostReceive=Disabled`）。当出站路由决策判定出口为 VPN 虚拟网卡时，如果套接字的源 IP 为物理网卡地址，内核会直接判定“源地址与出接口不匹配”并丢弃发包，触发 `WinError 10065 (WSAEHOSTUNREACH: 套接字操作尝试连接一个不可达的主机)`。

### 3. 陷阱三：抬高跃点无法使普通流量回落物理网卡（On-Link 特权陷阱）
- **误区**：认为 VPN 是靠“跃点低（Metric）”获胜的，只要把 VPN 接口的跃点和路由跃点改得极高（例如 10000），就能让流量自动回落到物理网卡。
- **底层真相**：在 Windows 路由仲裁机制中：
  - VPN 接口为点对点（P2P）虚拟接口，其默认路由下一跳为 `0.0.0.0`（在链路上，On-Link）；
  - 物理网卡则是经由局域网网关（NextHop = `192.168.x.1`）。
  **在掩码同为 `/0` 的情况下，Windows 内核判定直连在链路（On-Link）路由的优先级天然高于经网关路由，忽略跃点比较**！跃点修改彻底失效。

### 4. 陷阱四：静态 `/32` 明细路由打地鼠（Anycast IP 漂移与附属域名爆炸）
- **误区**：通过定期解析域名获取目标 IP，向系统路由表动态写入 `route add <IP> mask 255.255.255.255 ...`。
- **底层真相**：现代海外大型服务（如 OpenAI、Cloudflare 等）均依托全球 Anycast 边缘网络，背后伴随多达数十个二级微服务域名（如 `oaistatic.com`, `oaiusercontent.com`, `auth0.openai.com` 等），且全球 DNS 解析结果以分钟级频次动态漂移。只要漏掉一个子域名或 IP 发生漂移，该请求就会跌落直连被墙，导致“网页能打开但对话转圈卡死”。频繁修改内核路由表更会引入高昂的系统开销与连接竞争。

---

## 🚀 破局之道：A* 双层解耦架构

本项目提出的 **A* 双层解耦方案**，通过“**物理网卡 /1 覆盖压制 + 应用层域名 PAC 拦截 + Winsock 进程级出隧**”的组合拳，彻底优雅地解决了上述所有难题：

```text
                     【全系统出网网络请求】
                                │
          ┌─────────────────────┴─────────────────────┐
          ▼                                           ▼
   【未指定域名的普通流量】                       【目标海外域名（如 ChatGPT）】
   （微信/钉钉/B站/百度/系统后台）                     （匹配 *.openai.com, *.chatgpt.com 等）
          │                                           │
          │ 命中物理网卡 /1 压制路由                    │ 命中浏览器 PAC 脚本
          │                                           ▼
          ▼                              【本地轻量微代理 127.0.0.1:10811】
   【物理网卡 WLAN 满速直连】                         │ 1. 使用隧道内 DNS (10.240.x.x) 直解
   （不消耗 VPN 流量，无风控）                        │ 2. 套接字设置 IP_UNICAST_IF = VPN 网卡
                                                      ▼
                                                【VPN 虚拟网卡出海】
```

### 1. 物理底座：物理网卡易失性 `def1` 覆盖（解决普通流量回落）
不删除、不破坏 Windows 生成的 VPN 默认路由，而是在物理网卡上添加两条掩码为 `/1` 的易失路由：
- `0.0.0.0/1 -> 物理网卡网关`
- `128.0.0.0/1 -> 物理网卡网关`

**核心原理**：依据 TCP/IP 路由表的最长掩码匹配原则（Longest Prefix Match, LPM），1 位前缀（`/1`）匹配度天然高于 0 位前缀（`/0`）。因此所有普通流量在寻找路由时均会优先命中这两条路由，从而强制从物理网卡直连出网，彻底规避了 VPN 的 On-link 特权！

### 2. 精准拦截：浏览器级 PAC 脚本分流（解决 IP 漂移与微服务漏网）
在浏览器或系统网络设置中配置 PAC（Proxy Auto-Configuration）：
```javascript
function FindProxyForURL(url, host) {
    if (isPlainHostName(host) || host === 'localhost' || host === '127.0.0.1') return 'DIRECT';
    if (shExpMatch(host, "*.chatgpt.com") || host === "chatgpt.com" ||
        shExpMatch(host, "*.openai.com") || host === "openai.com" ||
        shExpMatch(host, "*.oaistatic.com") || shExpMatch(host, "*.oaiusercontent.com")) {
        return 'PROXY 127.0.0.1:10811';
    }
    return 'DIRECT';
}
```
**核心优势**：直接在应用层按照通配域名拦截，即使底层 Anycast IP 每秒漂移、即使微服务域名新增上百个，只要主域通配即可全覆盖。未匹配的域名直接返回 `DIRECT`，走底座的物理网卡直连。

### 3. 套接字出隧：Winsock 原生 `IP_UNICAST_IF` 进程级绑定（解决入隧与免提权）
微代理在向海外服务器发起连接时，无需任何驱动，通过 Windows 原生 Winsock 套接字选项直接指定出口网卡接口索引（Interface Index）：
```python
import socket
import struct

# IP_UNICAST_IF 在 Windows 上的 option 值为 31 (IPPROTO_IP 层)
IP_UNICAST_IF = 31

s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# 强制指定该 socket 的出站网卡为 VPN 虚拟网卡索引 (例如 ifIndex = 51)
s.setsockopt(socket.IPPROTO_IP, IP_UNICAST_IF, struct.pack("!I", vpn_ifindex))
s.connect((remote_ip, remote_port))
```
**核心优势**：
- 因为 VPN 适配器上原本的 `0.0.0.0/0` 始终完好无损，当套接字被限定在 VPN 接口内查找路由时，直接精准命中默认路由出隧！
- **全程零 `/32` 路由增删**；
- **微代理无需管理员提权（纯非提权用户态权限即可稳定运行）**。

### 4. 隧道内防污染 DNS 直解
微代理在发起 TCP 握手前，直接向 VPN 网卡分配的内部 DNS 服务器（如 `10.240.x.x`）发送标准 UDP DNS 请求解析真实 IP，彻底根绝国内公共 DNS 返回虚假污染 IP（如 `199.96.63.75`）的问题。

---

## 🛡️ 工业级安全与自愈保证（Zero-Damage Principles）

1. **纯易失状态（Volatile State Only）**：
   - 全程绝不修改 Windows 系统注册表中的 VPN 连接 Profile；
   - 物理网卡的 `def1` 路由仅存在于内存储存，VPN 断开或脚本退出时自动清理；
2. **看门狗安全回滚（Watchdog Rollback）**：
   - 守护进程通过心跳机制看护，若主程序发生异常崩溃或强杀，看门狗在超时后自动清除 `/1` 路由；
3. **秒级无缝降级**：
   - 分流服务在停止或重置后，网络会在 1 秒内无感平稳回退到纯净的原生全局 VPN 状态，**绝不发生断网事故**。

---

## 📂 项目结构

```text
win-vpn-split/
├── README.md               # 本文档：架构解析与实测定论
├── LICENSE                 # MIT 开源许可证
├── .gitignore              # Git 忽略文件
├── def1_manager.py         # 物理网卡 def1 (0.0.0.0/1) 覆盖路由生命周期管理器
└── micro_proxy_demo.py     # 纯标准库实现的 IP_UNICAST_IF 微代理与 PAC 演示
```

---

## 🚀 快速上手与验证

### 环境要求
- Windows 10 / Windows 11
- 已建立并连接的 Windows 原生 VPN（L2TP/IPsec、IKEv2 等）
- Python 3.8+ (仅使用内置标准库，无需 `pip install` 任何第三方依赖)

### 步骤 1：查询网络接口索引
在 PowerShell 中运行：
```powershell
Get-NetIPInterface -AddressFamily IPv4 | Select-Object ifIndex, InterfaceAlias
```
记录下你的物理网卡（如 `WLAN` 或 `以太网`）与 VPN 网卡（如 `VPN1`）的 `ifIndex`。

### 步骤 2：启动物理网卡覆盖压制（管理员权限）
```bash
python def1_manager.py --action add --interface WLAN
```

### 步骤 3：启动本地微代理
```bash
python micro_proxy_demo.py --vpn-index <VPN网卡索引> --dns-server <VPN内网DNS> --port 10811
```

### 步骤 4：配置浏览器
在 Edge / Chrome 浏览器或 Windows 系统代理设置中，填入 PAC 脚本或直接通过 SwitchyOmega 插件将目标域名指向 `127.0.0.1:10811`。

---

## 📄 许可证

本项目采用 [MIT License](LICENSE) 开源许可证。欢迎自由探讨、引用与扩展。
