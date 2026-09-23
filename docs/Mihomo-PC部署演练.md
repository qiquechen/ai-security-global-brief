# Mihomo Windows PC 部署演练

本方案只代理采集器显式发出的 HTTP/SOCKS 请求，不开启系统 TUN，不改变浏览器、邮件、DeepSeek 或 Windows 的全局网络。

## 1. 安装并进行无订阅冒烟测试

在项目根目录打开 PowerShell：

```powershell
.\scripts\install_mihomo_pc.ps1
.\scripts\start_mihomo_pc.ps1 -Smoke
.\scripts\test_mihomo_pc.ps1
.\scripts\stop_mihomo_pc.ps1
```

二进制、日志、PID 和真实配置位于 `runtime/mihomo/`，该目录已被 Git 忽略。

## 2. 填写主备订阅

编辑 `runtime/mihomo/config.yaml`：

1. 把 `primary-provider.url` 替换为主订阅 URL。
2. 把 `backup-provider.url` 替换为备用订阅 URL。
3. 把 `secret` 替换为本机随机长密码。
4. 不要把该文件、订阅 URL 或控制器密码提交到 Git。

主备应来自不同上游服务；同一订阅中的两个节点不能抵御订阅服务整体故障。

## 3. 启动真实配置并验证

```powershell
.\scripts\start_mihomo_pc.ps1
.\scripts\test_mihomo_pc.ps1
```

确认通过后在项目 `.env` 中设置：

```dotenv
PROXY=
PROXY_PRIMARY=http://127.0.0.1:17890
PROXY_BACKUP=
INGEST_CONNECTIVITY_CHECK=true
INGEST_CONNECTIVITY_TEST_URL=https://www.gstatic.com/generate_204
```

Mihomo 内部已通过 `PRIMARY-AUTO`、`BACKUP-AUTO` 和 `OUTBOUND` 完成节点测速及主备切换，因此项目只需要一个稳定的本地入口。`PROXY_BACKUP` 保留给未来第二个独立代理进程，不应填写同一 Mihomo 的另一个等价端口。

验证项目线路：

```powershell
.\.venv\Scripts\python.exe -m ai_digest.ingest.run --validate-only
.\.venv\Scripts\python.exe scripts\probe_lnc.py
```

## 4. 停止与日志

```powershell
.\scripts\stop_mihomo_pc.ps1
Get-Content .\runtime\mihomo\mihomo.stderr.log -Tail 100
```

当前脚本用于个人 PC 演练。服务器正式部署时，应将 Mihomo 注册为随系统启动、异常自动重启的系统服务，并继续仅监听 `127.0.0.1`。
