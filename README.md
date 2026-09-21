# 快手合规直播助手

这是一个 Windows/macOS 本地桌面 MVP，用于“真人在场 + 预录素材辅助”的直播制作流程。它不是快手官方客户端，也不提供账号登录、推流密钥获取、无人值守挂机或规避平台审核功能。

## 能做什么

- 多选视频文件、递归扫描文件夹、去重、排序和移除素材。
- 用 FFmpeg 将 Windows DirectShow 或 macOS AVFoundation 摄像头画面作为主画面，把预录素材缩略叠加在右下角。
- 默认叠加“部分画面为预录素材 / 主播在线回复”提示。
- 在岗确认倒计时；超过设定时间没有确认，自动终止 FFmpeg。
- 手动记录观众问题和已回复动作；开启互动看护后，长时间没有记录也会自动停推。
- 摄像头预览、FFmpeg 日志和不含 RTMP 密钥的 JSONL 审计记录。

## 使用前提

1. Windows 10/11 或 macOS 13+、Python 3.11+。
2. 安装 FFmpeg，并确保 `ffmpeg`、`ffprobe` 在 PATH，或放到程序目录的 `ffmpeg/bin` 目录。
3. 安装依赖：

   ```powershell
   py -m pip install -r requirements.txt
   ```

4. 从快手直播伴侣或官方控制台取得本场直播的 RTMP 地址和推流码，拼成完整的 `rtmp://` 或 `rtmps://` 地址。程序不会自动登录或抓取密钥。
5. 启动：

   ```powershell
   py app.py
   ```

也可以双击/执行 `run.ps1`（Windows）。首次启动会尝试用 FFmpeg 检测摄像头：Windows 使用 DirectShow，macOS 使用 AVFoundation。OpenCV 预览默认打开设备 0。若检测到的名称与预览设备不同，可在输入框手动填写实际设备名或索引。macOS 首次启动时请在系统设置中允许摄像头和麦克风权限。

## 打包成便携 ZIP

在有 Python 的 Windows x64 电脑上执行：

```powershell
.\build_portable.ps1
```

脚本会安装 PyInstaller，把 Python/Tkinter/OpenCV/Pillow 程序和 FFmpeg 一起打包成 `release\\KuaishouLiveAssistant_Portable_*.zip`。解压后双击 `KuaishouLiveAssistant.exe` 即可运行，不要求目标电脑安装 Python。

如果脚本找不到 FFmpeg，可显式指定包含 `ffmpeg.exe` 和 `ffprobe.exe` 的 `bin` 文件夹：

```powershell
.\build_portable.ps1 -FfmpegBin 'D:\\tools\\ffmpeg\\bin'
```

便携包采用 `onedir` 结构，不能只单独拿走 EXE；必须整体复制包含 `ffmpeg\\bin` 的文件夹。建议将整个 ZIP 解压到不含特殊权限限制的目录，例如 `D:\\KuaishouLiveAssistant`。

### macOS

macOS `.app` 必须在 macOS 上构建。准备一个包含 `ffmpeg` 和 `ffprobe` 的目录（例如 Homebrew 的 `$(brew --prefix)/bin`），然后运行：

```bash
chmod +x build_mac.sh
FFMPEG_BIN="$(brew --prefix)/bin" ./build_mac.sh
```

脚本会生成 `release/KuaishouLiveAssistant_macOS_<架构>.zip`。`ARCH=arm64` 用于 Apple Silicon，`ARCH=x86_64` 用于 Intel Mac。解压后将整个 `KuaishouLiveAssistant.app` 拖到“应用程序”即可；首次打开若被 Gatekeeper 拦截，请在“系统设置 → 隐私与安全性”中手动允许。

仓库还提供 `.github/workflows/build-macos.yml`，在 GitHub Actions 的 macOS runner 上构建两个架构并上传 ZIP artifact。

## 开播流程

1. 添加至少一个有合法使用权的视频素材。
2. 选择摄像头，必要时填写麦克风名称、分辨率和码率。
3. 粘贴本场 RTMP 地址，确认两项声明，并保持“预录素材提示”开启。
4. 点击“开始合成并推流”。推流期间定时点击“确认我在场”，收到问题后在“互动记录”中记录已回复。
5. 任何时候可点击“停止推流”。程序关闭、摄像头不可用或看护超时，也会停止当前 FFmpeg 进程。

## 输出与隐私

- 审计总日志：Windows 为 `data/audit.jsonl`；macOS 为 `~/Library/Application Support/KuaishouLiveAssistant/audit.jsonl`。
- 每次会话：`data/sessions/<session_id>/playlist.txt` 和 `session.json`。
- RTMP 地址只用于当前进程命令行，不写入审计日志和会话摘要。仍应把本机日志目录视为敏感数据。

## 参考项目

- [sky22333/zhibo](https://github.com/sky22333/zhibo)：参考了 FFmpeg concat 素材队列和进程重启思路，但本项目不采用其 7×24 无人循环模式。
- [withsalt/BilibiliLiveTools](https://github.com/withsalt/BilibiliLiveTools)：参考了 FFmpeg 配置、设备枚举和日志组织；其 B 站登录/API 代码不适用于快手，未移植。
- [KuwiNet/Live](https://github.com/KuwiNet/Live)：参考了基础 FFmpeg 参数；其 screen/无人值守脚本不在本项目范围内。

平台检测规则未公开，任何滤镜、变声、循环剪辑等做法都不能保证改变平台判定。请以快手最新直播、电商和素材版权规则为准。
