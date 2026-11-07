# 对应微信客户端的备用资源

这里保存与 `adapter_wc` 原生 profile 对应的本地分析样本和离线安装包，便于后续复核函数、重新导入 Ghidra 或恢复同版客户端。

| 平台 | 版本 | 资源与核验记录 |
| --- | --- | --- |
| Linux x86_64 / Debian amd64 | 4.1.13.26 | [版本说明](linux-x86_64/4.1.13.26/README.md)、[清单](linux-x86_64/4.1.13.26/manifest.json)、[SHA256SUMS](linux-x86_64/4.1.13.26/SHA256SUMS) |

目录布局：

```text
resources/wechat/linux-x86_64/4.1.13.26/
  README.md
  manifest.json
  SHA256SUMS
  binary/wechat                        Linux ELF 主程序，本地备用
  installer/WeChatLinux_x86_64.deb       原始安装包，本地备用
```

文档、manifest 和校验清单随源码保存；`binary/`、`installer/` 被 Git 忽略，不进入源码仓库、Python wheel 或容器镜像（安装包只在构建时挂载）。克隆仓库只会获得清单，实际文件需另外复制这份资源目录。程序不会自动执行、安装或替换这些资源，运行时仍检查实际微信进程和 profile。

主程序约 173 MiB，原始 deb 约 221 MiB，故作为本地独立资源保存。原始 deb 另附在 [GitHub Release `wechat-4.1.13.26`](https://github.com/yz68ac/message_bot/releases/tag/wechat-4.1.13.26)，构建主机按 [README](../../README.md#0-准备代码和微信安装包) 下载到 `installer/` 并用清单复核；主程序单文件仅供分析，未上传。不要只把校验清单误当作已附带二进制。

来源是用户此前提供的 deb；2026-11-07 从腾讯官方地址重新下载，SHA-256 与此一致。资源不包含账号、登录状态、数据库、聊天或密钥。适配器的技术背景在独立库的 `docs/` 中；机器人集成见 [更新微信适配库](../../README.md#更新微信适配库)。
