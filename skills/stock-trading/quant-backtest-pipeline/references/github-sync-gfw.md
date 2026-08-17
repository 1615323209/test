# GitHub 同步实战（GFW 环境，2026-08）

场景：把量化数据/方案文档同步到 GitHub 私有/公开仓库。服务器（腾讯云）访问 GitHub 被墙，走 Windows（HX-C1000）中转。

## 认证：fine-grained token 的坑 vs classic token

**fine-grained token（github_pat_ 开头）极易踩坑**，症状与根因：
- `git clone/push` 报 `403 Write access to repository not granted`（Git 协议对未授权私有仓库返回 403 而非 404，防信息泄露）
- API `/repos/{user}/{repo}` 返回 404，`/user/repos` 返回空列表 —— token 的 Repository access 没勾选该仓库
- API 写文件报 `403 Resource not accessible by personal access token` —— Contents 权限是 Read-only
- **矛盾现象**：`/user/repos` 返回的 `permissions.push=True` 是"用户身份理论权限"，不是 token 实际授权，别被它误导

**必须同时配好两处**：Repository access 勾选仓库 + Permissions → Contents = Read and write。

**推荐直接用 classic token（ghp_ 开头）**：Generate new token (classic) → 勾选 `repo` 大类 → 一次拥有全部仓库读写权限，无需逐项配置。诊断流程：API 能 `/user` 但 `/user/repos` 空 → token 仓库访问没配；git 403 + API 404 → 仓库不存在或未授权。

## GFW 干扰：443 端口 SNI 阻断

**症状**：`ping github.com` 通（20.205.243.166 等），但 HTTPS 443 连不上（`Recv failure: Connection was reset` / `Failed to connect to github.com port 443`）——ping 走 ICMP 不受 SNI 阻断，TCP/TLS 握手被 GFW 对 github.com 域名的 SNI 干扰。

**解法：hosts 指向美国 IP**（新加坡 IP 20.205.x 被干扰，美国 140.82.112.x 可连）：
1. 先测可用 IP：socket 连各 IP 的 443 端口，挑通的
2. 追加到 `C:\Windows\System32\drivers\etc\hosts`（需管理员）：
   ```
   140.82.112.3 github.com
   140.82.112.3 api.github.com
   140.82.112.3 codeload.github.com
   140.82.112.3 objects.githubusercontent.com
   ```
3. `ipconfig /flushdns` 刷新 DNS 缓存
4. 验证：Python `socket.gethostbyname('github.com')` 应返回 140.82.112.3
5. 注意：`nslookup` 走 DNS 服务器**不读 hosts**，别用它验证

**⚠️ IP 稳定性（2026-08 实测）**：140.82.112.3 **不稳定**——同一 IP 第一次 connect OK、第二次 TimeoutError（时通时断），push 反复 `Connection was reset`。**140.82.113.3 稳定**——改 hosts 指向 113.3 后 push 一次成功。教训：hosts 失效时先测多个美国 IP（140.82.112.3 / 113.3 / 114.3 / 116.3 / 121.3），选稳定的写进去，不要死磕一个 IP 无限重试。

**push 重试模式**：GFW 干扰是间歇性的，push 失败先重试 2-3 次（间隔 10-30s），仍失败再换 hosts IP。实测同一参数组合第 5 次尝试成功过（exit=128 数次后 0）。

## 大文件传输：全部通道实测（2026-08，结论已更新）

**SSH 反向隧道 scp 传大文件最不可靠**：3.3GB 单文件传 10-20 分钟卡死（连接被重置），中断后文件 0 字节/部分字节，卡死时 scp 进程不退（需 kill）。**分块传输（split 200M）实测也失败**——单块 200MB 600 秒超时（隧道带宽 <0.3MB/s，且 GFW 持续干扰）。**再切 20MB 小块仍全失败**——隧道能承载小命令（echo/dir 通）但 SFTP 数据通道传输 >几 MB 就被掐。

**Windows 直连 Linux 公网 IP（scp 直连 22）也不可靠**：ssh 命令正常（echo OK、能 stat），但 scp 数据传输间歇性卡死——首次部分成功传了 535MB，之后整传、分块全部卡 0 字节。**GFW 对 SSH 长连接的数据传输（无论隧道还是直连）间歇性干扰**，ssh 命令能通不代表 scp 数据能通。

**HTTP 通道全被腾讯云安全组屏蔽**：公网访问 80（被 Caddy 占用）/443/8000/8080 全部超时，安全组只开 22 端口。SSH 端口转发（Windows `ssh -N -L 8001:localhost:8000 ubuntu@...`）+ 本地 HTTP 下载：部分可行（22MB 后断），且 python http.server 不支持 Range 断点续传（curl -C - 报 33）。

**✅ 最终务实方案（已验证）**：
1. **不传 Windows**：回测/IC 分析直接在 Linux 跑——回测按年 scan+collect 内存可控（2GB+6GB swap 够），先 `polars` 提取回测精简列 `factor_bt.parquet`（16 列 450MB 替代 3.3GB）大幅提速
2. 确实要传时：小块（<20MB）或等网络窗口，成功率低；或让用户在本地（非 GFW 环境）中转
3. Windows 侧密钥 `C:\Users\Administrator\.ssh\id_ed25519` 能登录 Linux（authorized_keys 含 `hermes-windows`），此链路本身通，只是数据传输被干扰——别在传输上无限重试（用户偏好：试 2-3 次不行就换方案）

## 环境信息

- Windows 侧：HX-C1000，git 2.54 已装，无 gh CLI；RTX A4000 16GB 显存 + 16GB 内存
- Windows→Linux 密钥：`C:\Users\Administrator\.ssh\id_ed25519`（pub 注释 hermes-windows，已加入 Linux authorized_keys）
- Linux→Windows 密钥：`/home/ubuntu/.ssh/desktop_key`（反向隧道 2222 用）
- git 认证：`git config --global credential.helper store` + `%USERPROFILE%\.git-credentials` 存 `https://user:token@github.com`；或 push 时 URL 内嵌 token `https://user:token@github.com/...`
- ⚠️ Windows 上 `git config credential.helper store` 可能被 Git Credential Manager 覆盖（wincredman），用 `git -c credential.helper= push` 强制绕过缓存
- 修改 hosts 后用 Python 写（open 'a'）比 bat 可靠（bat 在 SSH 下 LF 行尾会整文件串行执行报错）
