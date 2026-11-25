# ARCH 热点公开版

ARCH 热点公开版是面向 Raspberry Pi / SX1255 多模热点设备的系统安装与 Web 管理源码树。

- 版本：`1.0.170`
- 通道：`public`
- OTA：`https://hljrsa.cn:50113/releases/ota/arch-hotspot/public/current.json`
- 许可：GNU GPL v3.0 or later

本仓库来自公开版发布包，但已做安全脱敏，不能与线上二进制发布包逐字节等同。

## 目录

```text
etc/     系统服务、无线电网关和默认配置
opt/     Web 控制台与 FMO 桥接代码
usr/     安装、升级、远程维护和硬件管理脚本
install.sh
build_upstream.sh
DISCLAIMER.md
```

## 安全脱敏

- 不包含共享 SSH 隧道私钥 `arch-tunnel.key`
- 不包含固定远程调测口令
- 安装时在 `/etc/mmdvm/remote-debug.secret` 生成每台设备独立的 256-bit 随机口令
- 构建和运行所需的真实隧道私钥必须由设备所有者自行提供并配置服务端授权

部署说明见 `docs/PROVISIONING.md`。

## 构建与检查

```bash
bash -n install.sh
python3 -m py_compile $(find opt usr -type f -name '*.py')
```

公开版由内测版删除 TETRA/TDR 相关组件后生成。完整打包规范不在本仓库自动执行。

## 许可与第三方组件

本仓库按 GPL v3.0 或更高版本发布。MMDVM 系列、ircDDBGateway、SoapySX、liquid-dsp 等第三方组件的版权和许可证归各自上游所有，详见 `THIRD_PARTY_NOTICES.md`。

## 项目主页

https://arch.hljrsa.cn