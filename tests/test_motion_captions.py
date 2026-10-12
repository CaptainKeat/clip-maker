import json
import uuid
from contextlib import contextmanager
import unittest
from pathlib import Path
from clipmaker.db import database
from clipmaker.editor import editor_payload, save_editor_draft
from clipmaker.motion_captions import normalize_motion, motion_pose, motion_events, append_motion_ass
from clipmaker.settings import Settings


@contextmanager
def fixture_directory():
    # Plain workspace directories also work under Windows restricted tokens.
    folder = Path(__file__).parent / ("fixture-" + uuid.uuid4().hex)
    folder.mkdir()
    try:
        yield str(folder)
    finally:
        for child in folder.iterdir():
            child.unlink()
        folder.rmdir()


class MotionCaptionTests(unittest.TestCase):
    def test_paths_interpolate_and_hold_without_nonfinite_values(self):
        m = normalize_motion({"enabled": True, "size": float("nan"), "color": "bad",
            "keyframes": [{"time": 12, "x": 70, "y": 40}, {"time": 10, "x": 30, "y": 20},
                          {"time": 12, "x": 80, "y": 40}]}, 10, 12)
        self.assertEqual(m["size"], 1)
        self.assertEqual(m["color"], "#ffffff")
        self.assertEqual(motion_pose(m, 11, 10, 12)["x"], 55)
        self.assertEqual(motion_pose(m, 15, 10, 12)["x"], 80)

    def test_cut_reorder_speed_and_overlapping_speakers(self):
        m = {"enabled": True, "color": "#123456", "x": 25, "y": 20,
             "keyframes": [{"time": 10, "x": 25, "y": 20}, {"time": 14, "x": 75, "y": 40}]}
        cues = [{"start": 10, "end": 14, "text": "Hello", "motion": m},
                {"start": 12, "end": 14, "text": "Other speaker", "motion": m}]
        segments = [{"source_start": 12, "source_end": 14, "speed": 2},
                    {"source_start": 10, "source_end": 11, "speed": 1}]
        events = motion_events(cues, segments)
        self.assertIn("\\pos(540.00,576.00)", events[0])
        self.assertIn("\\c&H00563412", events[0])
        self.assertEqual(sum("Hello" in e for e in events), 60)
        self.assertEqual(sum("Other speaker" in e for e in events), 30)
        self.assertTrue(any("0:00:01.00" in e and "Hello" in e for e in events))

    def test_all_motion_file_and_override_text_are_safe(self):
        with fixture_directory() as folder:
            path = Path(folder) / "captions.ass"
            self.assertTrue(append_motion_ass(path, [{"start": 0, "end": .1,
                "text": r"{\pos(0,0)}", "motion": {"enabled": True}}],
                [{"source_start": 0, "source_end": 1}]))
            text = path.read_text(encoding="utf-8-sig")
            self.assertIn("PlayResY: 1920", text)
            self.assertNotIn(r"{\pos(0,0)}", text)
            self.assertIn("Dialogue: 1", text)

    def test_draft_roundtrip_preserves_path_and_legacy_caption(self):
        with fixture_directory() as folder:
            settings = Settings(data_dir=folder, output_dir=folder)
            with database(settings.db_path) as db:
                db.execute("INSERT INTO videos(id,path,rel_path,name,size_bytes,mtime_ns,duration_sec) VALUES(1,'test.mp4','test.mp4','test',1,1,20)")
                db.execute("INSERT INTO candidates(id,video_id,start_sec,end_sec,score,reason) VALUES(1,1,10,15,1,'test')")
            captions = [{"text": "Moving", "start": 10, "end": 12, "speaker": "Me", "motion":
                {"enabled": True, "effect": "shake", "color": "#ffaa33", "keyframes":
                 [{"time": 10, "x": 30, "y": 25}, {"time": 12, "x": 60, "y": 25}]}},
                {"text": "Normal", "start": 13, "end": 14}]
            saved = save_editor_draft(settings, 1, {"source_start_sec": 10, "source_end_sec": 15,
                "segments": [{"source_start": 10, "source_end": 15}], "captions": captions})
            self.assertEqual(saved["captions"][0]["motion"]["keyframes"][1]["x"], 60)
            self.assertEqual(editor_payload(settings, 1)["captions"][0]["motion"]["effect"], "shake")
            self.assertIsNone(saved["captions"][1]["motion"])


if __name__ == "__main__":
    unittest.main()
