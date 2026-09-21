from __future__ import annotations

import argparse
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import BooleanVar, IntVar, StringVar, Tk, filedialog, messagebox
from tkinter import END, BOTH, LEFT, RIGHT, X, Y
from tkinter import ttk

try:
    import cv2
except ImportError:  # Camera preview is optional at import time.
    cv2 = None



def runtime_dir() -> Path:
    """Return the folder that should travel with the executable."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


APP_DIR = runtime_dir()
if getattr(sys, "frozen", False) and sys.platform == "darwin":
    RESOURCE_DIR = Path(sys.executable).resolve().parents[1] / "Resources"
    DATA_DIR = Path.home() / "Library" / "Application Support" / "KuaishouLiveAssistant"
else:
    RESOURCE_DIR = APP_DIR
    DATA_DIR = APP_DIR / "data"
AUDIT_PATH = DATA_DIR / "audit.jsonl"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".avi", ".flv", ".webm", ".m4v"}


@dataclass
class Material:
    path: str
    duration: float | None = None
    width: int | None = None
    height: int | None = None
    has_audio: bool = False


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def find_binary(name: str) -> str | None:
    candidates = []
    env_name = name.upper() + "_PATH"
    if os.environ.get(env_name):
        candidates.append(Path(os.environ[env_name]))
    suffix = ".exe" if os.name == "nt" else ""
    candidates.extend(
        [
            RESOURCE_DIR / "ffmpeg" / "bin" / f"{name}{suffix}",
            APP_DIR / "ffmpeg" / "bin" / f"{name}{suffix}",
            APP_DIR / f"{name}{suffix}",
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name) or shutil.which(name + suffix)


def runtime_env() -> dict[str, str]:
    """Environment for bundled helper binaries, especially macOS dylibs."""
    env = os.environ.copy()
    if sys.platform == "darwin":
        lib_dir = RESOURCE_DIR / "ffmpeg" / "lib"
        if lib_dir.is_dir():
            existing = env.get("DYLD_LIBRARY_PATH", "")
            env["DYLD_LIBRARY_PATH"] = os.pathsep.join(
                part for part in (str(lib_dir), existing) if part
            )
    return env


def current_platform() -> str:
    """Return the capture backend name used by FFmpeg on this host."""
    if os.name == "nt":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "other"


def capture_backend(platform: str | None = None) -> str:
    platform = platform or current_platform()
    return {"windows": "dshow", "macos": "avfoundation"}.get(platform, "")


def probe_media(path: str, ffprobe: str | None = None) -> Material:
    material = Material(path=os.path.abspath(path))
    ffprobe = ffprobe or find_binary("ffprobe")
    if not ffprobe:
        return material
    try:
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-show_entries",
                "format=duration:stream=index,codec_type,width,height",
                "-of",
                "json",
                path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=True,
            env=runtime_env(),
        )
        payload = json.loads(result.stdout or "{}")
        streams = payload.get("streams", [])
        video = next((s for s in streams if s.get("codec_type") == "video"), None)
        material.has_audio = any(s.get("codec_type") == "audio" for s in streams)
        if video:
            material.width = int(video["width"]) if video.get("width") else None
            material.height = int(video["height"]) if video.get("height") else None
        duration = payload.get("format", {}).get("duration")
        material.duration = float(duration) if duration else None
    except (OSError, ValueError, KeyError, subprocess.SubprocessError, json.JSONDecodeError):
        pass
    return material


def collect_video_files(folder: str) -> list[str]:
    root = Path(folder)
    return sorted(
        (str(p.resolve()) for p in root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS),
        key=str.lower,
    )


def concat_quote(path: str) -> str:
    # concat demuxer paths use POSIX separators and single-quoted values.
    normalized = os.path.abspath(path).replace("\\", "/")
    return normalized.replace("'", "'\\''")


def write_concat_file(materials: list[Material], destination: str) -> None:
    Path(destination).parent.mkdir(parents=True, exist_ok=True)
    with open(destination, "w", encoding="utf-8", newline="\n") as handle:
        for material in materials:
            handle.write(f"file '{concat_quote(material.path)}'\n")


def parse_dshow_devices(output: str) -> list[str]:
    devices: list[str] = []
    for line in output.splitlines():
        if "(video)" not in line.lower():
            continue
        matches = re.findall(r'"([^"\r\n]+)"', line)
        if matches:
            name = matches[-1]
            if name not in devices:
                devices.append(name)
    return devices


def parse_avfoundation_devices(output: str) -> list[str]:
    """Parse FFmpeg's AVFoundation video-device section."""
    devices: list[str] = []
    in_video = False
    for line in output.splitlines():
        lowered = line.lower()
        if "avfoundation video devices" in lowered:
            in_video = True
            continue
        if "avfoundation audio devices" in lowered:
            in_video = False
            continue
        if not in_video:
            continue
        match = re.search(r"\[\s*\d+\s*\]\s+(.+?)\s*$", line)
        if match:
            name = match.group(1).strip()
            if name and name not in devices:
                devices.append(name)
    return devices


def preferred_font_path(platform: str | None = None) -> str | None:
    platform = platform or current_platform()
    candidates = {
        "windows": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyh.ttf"],
        "macos": [
            "/System/Library/Fonts/PingFang.ttc",
            "/System/Library/Fonts/Hiragino Sans GB.ttc",
            "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        ],
    }.get(platform, [])
    return next((path for path in candidates if Path(path).is_file()), None)


def escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", r"\'")


def build_filter(font_path: str | None) -> str:
    text = "部分画面为预录素材 / 主播在线回复"
    if font_path:
        font = f":fontfile='{escape_filter_path(font_path)}'"
    else:
        font = ""
    disclosure = (
        f"drawtext=text='{text}'{font}:fontcolor=white:fontsize=24:"
        "box=1:boxcolor=black@0.60:boxborderw=10:x=24:y=24"
    )
    return (
        "[1:v]scale=640:360:force_original_aspect_ratio=decrease,"
        "pad=640:360:(ow-iw)/2:(oh-ih)/2:color=black@0[material];"
        "[0:v][material]overlay=W-w-24:H-h-24:format=auto," + disclosure + "[v]"
    )


def build_ffmpeg_command(
    ffmpeg: str,
    camera: str,
    microphone: str,
    playlist: str,
    rtmp_url: str,
    width: int,
    height: int,
    bitrate_kbps: int,
    font_path: str | None,
    platform: str | None = None,
) -> list[str]:
    platform = platform or current_platform()
    if platform == "macos":
        # AVFoundation accepts numeric indices or device names as video:audio.
        camera_input = f"{camera}:{microphone.strip() or 'none'}"
    else:
        camera_input = f"video={camera}"
        if microphone.strip():
            camera_input += f":audio={microphone.strip()}"
    backend = capture_backend(platform)
    input_args = ["-f", backend] if backend else []
    if platform == "macos":
        input_args += ["-framerate", "30", "-video_size", f"{width}x{height}"]
    else:
        input_args += ["-video_size", f"{width}x{height}", "-framerate", "30"]
    return [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "warning",
        "-nostdin",
        *input_args,
        "-i",
        camera_input,
        "-re",
        "-stream_loop",
        "-1",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        playlist,
        "-filter_complex",
        build_filter(font_path),
        "-map",
        "[v]",
        "-map",
        ("0:a?" if microphone.strip() else "1:a?"),
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-tune",
        "zerolatency",
        "-pix_fmt",
        "yuv420p",
        "-r",
        "30",
        "-g",
        "60",
        "-b:v",
        f"{bitrate_kbps}k",
        "-maxrate",
        f"{bitrate_kbps}k",
        "-bufsize",
        f"{bitrate_kbps * 2}k",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "44100",
        "-f",
        "flv",
        rtmp_url,
    ]


class AuditLog:
    def __init__(self, path: Path = AUDIT_PATH):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()

    def write(self, event: str, **fields) -> None:
        record = {"time": utc_now(), "event": event, **fields}
        with self._lock:
            with self.path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")


class LiveAssistant:
    def __init__(self, root: Tk):
        self.root = root
        self.root.title("快手合规直播助手")
        self.root.geometry("1180x760")
        self.root.minsize(980, 650)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.ffmpeg = find_binary("ffmpeg")
        self.ffprobe = find_binary("ffprobe")
        self.audit = AuditLog()
        self.materials: list[Material] = []
        self.process: subprocess.Popen | None = None
        self.session_id: str | None = None
        self.session_started: float | None = None
        self.session_dir: Path | None = None
        self.last_presence: float | None = None
        self.last_interaction: float | None = None
        self.interaction_count = 0
        self.log_queue: queue.Queue[str] = queue.Queue()
        self.capture = None
        self.preview_photo = None
        self.camera_devices: list[str] = []
        self.platform = current_platform()
        self.capture_api = capture_backend(self.platform)

        self.camera_var = StringVar(value="")
        self.microphone_var = StringVar(value="")
        self.rtmp_var = StringVar(value="")
        self.rtmp_visible = BooleanVar(value=False)
        self.resolution_var = StringVar(value="1280x720")
        self.bitrate_var = IntVar(value=2500)
        self.presence_timeout_var = IntVar(value=90)
        self.interaction_timeout_var = IntVar(value=600)
        self.strict_interaction_var = BooleanVar(value=True)
        self.disclosure_var = BooleanVar(value=True)
        self.rights_var = BooleanVar(value=False)
        self.presence_ack_var = BooleanVar(value=False)
        self.status_var = StringVar(value="未开播")
        self.presence_status_var = StringVar(value="尚未确认在场")
        self.interaction_status_var = StringVar(value="尚未记录互动")
        self.camera_status_var = StringVar(value="摄像头未检测")
        self.source_status_var = StringVar(value="素材 0 个")
        self.prompt_var = StringVar(value="")

        self._build_ui()
        self.root.after(100, self._drain_logs)
        self.root.after(250, self._tick)
        self.root.after(600, self._update_preview)
        self._detect_cameras()

    def _build_ui(self) -> None:
        style = ttk.Style(self.root)
        try:
            style.theme_use("vista")
        except Exception:
            pass
        style.configure("Title.TLabel", font=("Segoe UI", 18, "bold"))
        style.configure("Muted.TLabel", foreground="#5f6b7a")
        style.configure("Danger.TLabel", foreground="#a12622")
        style.configure("Good.TLabel", foreground="#18794e")

        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill=BOTH, expand=True)
        ttk.Label(outer, text="快手合规直播助手", style="Title.TLabel").pack(anchor="w")
        ttk.Label(
            outer,
            text="预录素材仅作为画面辅助，必须保持摄像头开启、本人在场并负责实时互动。",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(2, 12))

        main = ttk.Panedwindow(outer, orient="horizontal")
        main.pack(fill=BOTH, expand=True)
        left = ttk.Frame(main, padding=(0, 0, 10, 0))
        right = ttk.Frame(main, padding=(10, 0, 0, 0))
        main.add(left, weight=3)
        main.add(right, weight=2)

        source_frame = ttk.LabelFrame(left, text="素材队列", padding=10)
        source_frame.pack(fill=BOTH, expand=True)
        self.source_list = ttk.Treeview(source_frame, columns=("name", "duration", "size", "audio"), show="headings", height=12)
        self.source_list.heading("name", text="文件")
        self.source_list.heading("duration", text="时长")
        self.source_list.heading("size", text="画面")
        self.source_list.heading("audio", text="音频")
        self.source_list.column("name", width=260, anchor="w")
        self.source_list.column("duration", width=86, anchor="center")
        self.source_list.column("size", width=100, anchor="center")
        self.source_list.column("audio", width=70, anchor="center")
        self.source_list.pack(side=LEFT, fill=BOTH, expand=True)
        scrollbar = ttk.Scrollbar(source_frame, orient="vertical", command=self.source_list.yview)
        scrollbar.pack(side=RIGHT, fill=Y)
        self.source_list.configure(yscrollcommand=scrollbar.set)
        self.source_list.bind("<Delete>", lambda _event: self._remove_selected())
        buttons = ttk.Frame(left)
        buttons.pack(fill=X, pady=(8, 4))
        ttk.Button(buttons, text="添加视频", command=self._add_files).pack(side=LEFT)
        ttk.Button(buttons, text="扫描文件夹", command=self._add_folder).pack(side=LEFT, padx=6)
        ttk.Button(buttons, text="移除", command=self._remove_selected).pack(side=LEFT)
        ttk.Button(buttons, text="清空", command=self._clear_materials).pack(side=LEFT, padx=6)
        ttk.Button(buttons, text="上移", command=lambda: self._move_selected(-1)).pack(side=RIGHT)
        ttk.Button(buttons, text="下移", command=lambda: self._move_selected(1)).pack(side=RIGHT, padx=(6, 0))
        ttk.Label(left, textvariable=self.source_status_var, style="Muted.TLabel").pack(anchor="w")

        preview_frame = ttk.LabelFrame(left, text="摄像头预览", padding=8)
        preview_frame.pack(fill=X, pady=(12, 0))
        self.preview_label = ttk.Label(preview_frame, text="正在等待摄像头画面", anchor="center")
        self.preview_label.configure(width=72)
        self.preview_label.pack(fill=X, ipady=55)
        ttk.Label(preview_frame, textvariable=self.camera_status_var, style="Muted.TLabel").pack(anchor="w", pady=(5, 0))

        config = ttk.LabelFrame(right, text="开播配置", padding=10)
        config.pack(fill=X)
        camera_hint = "选择或填写 AVFoundation 设备名/索引" if self.platform == "macos" else "选择或填写 DirectShow 设备名"
        self._labeled_entry(config, 0, "摄像头", self.camera_var, camera_hint)
        ttk.Button(config, text="检测设备", command=self._detect_cameras).grid(row=0, column=2, padx=(6, 0), sticky="ew")
        self._labeled_entry(config, 1, "麦克风(可选)", self.microphone_var, "留空则不从摄像头采集音频")
        self._labeled_rtmp(config)
        ttk.Label(config, text="分辨率").grid(row=3, column=0, sticky="w", pady=5)
        ttk.Combobox(config, textvariable=self.resolution_var, values=("1280x720", "1920x1080", "854x480"), state="readonly", width=14).grid(row=3, column=1, sticky="ew", pady=5)
        ttk.Label(config, text="码率(kbps)").grid(row=4, column=0, sticky="w", pady=5)
        ttk.Spinbox(config, from_=800, to=8000, increment=100, textvariable=self.bitrate_var, width=14).grid(row=4, column=1, sticky="ew", pady=5)
        config.columnconfigure(1, weight=1)

        guard = ttk.LabelFrame(right, text="在场与合规看护", padding=10)
        guard.pack(fill=X, pady=(12, 0))
        ttk.Checkbutton(guard, text="叠加“预录素材 / 主播在线回复”提示", variable=self.disclosure_var).pack(anchor="w")
        ttk.Checkbutton(guard, text="我确认素材有合法使用权，并会实时负责直播", variable=self.rights_var).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(guard, text="我确认本人现在就在电脑前", variable=self.presence_ack_var).pack(anchor="w", pady=(4, 0))
        ttk.Checkbutton(guard, text="互动超时自动停推", variable=self.strict_interaction_var).pack(anchor="w", pady=(4, 0))
        timeout_row = ttk.Frame(guard)
        timeout_row.pack(fill=X, pady=(8, 0))
        ttk.Label(timeout_row, text="在岗超时(s)").pack(side=LEFT)
        ttk.Spinbox(timeout_row, from_=30, to=1800, textvariable=self.presence_timeout_var, width=7).pack(side=LEFT, padx=(5, 14))
        ttk.Label(timeout_row, text="互动超时(s)").pack(side=LEFT)
        ttk.Spinbox(timeout_row, from_=60, to=7200, textvariable=self.interaction_timeout_var, width=7).pack(side=LEFT, padx=5)
        ttk.Button(guard, text="确认我在场", command=self._mark_presence).pack(fill=X, pady=(9, 0))
        ttk.Label(guard, textvariable=self.presence_status_var, style="Muted.TLabel").pack(anchor="w", pady=(4, 0))
        ttk.Label(guard, textvariable=self.interaction_status_var, style="Muted.TLabel").pack(anchor="w", pady=(2, 0))

        controls = ttk.Frame(right)
        controls.pack(fill=X, pady=(12, 0))
        ttk.Button(controls, text="开始合成并推流", command=self._start_stream).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(controls, text="停止推流", command=lambda: self._stop_stream("主播手动停止")).pack(side=LEFT, fill=X, expand=True, padx=(8, 0))
        ttk.Label(right, textvariable=self.status_var, style="Good.TLabel").pack(anchor="w", pady=(7, 0))

        interaction = ttk.LabelFrame(right, text="互动记录", padding=10)
        interaction.pack(fill=X, pady=(12, 0))
        ttk.Entry(interaction, textvariable=self.prompt_var).pack(side=LEFT, fill=X, expand=True)
        ttk.Button(interaction, text="记录已回复", command=self._record_interaction).pack(side=RIGHT, padx=(8, 0))

        log_frame = ttk.LabelFrame(right, text="运行日志", padding=8)
        log_frame.pack(fill=BOTH, expand=True, pady=(12, 0))
        self.log_text = ttk.Treeview(log_frame, columns=("time", "message"), show="headings", height=8)
        self.log_text.heading("time", text="时间")
        self.log_text.heading("message", text="事件")
        self.log_text.column("time", width=80, anchor="center")
        self.log_text.column("message", width=280, anchor="w")
        self.log_text.pack(fill=BOTH, expand=True)

    def _labeled_entry(self, parent, row: int, label: str, variable: StringVar, placeholder: str) -> None:
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(parent, textvariable=variable, width=28)
        entry.grid(row=row, column=1, sticky="ew", pady=5)
        entry.insert(0, "")
        entry.configure(validate="none")
        entry.bind("<FocusIn>", lambda _event: None)
        ttk.Label(parent, text=placeholder, style="Muted.TLabel").grid(row=row, column=3, sticky="w", padx=(6, 0))

    def _labeled_rtmp(self, parent) -> None:
        ttk.Label(parent, text="RTMP 地址").grid(row=2, column=0, sticky="w", pady=5)
        self.rtmp_entry = ttk.Entry(parent, textvariable=self.rtmp_var, show="*", width=28)
        self.rtmp_entry.grid(row=2, column=1, sticky="ew", pady=5)
        ttk.Button(parent, text="显示", command=self._toggle_rtmp).grid(row=2, column=2, padx=(6, 0), sticky="ew")
        ttk.Label(parent, text="仅保存在内存，不写入日志", style="Muted.TLabel").grid(row=2, column=3, sticky="w", padx=(6, 0))

    def _toggle_rtmp(self) -> None:
        self.rtmp_visible.set(not self.rtmp_visible.get())
        self.rtmp_entry.configure(show="" if self.rtmp_visible.get() else "*")

    def _log(self, message: str) -> None:
        self.log_queue.put(message)

    def _drain_logs(self) -> None:
        while True:
            try:
                message = self.log_queue.get_nowait()
            except queue.Empty:
                break
            self.log_text.insert("", END, values=(time.strftime("%H:%M:%S"), message))
            children = self.log_text.get_children()
            if len(children) > 200:
                self.log_text.delete(children[0])
        self.root.after(100, self._drain_logs)

    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(title="选择视频素材", filetypes=[("视频", " ".join(f"*{e}" for e in VIDEO_EXTENSIONS)), ("所有文件", "*.*")])
        self._append_paths(paths)

    def _add_folder(self) -> None:
        folder = filedialog.askdirectory(title="选择素材文件夹")
        if folder:
            self._append_paths(collect_video_files(folder))

    def _append_paths(self, paths) -> None:
        known = {m.path.lower() for m in self.materials}
        added = 0
        for path in paths:
            absolute = os.path.abspath(path)
            if absolute.lower() in known or Path(absolute).suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            material = probe_media(absolute, self.ffprobe)
            self.materials.append(material)
            known.add(absolute.lower())
            added += 1
        self._refresh_materials()
        if added:
            self._log(f"已加入 {added} 个素材")

    def _refresh_materials(self) -> None:
        self.source_list.delete(*self.source_list.get_children())
        for index, material in enumerate(self.materials):
            duration = f"{material.duration:.1f}s" if material.duration is not None else "未知"
            size = f"{material.width}x{material.height}" if material.width and material.height else "未知"
            audio = "有" if material.has_audio else "无/未知"
            self.source_list.insert("", END, iid=str(index), values=(Path(material.path).name, duration, size, audio))
        self.source_status_var.set(f"素材 {len(self.materials)} 个")

    def _remove_selected(self) -> None:
        selected = sorted((int(i) for i in self.source_list.selection()), reverse=True)
        for index in selected:
            if 0 <= index < len(self.materials):
                self.materials.pop(index)
        self._refresh_materials()

    def _clear_materials(self) -> None:
        self.materials.clear()
        self._refresh_materials()

    def _move_selected(self, offset: int) -> None:
        selection = self.source_list.selection()
        if len(selection) != 1:
            return
        index = int(selection[0])
        target = index + offset
        if not (0 <= target < len(self.materials)):
            return
        self.materials[index], self.materials[target] = self.materials[target], self.materials[index]
        self._refresh_materials()
        self.source_list.selection_set(str(target))

    def _detect_cameras(self) -> None:
        if not self.ffmpeg:
            backend_name = "AVFoundation" if self.platform == "macos" else "DirectShow"
            self.camera_status_var.set(f"未找到 FFmpeg，无法检测 {backend_name} 设备")
            return
        try:
            result = subprocess.run(
                (
                    [self.ffmpeg, "-hide_banner", "-f", "avfoundation", "-list_devices", "true", "-i", ""]
                    if self.platform == "macos"
                    else [self.ffmpeg, "-hide_banner", "-list_devices", "true", "-f", "dshow", "-i", "dummy"]
                ),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                env=runtime_env(),
            )
            output = (result.stderr or "") + "\n" + (result.stdout or "")
            self.camera_devices = (
                parse_avfoundation_devices(output)
                if self.platform == "macos"
                else parse_dshow_devices(output)
            )
            if self.camera_devices:
                # AVFoundation opens devices by numeric index; keep the name in
                # the status while selecting the first detected index.
                self.camera_var.set("0" if self.platform == "macos" else self.camera_devices[0])
                self.camera_status_var.set(f"检测到 {len(self.camera_devices)} 个视频设备")
                self._log("已检测到摄像头设备")
            else:
                backend_name = "AVFoundation" if self.platform == "macos" else "DirectShow"
                self.camera_status_var.set(f"未检测到视频设备，可手动填写 {backend_name} 名称/索引")
        except (OSError, subprocess.SubprocessError) as exc:
            self.camera_status_var.set(f"设备检测失败: {exc}")

    def _ensure_preview_camera(self) -> bool:
        if cv2 is None:
            self.camera_status_var.set("未安装 opencv-python，无法打开摄像头")
            return False
        if self.capture is None or not self.capture.isOpened():
            if os.name == "nt":
                api = cv2.CAP_DSHOW
            elif self.platform == "macos":
                api = getattr(cv2, "CAP_AVFOUNDATION", 0)
            else:
                api = 0
            self.capture = cv2.VideoCapture(0, api)
        if self.capture is None or not self.capture.isOpened():
            self.camera_status_var.set("摄像头打开失败")
            return False
        ok, frame = self.capture.read()
        if not ok or frame is None:
            self.camera_status_var.set("摄像头没有返回画面")
            return False
        self.camera_status_var.set("摄像头在线")
        return True

    def _update_preview(self) -> None:
        if self.capture is not None and self.capture.isOpened() and cv2 is not None:
            ok, frame = self.capture.read()
            if ok and frame is not None:
                try:
                    from PIL import Image, ImageTk

                    frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                    image = Image.fromarray(frame)
                    image.thumbnail((600, 310))
                    self.preview_photo = ImageTk.PhotoImage(image)
                    self.preview_label.configure(image=self.preview_photo, text="")
                except ImportError:
                    self.preview_label.configure(text="已打开摄像头；安装 Pillow 可显示预览")
        self.root.after(120, self._update_preview)

    def _mark_presence(self) -> None:
        self.last_presence = time.monotonic()
        self.presence_status_var.set("在岗确认有效")
        self.audit.write("presence_confirmed", session_id=self.session_id)
        self._log("已记录主播在岗确认")

    def _record_interaction(self) -> None:
        prompt = self.prompt_var.get().strip()
        if not prompt:
            messagebox.showinfo("互动记录", "请先填写观众问题或互动摘要。")
            return
        self.last_interaction = time.monotonic()
        self.interaction_count += 1
        self.interaction_status_var.set(f"最近互动已记录 · 共 {self.interaction_count} 次")
        self.audit.write("interaction_recorded", session_id=self.session_id, prompt_length=len(prompt))
        self.prompt_var.set("")
        self._log("已记录一次观众互动回复")

    def _start_stream(self) -> None:
        if self.process and self.process.poll() is None:
            messagebox.showinfo("正在推流", "当前已有推流进程。")
            return
        if not self.ffmpeg:
            binary_name = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
            messagebox.showerror("缺少 FFmpeg", f"请安装 FFmpeg，或将 {binary_name} 放入程序目录的 ffmpeg/bin。")
            return
        if not self.materials:
            messagebox.showerror("缺少素材", "请先加入至少一个视频素材。")
            return
        if not self.camera_var.get().strip():
            backend_name = "AVFoundation" if self.platform == "macos" else "DirectShow"
            messagebox.showerror("缺少摄像头", f"请检测或填写 {backend_name} 摄像头名称/索引。")
            return
        if not self.rtmp_var.get().strip().lower().startswith(("rtmp://", "rtmps://")):
            messagebox.showerror("RTMP 地址无效", "请粘贴快手直播伴侣提供的 rtmp:// 或 rtmps:// 地址。")
            return
        if not self.disclosure_var.get():
            messagebox.showerror("必须披露素材", "此工具要求开启预录素材提示，避免把预录画面伪装成全程实时。")
            return
        if not self.rights_var.get() or not self.presence_ack_var.get():
            messagebox.showerror("确认未完成", "请确认素材授权，并确认本人在电脑前负责实时互动。")
            return
        if not self._ensure_preview_camera():
            messagebox.showerror("摄像头不可用", "没有检测到可用摄像头画面，程序不会启动推流。")
            return
        try:
            width, height = (int(part) for part in self.resolution_var.get().split("x", 1))
            bitrate = int(self.bitrate_var.get())
        except ValueError:
            messagebox.showerror("配置无效", "分辨率或码率格式不正确。")
            return

        self.session_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.session_dir = DATA_DIR / "sessions" / self.session_id
        self.session_dir.mkdir(parents=True, exist_ok=True)
        playlist = self.session_dir / "playlist.txt"
        write_concat_file(self.materials, str(playlist))
        font_path = preferred_font_path(self.platform)
        command = build_ffmpeg_command(
            self.ffmpeg,
            self.camera_var.get().strip(),
            self.microphone_var.get(),
            str(playlist),
            self.rtmp_var.get().strip(),
            width,
            height,
            bitrate,
            font_path,
            self.platform,
        )
        try:
            self.process = subprocess.Popen(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                env=runtime_env(),
            )
        except OSError as exc:
            self.process = None
            messagebox.showerror("启动失败", str(exc))
            return

        self.session_started = time.monotonic()
        self.last_presence = self.session_started
        self.last_interaction = self.session_started
        self.interaction_count = 0
        self.status_var.set("推流运行中 · 必须保持本人在场")
        self.presence_status_var.set("在岗确认有效")
        self.interaction_status_var.set("等待记录互动")
        self.audit.write(
            "stream_started",
            session_id=self.session_id,
            material_count=len(self.materials),
            materials=[Path(m.path).name for m in self.materials],
            camera=self.camera_var.get().strip(),
            microphone=bool(self.microphone_var.get().strip()),
            resolution=f"{width}x{height}",
            bitrate_kbps=bitrate,
        )
        self._log("推流进程已启动；推流地址不会写入日志")
        threading.Thread(target=self._read_process_output, daemon=True).start()

    def _read_process_output(self) -> None:
        process = self.process
        if process is None or process.stderr is None:
            return
        for line in process.stderr:
            cleaned = line.strip()
            if cleaned:
                self._log(cleaned[:240])
        code = process.wait()
        self.log_queue.put(f"FFmpeg 进程结束，退出码 {code}")
        if self.process is process:
            self.root.after(0, lambda: self._process_finished(code))

    def _process_finished(self, code: int) -> None:
        if self.process is None:
            return
        self.process = None
        if self.session_started is not None:
            self._write_session_summary("ffmpeg_exit", code)
        self.status_var.set(f"已停止 · FFmpeg 退出码 {code}")

    def _stop_stream(self, reason: str) -> None:
        process = self.process
        if process is None or process.poll() is not None:
            self.status_var.set("未开播")
            return
        self._log(reason)
        self.audit.write("stream_stop_requested", session_id=self.session_id, reason=reason)
        try:
            process.terminate()
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        finally:
            self.process = None
            self._write_session_summary(reason)
            self.status_var.set(f"已停止 · {reason}")

    def _write_session_summary(self, reason: str, exit_code: int | None = None) -> None:
        if not self.session_dir or not self.session_started:
            return
        summary = {
            "session_id": self.session_id,
            "started_at": utc_now(),
            "reason": reason,
            "exit_code": exit_code,
            "material_count": len(self.materials),
            "materials": [asdict(m) for m in self.materials],
            "interaction_count": self.interaction_count,
        }
        (self.session_dir / "session.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
        self.session_started = None

    def _tick(self) -> None:
        if self.process and self.process.poll() is None:
            now = time.monotonic()
            presence_age = now - (self.last_presence or now)
            presence_limit = max(30, int(self.presence_timeout_var.get()))
            if presence_age >= presence_limit:
                self._stop_stream("在岗确认超时，自动停推")
            else:
                self.presence_status_var.set(f"在岗确认剩余 {int(presence_limit - presence_age)} 秒")
                if self.strict_interaction_var.get():
                    interaction_age = now - (self.last_interaction or now)
                    interaction_limit = max(60, int(self.interaction_timeout_var.get()))
                    if interaction_age >= interaction_limit:
                        self._stop_stream("互动记录超时，自动停推")
                    else:
                        self.interaction_status_var.set(f"最近互动 {int(interaction_age)} 秒前 · 超时 {interaction_limit} 秒")
        self.root.after(1000, self._tick)

    def close(self) -> None:
        if self.process and self.process.poll() is None:
            self._stop_stream("程序关闭")
        if self.capture is not None:
            self.capture.release()
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="快手合规直播助手")
    parser.add_argument("--self-test", action="store_true", help="运行无界面核心检查")
    args = parser.parse_args()
    if args.self_test:
        print(json.dumps({"ffmpeg": find_binary("ffmpeg"), "ffprobe": find_binary("ffprobe"), "app_dir": str(APP_DIR)}, ensure_ascii=False))
        return
    root = Tk()
    LiveAssistant(root)
    root.mainloop()


if __name__ == "__main__":
    main()
