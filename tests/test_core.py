import json
import tempfile
import unittest
from pathlib import Path

import app


class CoreTests(unittest.TestCase):
    def test_concat_file_quotes_paths(self):
        with tempfile.TemporaryDirectory() as tmp:
            media = app.Material(str(Path(tmp) / "a file's.mp4"))
            target = Path(tmp) / "playlist.txt"
            app.write_concat_file([media], str(target))
            self.assertEqual(target.read_text(encoding="utf-8"), "file '" + app.concat_quote(media.path) + "'\n")

    def test_collect_video_files_is_recursive_and_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "B.MP4").touch()
            (root / "a.mkv").touch()
            (root / "sub").mkdir()
            (root / "sub" / "c.mov").touch()
            (root / "ignore.txt").touch()
            found = app.collect_video_files(str(root))
            self.assertEqual([Path(p).name for p in found], ["a.mkv", "B.MP4", "c.mov"])

    def test_parse_dshow_devices(self):
        output = '\n'.join([
            '[dshow @ 000] "Integrated Camera" (video)',
            '[dshow @ 000] "Microphone (USB)" (audio)',
            '[dshow @ 000] "Integrated Camera" (video)',
        ])
        self.assertEqual(app.parse_dshow_devices(output), ["Integrated Camera"])

    def test_ffmpeg_command_keeps_url_only_as_destination(self):
        command = app.build_ffmpeg_command(
            "ffmpeg.exe", "Camera", "", "playlist.txt", "rtmps://example/live/key", 1280, 720, 2500, None
        )
        self.assertEqual(command[0], "ffmpeg.exe")
        self.assertIn("rtmps://example/live/key", command)
        self.assertNotIn("-stream_loop", command[:12])
        self.assertIn("-stream_loop", command)
        self.assertIn("[v]", command)

    def test_microphone_switches_audio_mapping_to_camera(self):
        with_mic = app.build_ffmpeg_command(
            "ffmpeg.exe", "Camera", "Mic", "playlist.txt", "rtmp://x", 1280, 720, 2500, None
        )
        without_mic = app.build_ffmpeg_command(
            "ffmpeg.exe", "Camera", "", "playlist.txt", "rtmp://x", 1280, 720, 2500, None
        )
        self.assertIn("0:a?", with_mic)
        self.assertIn("1:a?", without_mic)

    def test_parse_avfoundation_devices(self):
        output = """[AVFoundation indev @ 0x] AVFoundation video devices:\n[AVFoundation indev @ 0x] [0] FaceTime HD Camera\n[AVFoundation indev @ 0x] [1] Continuity Camera\n[AVFoundation indev @ 0x] AVFoundation audio devices:\n[AVFoundation indev @ 0x] [0] MacBook Microphone\n"""
        self.assertEqual(app.parse_avfoundation_devices(output), ["FaceTime HD Camera", "Continuity Camera"])

    def test_macos_ffmpeg_command_uses_avfoundation(self):
        command = app.build_ffmpeg_command(
            "ffmpeg", "0", "1", "playlist.txt", "rtmp://x", 1280, 720, 2500, None, "macos"
        )
        self.assertIn("avfoundation", command)
        self.assertIn("0:1", command)
        self.assertNotIn("dshow", command)


if __name__ == "__main__":
    unittest.main()
