from __future__ import annotations

import json
import unittest
from unittest.mock import patch
from pathlib import Path

from clipmaker.analyze import TrackFeatures, _candidate_peaks, _dynamic_moment_window, _chapter_window, _selected_chapters, analyze_video
from clipmaker.db import connect, database
from clipmaker.editor import (_caption_after_cuts, _caption_after_segments, _editor_filter_graph, _editor_filter_graph_v2, _keep_segments, _normalize_cuts, _normalize_segments, _output_duration)
from clipmaker.media import _path_is_within, normalize_chapters, quick_fingerprint, scan_library
from clipmaker.render import (
    _combined_window, _hate_qualities, _render_filter_graph, _styled_caption_text,
    _transcript_audio_ordinals, _write_ass,
)
from clipmaker.captions import caption_phrases, caption_word_groups
from clipmaker.transcribe import (
    _candidate_audio_ordinals, merge_track_words, music_repetition_penalty,
    setup_landing_bonus, transcript_humor_score,
)
from clipmaker.taste import _cosine, _feature_vector
from clipmaker.server import JOBS, JOBS_LOCK, _candidate_filters, _directory_listing, _update_progress
from clipmaker.lmstudio import lmstudio_chat, lmstudio_state
from clipmaker.moment_refine import _bounded_refinement, _json_object, _reaction_anchors, _timed_transcript
from clipmaker.settings import Settings, update_storage_folders
from clipmaker.storage import export_media_path, export_media_url, safe_vod_folder_name
from clipmaker.vod_preview import preview_window
from clipmaker.audio_events import event_bonus


class FingerprintTests(unittest.TestCase):
    def test_equal_content_has_equal_fingerprint(self):
        first = Path(__file__).with_name(".fingerprint-equal-first.mp4")
        second = Path(__file__).with_name(".fingerprint-equal-second.mp4")
        try:
            first.write_bytes(b"same-video" * 100)
            second.write_bytes(b"same-video" * 100)
            self.assertEqual(quick_fingerprint(first), quick_fingerprint(second))
        finally:
            first.unlink(missing_ok=True)
            second.unlink(missing_ok=True)

    def test_different_tail_changes_fingerprint(self):
        first = Path(__file__).with_name(".fingerprint-tail-first.mp4")
        second = Path(__file__).with_name(".fingerprint-tail-second.mp4")
        try:
            first.write_bytes(b"a" * 2_000_000 + b"x")
            second.write_bytes(b"a" * 2_000_000 + b"y")
            self.assertNotEqual(quick_fingerprint(first), quick_fingerprint(second))
        finally:
            first.unlink(missing_ok=True)
            second.unlink(missing_ok=True)


class LibraryRootIsolationTests(unittest.TestCase):
    def test_missing_vod_cleanup_does_not_delete_other_libraries_or_break_duplicate_fk(self):
        from contextlib import contextmanager

        project = Path(__file__).resolve().parent.parent
        active_root = project / "tests"
        connection = connect(Path(":memory:"))
        missing_active = active_root / "missing-vod.mkv"
        connection.execute(
            """
            INSERT INTO videos(path,rel_path,name,size_bytes,mtime_ns,quick_hash,duration_sec,status)
            VALUES (?,?,?,?,?,?,?,'ready')
            """,
            (str(missing_active), "missing-vod.mkv", "missing-vod.mkv", 10, 1, "same", 10),
        )
        canonical_id = int(connection.execute("SELECT last_insert_rowid()").fetchone()[0])
        connection.execute(
            """
            INSERT INTO videos(path,rel_path,name,size_bytes,mtime_ns,quick_hash,duration_sec,status,duplicate_of)
            VALUES (?,?,?,?,?,?,?,'ready',?)
            """,
            (r"E:\\Other Library\\kept.mkv", "kept.mkv", "kept.mkv", 10, 1, "same", 10, canonical_id),
        )

        @contextmanager
        def shared_database(_path):
            yield connection

        settings = Settings(source_root=str(active_root), data_dir=str(project / "data"), output_dir=str(project / "data"))
        try:
            with patch("clipmaker.media.database", shared_database):
                result = scan_library(settings)
            rows = connection.execute("SELECT path, duplicate_of FROM videos").fetchall()
            self.assertEqual(result.discovered, 0)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["path"], r"E:\\Other Library\\kept.mkv")
            self.assertIsNone(rows[0]["duplicate_of"])
        finally:
            connection.close()

    def test_path_scope_distinguishes_configured_libraries(self):
        self.assertTrue(_path_is_within(r"G:\\OBS\\game.mkv", Path(r"G:\\OBS")))
        self.assertFalse(_path_is_within(r"E:\\OBS Clips\\game.mkv", Path(r"G:\\OBS")))


class ChapterMarkerTests(unittest.TestCase):
    def test_ffprobe_chapters_are_normalized(self):
        payload = {"chapters": [
            {"id": 0, "start_time": "42.25", "end_time": "45", "tags": {"title": "Chapter 1"}},
            {"id": 1, "start_time": "118.5", "end_time": "120", "tags": {}},
        ]}
        chapters = normalize_chapters(payload, 120)
        self.assertEqual(chapters[0], {"index": 0, "start_sec": 42.25, "end_sec": 45.0, "title": "Chapter 1"})
        self.assertEqual(chapters[1]["title"], "Marker 2")

    def test_chapter_window_keeps_context_before_press(self):
        self.assertEqual(_chapter_window(80, 200), (35.0, 95.0))
        self.assertEqual(_chapter_window(8, 200), (0.0, 60.0))
        self.assertEqual(_chapter_window(195, 200), (140.0, 200.0))

    def test_selected_chapter_indices_can_disable_or_filter_flags(self):
        chapters = [{"index": 0}, {"index": 1}, {"index": 2}]
        self.assertEqual(_selected_chapters(chapters, None), chapters)
        self.assertEqual(_selected_chapters(chapters, []), [])
        self.assertEqual(_selected_chapters(chapters, [2, 0]), [{"index": 0}, {"index": 2}])

class CandidateTests(unittest.TestCase):
    def test_hate_reason_teaches_the_correct_system(self):
        self.assertEqual(_hate_qualities(["not_funny"], ""), (None, 0, 0))
        self.assertEqual(_hate_qualities(["bad_audio"], ""), (0, None, 0))
        self.assertEqual(
            _hate_qualities(["bad_captions", "weak_moment"], ""),
            (0, 0, 0),
        )

    def test_separated_spikes_create_separate_candidates(self):
        frames = [-35.0] * 180
        frames[30] = -5.0
        frames[120] = -4.0
        track = TrackFeatures(0, 1, frames, -35.0, -35.0, 1.0)
        peaks = _candidate_peaks([track], 180, 10)
        seconds = [item[0] for item in peaks]
        self.assertTrue(any(abs(second - 30) <= 3 for second in seconds))
        self.assertTrue(any(abs(second - 120) <= 3 for second in seconds))

    def test_isolated_reaction_creates_short_clip(self):
        scores = [0.02] * 180
        scores[80] = 0.9
        start, end = _dynamic_moment_window(scores, 80, 180)
        self.assertEqual(end - start, 36.0)

    def test_extended_exchange_creates_longer_clip(self):
        scores = [0.02] * 180
        for second in range(55, 102):
            scores[second] = 0.55
        start, end = _dynamic_moment_window(scores, 80, 180)
        self.assertGreater(end - start, 60.0)
        self.assertLessEqual(end - start, 90.0)

    def test_long_chaos_is_capped_at_ninety_seconds(self):
        scores = [0.7] * 240
        start, end = _dynamic_moment_window(scores, 120, 240)
        self.assertEqual(end - start, 90.0)

    def test_vod_edge_keeps_minimum_length(self):
        scores = [0.02] * 120
        scores[2] = 0.9
        start, end = _dynamic_moment_window(scores, 2, 120)
        self.assertEqual(start, 0.0)
        self.assertEqual(end - start, 30.0)

    def test_qwen_refinement_is_clamped_to_reel_range(self):
        self.assertEqual(_bounded_refinement(20, 35, 80), (12.5, 42.5))
        self.assertEqual(_bounded_refinement(-20, 200, 120), (15.0, 105.0))

    def test_qwen_json_accepts_fenced_response(self):
        payload = _json_object(
            '~~~json\n{"start": 4, "end": 44, "quality": 0.8}\n~~~'
        )
        self.assertEqual(payload["end"], 44)

    def test_reaction_transcript_scores_above_plain_text(self):
        plain = transcript_humor_score("we should walk over to the next building")
        reaction = transcript_humor_score("holy shit bro what the fuck no way hahaha")
        self.assertGreater(reaction, plain)


    def test_setup_before_laughter_gets_extra_weight(self):
        self.assertGreater(
            setup_landing_bonus("he opened the door and immediately fell through the map hahaha"),
            setup_landing_bonus("hahaha"),
        )

    def test_reaction_anchors_keep_timestamps_for_qwen(self):
        payload = {"transcription": [{"words": [
            {"text": "open", "start": 2.0, "end": 2.2},
            {"text": "bro", "start": 4.1, "end": 4.3},
            {"text": "hahaha", "start": 7.5, "end": 8.0},
        ]}]}
        self.assertEqual(_reaction_anchors(payload), [4.1, 7.5])

    def test_mic_track_is_always_in_transcription_set(self):
        row = {
            "audio_streams_json": json.dumps([
                {"ordinal": 0}, {"ordinal": 1}, {"ordinal": 2}, {"ordinal": 3}
            ]),
            "track_scores_json": json.dumps({"1": .9, "2": .1, "3": .8}),
        }
        settings = Settings(max_transcription_tracks=3, mic_audio_ordinal=2)
        self.assertEqual(_candidate_audio_ordinals(row, settings), [0, 2, 1])

    def test_multitrack_merge_keeps_conversation_and_prefers_me_for_duplicates(self):
        choices = [
            {
                "audio_ordinal": 0,
                "selection_score": .7,
                "labeled_words": [
                    {"text": "hello", "start": 1.0, "end": 1.3, "speaker": "Person 1"},
                    {"text": "why", "start": 2.0, "end": 2.2, "speaker": "Person 2"},
                ],
            },
            {
                "audio_ordinal": 2,
                "selection_score": .5,
                "labeled_words": [
                    {"text": "hello", "start": 1.05, "end": 1.3, "speaker": "Me"},
                    {"text": "bro", "start": 2.3, "end": 2.5, "speaker": "Me"},
                ],
            },
        ]
        merged = merge_track_words(choices)
        self.assertEqual([word["text"] for word in merged], ["hello", "why", "bro"])
        self.assertEqual(merged[0]["speaker"], "Me")

    def test_qwen_transcript_contains_speaker_turns(self):
        payload = {"transcription": [{"start": 0, "end": 3, "words": [
            {"text": "open", "start": .1, "end": .3, "speaker": "Me"},
            {"text": "it", "start": .35, "end": .5, "speaker": "Me"},
            {"text": "no", "start": .8, "end": 1.0, "speaker": "Person 1"},
        ]}]}
        transcript = _timed_transcript(payload)
        self.assertIn("Me: open it", transcript)
        self.assertIn("Person 1: no", transcript)

    def test_repeated_lyrics_receive_music_penalty(self):
        lyrics = "you spin me round baby round round " * 8
        conversation = "we need to get the scrap and leave before the monster arrives"
        self.assertGreater(music_repetition_penalty(lyrics), 0.5)
        self.assertEqual(music_repetition_penalty(conversation), 0.0)


class TranscriptFirstPipelineTests(unittest.TestCase):
    @patch("clipmaker.moment_refine.ensure_stored_refinement")
    @patch("clipmaker.transcribe.transcribe_candidate")
    @patch("clipmaker.analyze.analyze_track")
    def test_all_candidates_are_transcribed_before_top_ai_review(
        self, mocked_track, mocked_transcribe, mocked_refine
    ):
        frames = [-35.0] * 180
        frames[30] = -4.0
        frames[110] = -3.0
        mocked_track.return_value = TrackFeatures(0, 1, frames, -35.0, -35.0, 1.0)
        folder = Path(__file__).parent
        for item in folder.glob("clipmaker.db*"):
            item.unlink(missing_ok=True)
        settings = Settings(data_dir=str(folder), output_dir=str(folder))
        try:
            with database(settings.db_path) as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO videos(
                        path, rel_path, name, size_bytes, mtime_ns, duration_sec,
                        audio_streams_json, status
                    ) VALUES (?, ?, ?, 1, 1, 180, ?, 'ready')
                    """,
                    ("fake.mp4", "fake.mp4", "fake.mp4", json.dumps([{"ordinal": 0, "index": 1}])),
                )
                video_id = int(cursor.lastrowid)

            def store_transcript(_settings, candidate_id, progress=None, refine=True):
                with database(_settings.db_path) as connection:
                    connection.execute(
                        """
                        UPDATE candidates
                        SET transcript='that was stupid hahaha',
                            transcript_json='{"transcription":[]}',
                            transcript_score=.8, score=.8
                        WHERE id=?
                        """,
                        (candidate_id,),
                    )
                return "that was stupid hahaha"

            mocked_transcribe.side_effect = store_transcript
            count = analyze_video(settings, video_id)
            self.assertEqual(mocked_transcribe.call_count, count)
            self.assertTrue(
                all(call.kwargs["refine"] is False for call in mocked_transcribe.call_args_list)
            )
            self.assertEqual(mocked_refine.call_count, min(10, count))
            with database(settings.db_path) as connection:
                status = connection.execute(
                    "SELECT status FROM videos WHERE id=?", (video_id,)
                ).fetchone()["status"]
                peaks = connection.execute(
                    "SELECT peak_sec FROM candidates WHERE video_id=?", (video_id,)
                ).fetchall()
            self.assertEqual(status, "analyzed")
            self.assertTrue(all(float(row["peak_sec"]) > 0 for row in peaks))
        finally:
            for item in folder.glob("clipmaker.db*"):
                item.unlink(missing_ok=True)


class CombineClipTests(unittest.TestCase):
    def test_combined_window_keeps_both_peaks_and_caps_at_ninety_seconds(self):
        start, end = _combined_window([
            {"start": 0, "end": 75, "peak": 48},
            {"start": 55, "end": 145, "peak": 102},
        ], 300)
        self.assertAlmostEqual(end - start, 90.0)
        self.assertLessEqual(start, 48)
        self.assertGreaterEqual(end, 102)

    def test_combined_window_rejects_jokes_more_than_ninety_seconds_apart(self):
        with self.assertRaisesRegex(ValueError, "too far apart"):
            _combined_window([
                {"start": 0, "end": 40, "peak": 10},
                {"start": 120, "end": 170, "peak": 150},
            ], 300)


class CaptionTimingTests(unittest.TestCase):
    def test_low_confidence_stretched_tokens_do_not_start_caption_early(self):
        segment = {
            "timestamps": {"from": "00:00:00,000", "to": "00:00:10,900"},
            "text": "I have increased levels of radiation, hold up!",
            "tokens": [
                {"text": " I", "offsets": {"from": 10, "to": 2000}, "p": 0.04},
                {"text": " have", "offsets": {"from": 2000, "to": 7670}, "p": 0.33},
                {"text": " increased", "offsets": {"from": 7710, "to": 8160}, "p": 0.89},
                {"text": " levels", "offsets": {"from": 8250, "to": 8490}, "p": 0.92},
                {"text": " of", "offsets": {"from": 8490, "to": 8600}, "p": 0.98},
                {"text": " radiation", "offsets": {"from": 8600, "to": 9200}, "p": 0.95},
                {"text": ",", "offsets": {"from": 9200, "to": 9270}, "p": 0.16},
                {"text": " hold", "offsets": {"from": 9270, "to": 9440}, "p": 0.88},
                {"text": " up", "offsets": {"from": 9440, "to": 9580}, "p": 0.87},
                {"text": "!", "offsets": {"from": 9580, "to": 9690}, "p": 0.7},
            ],
        }
        phrases = caption_phrases({"transcription": [segment]})
        self.assertGreater(phrases[0][0], 6.5)
        self.assertLess(phrases[0][0], 7.5)
        self.assertEqual(" ".join(phrase[2] for phrase in phrases), "I have increased levels of radiation, hold up!")
        self.assertTrue(all(len(phrase[2]) <= 42 for phrase in phrases))
        self.assertTrue(all(phrase[1] - phrase[0] <= 2.2 for phrase in phrases))
        self.assertLessEqual(phrases[0][1], phrases[1][0])


    def test_stretched_middle_word_does_not_linger_through_silence(self):
        payload = {
            "transcription": [
                {
                    "start": 0.0,
                    "end": 8.0,
                    "words": [
                        {"text": "Ow", "start": 0.2, "end": 4.8, "confidence": 0.9},
                        {"text": "Okay.", "start": 4.8, "end": 5.2, "confidence": 0.9},
                    ],
                }
            ]
        }
        phrases = caption_phrases(payload, "balanced")
        self.assertEqual(phrases[0][2], "Ow")
        self.assertLessEqual(phrases[0][1] - phrases[0][0], 0.9)
        self.assertEqual(phrases[1][2], "Okay.")

    def test_freecut_pause_creates_a_new_phrase(self):
        payload = {
            "transcription": [
                {
                    "text": "take a look over there",
                    "start": 4,
                    "end": 5.4,
                    "words": [
                        {"text": "take", "start": 4, "end": 4.24},
                        {"text": "a", "start": 4.27, "end": 4.36},
                        {"text": "look", "start": 4.39, "end": 4.66},
                        {"text": "over", "start": 4.90, "end": 5.12},
                        {"text": "there", "start": 5.15, "end": 5.40},
                    ],
                }
            ]
        }
        phrases = caption_phrases(payload)
        self.assertEqual([phrase[2] for phrase in phrases], ["take a look", "over there"])
        self.assertEqual(phrases[1][0], 4.9)


    def test_ass_caption_events_never_overlap(self):
        destination = Path(__file__).with_name(".caption-no-overlap.ass")
        row = {"transcript_json": json.dumps({"transcription": [{"words": [
            {"text": "hey", "start": 0.0, "end": 0.30, "speaker": "Me"},
            {"text": "what", "start": 0.20, "end": 0.48, "speaker": "Person 1"},
            {"text": "bro", "start": 0.50, "end": 0.72, "speaker": "Person 1"},
        ]}]})}
        try:
            self.assertTrue(_write_ass(row, destination, caption_preset="punch"))
            events = []
            for line in destination.read_text(encoding="utf-8-sig").splitlines():
                if not line.startswith("Dialogue:"):
                    continue
                fields = line.split(",", 9)
                def seconds(value):
                    hours, minutes, secs = value.split(":")
                    return int(hours) * 3600 + int(minutes) * 60 + float(secs)
                events.append((seconds(fields[1]), seconds(fields[2])))
            self.assertTrue(events)
            self.assertTrue(all(events[index - 1][1] <= events[index][0] for index in range(1, len(events))))
        finally:
            destination.unlink(missing_ok=True)

    def test_render_mixes_every_track_that_supplied_transcript_words(self):
        row = {
            "audio_streams_json": json.dumps([{"ordinal": 0}, {"ordinal": 1}, {"ordinal": 4}]),
            "transcript_json": json.dumps({
                "transcription": [{"words": [
                    {"text": "hello", "start": 0.1, "end": 0.3, "track_ordinal": 4},
                    {"text": "bro", "start": 0.5, "end": 0.7, "track_ordinal": 0},
                ]}],
                "clipmaker": {"track_choices": [
                    {"audio_ordinal": 0, "word_count": 1},
                    {"audio_ordinal": 4, "word_count": 1},
                ]},
            }),
        }
        ordinals = _transcript_audio_ordinals(row)
        self.assertEqual(ordinals, [4, 0])
        graph, _video, audio = _render_filter_graph(None, ordinals, "natural")
        self.assertIn("[0:a:4]", graph)
        self.assertIn("[0:a:0]", graph)
        self.assertIn("amix=inputs=2", graph)
        self.assertEqual(audio, "[outa]")

    def test_punch_captions_use_short_active_word_groups(self):
        payload = {
            "transcription": [{
                "start": 0,
                "end": 2,
                "words": [
                    {"text": "holy", "start": 0.0, "end": 0.25, "confidence": 0.9},
                    {"text": "shit", "start": 0.27, "end": 0.50, "confidence": 0.9},
                    {"text": "what", "start": 0.52, "end": 0.75, "confidence": 0.9},
                    {"text": "is", "start": 0.77, "end": 0.89, "confidence": 0.9},
                    {"text": "happening", "start": 0.91, "end": 1.35, "confidence": 0.9},
                ],
            }]
        }
        groups = caption_word_groups(payload, "punch")
        self.assertTrue(all(len(group) <= 4 for group in groups))
        styled = _styled_caption_text(groups[0], 0, "punch")
        self.assertIn("HOLY", styled)
        self.assertIn(r"\fscx116", styled)


class TasteTests(unittest.TestCase):
    def test_similar_reaction_style_scores_above_plain_planning(self):
        reference = _feature_vector("holy shit bro no way hahaha what the fuck")
        reaction = _feature_vector("bro what the fuck no way haha")
        planning = _feature_vector("walk to the building and collect the equipment")
        self.assertGreater(_cosine(reference, reaction), _cosine(reference, planning))


if __name__ == "__main__":
    unittest.main()


class VodPreviewTests(unittest.TestCase):
    def test_long_vod_samples_the_middle(self):
        self.assertEqual(preview_window(3600), (1777.5, 45.0))

    def test_short_vod_uses_its_full_duration(self):
        self.assertEqual(preview_window(30), (0.0, 30.0))


class CandidateFilterTests(unittest.TestCase):
    def test_video_and_status_filters_combine(self):
        where, params = _candidate_filters({"status": ["approved"], "video_id": ["42"]})
        self.assertEqual(where, "WHERE c.status = ? AND c.video_id = ?")
        self.assertEqual(params, ["approved", 42])

    def test_all_statuses_can_still_filter_one_video(self):
        where, params = _candidate_filters({"status": ["all"], "video_id": ["7"]})
        self.assertEqual(where, "WHERE c.video_id = ?")
        self.assertEqual(params, [7])



class _FakeLmStudioResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return b'{"ok": true, "server_up": false, "model_loaded": false, "loaded_models": []}'


class LmStudioControlTests(unittest.TestCase):
    @patch("clipmaker.lmstudio.urlopen", return_value=_FakeLmStudioResponse())
    def test_state_uses_ai_council_controller(self, mocked_urlopen):
        state = lmstudio_state(Settings())
        self.assertTrue(state["ok"])
        self.assertEqual(state["loaded_models"], [])
        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:11436/state")

class _FakeLmStudioAiState(_FakeLmStudioResponse):
    def read(self):
        return b'{"ok": true, "server_up": true, "model_loaded": true, "loaded_models": [{"identifier": "council-analyst"}]}'


class _FakeLmStudioChatResponse(_FakeLmStudioResponse):
    def read(self):
        return b'{"choices": [{"message": {"content": "caption ready"}}]}'


class LmStudioChatTests(unittest.TestCase):
    @patch("clipmaker.lmstudio.urlopen", side_effect=[_FakeLmStudioAiState(), _FakeLmStudioChatResponse()])
    def test_chat_uses_shared_council_model(self, mocked_urlopen):
        result = lmstudio_chat(Settings(), "write it")
        self.assertEqual(result, "caption ready")
        request = mocked_urlopen.call_args_list[1].args[0]
        self.assertEqual(request.full_url, "http://127.0.0.1:1234/v1/chat/completions")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["model"], "council-analyst")

class EditorSubtitleProgressTests(unittest.TestCase):
    def setUp(self):
        self.job_id = "editor-caption-test"
        with JOBS_LOCK:
            JOBS[self.job_id] = {
                "kind": "caption", "current": 1, "total": 1, "progress": 2,
                "title": "Preparing auto subtitles", "detail": "", "stage": "Starting",
            }

    def tearDown(self):
        with JOBS_LOCK:
            JOBS.pop(self.job_id, None)

    def test_caption_progress_tracks_loading_chunks_and_finish(self):
        _update_progress(self.job_id, "loading local Parakeet model")
        self.assertEqual(JOBS[self.job_id]["progress"], 8)
        self.assertEqual(JOBS[self.job_id]["stage"], "Loading subtitle engine")

        _update_progress(self.job_id, "Parakeet chunk 3/4")
        self.assertGreaterEqual(JOBS[self.job_id]["progress"], 70)
        self.assertIn("3 of 4", JOBS[self.job_id]["title"])

        _update_progress(self.job_id, "editor subtitles ready")
        self.assertEqual(JOBS[self.job_id]["progress"], 97)
        self.assertEqual(JOBS[self.job_id]["stage"], "Finishing subtitles")

class EditorStageFourTests(unittest.TestCase):
    def test_segment_speed_freeze_and_caption_mapping(self):
        segments = _normalize_segments([
            {"source_start": 10, "source_end": 20, "speed": 2, "freeze": 1},
            {"source_start": 30, "source_end": 35, "speed": 1},
        ], 0, 60)
        self.assertEqual(_output_duration(segments), 11.0)
        mapped = _caption_after_segments({"text": "hello", "start": 32, "end": 33}, segments)
        self.assertEqual((mapped["start"], mapped["end"]), (8.0, 9.0))

    def test_stage_four_graph_contains_new_editor_effects(self):
        graph, video, audio = _editor_filter_graph_v2(
            None,
            [{"ordinal": 0, "enabled": True, "gain_db": 0, "keyframes": [{"time": 0, "gain_db": 0}, {"time": 2, "gain_db": -6}]}],
            "natural",
            [{"source_start": 0, "source_end": 2, "speed": 1.5, "freeze": 0.5, "transition": "fade"}],
            {"mode": "fill", "zoom": 1, "x": 0, "y": 0, "fade_in": 0, "fade_out": 0},
            {"brightness": 0.1, "contrast": 1.1, "saturation": 1.2, "temperature": 0.2},
            1.833, 0, [], [],
        )
        self.assertIn("atempo=1.500000", graph)
        self.assertIn("tpad=stop_mode=clone", graph)
        self.assertIn("colorbalance", graph)
        self.assertIn("eval=frame", graph)
        self.assertEqual((video, audio), ("[colored]", "[outa]"))

    def test_acoustic_events_change_ranking(self):
        events = [{"type": "laughter", "score": 0.9}, {"type": "yelling", "score": 0.7}]
        self.assertGreater(event_bonus(events), 0.2)

class EditorStageTwoTests(unittest.TestCase):
    def test_overlapping_cuts_merge_and_keep_segments(self):
        cuts = _normalize_cuts([
            {"start": 12, "end": 18},
            {"start": 17.5, "end": 22},
            {"start": 40, "end": 43},
        ], 10, 50)
        self.assertEqual(cuts, [{"start": 12.0, "end": 22.0}, {"start": 40.0, "end": 43.0}])
        self.assertEqual(_keep_segments(10, 50, cuts), [(0, 2.0), (12.0, 30.0), (33.0, 40)])

    def test_caption_after_cut_is_removed_or_retimed(self):
        cuts = [{"start": 15.0, "end": 20.0}]
        self.assertIsNone(_caption_after_cuts({"text": "gone", "start": 16, "end": 17}, 10, cuts))
        kept = _caption_after_cuts({"text": "kept", "start": 22, "end": 23}, 10, cuts)
        self.assertEqual((kept["start"], kept["end"]), (7.0, 8.0))

    def test_filter_graph_contains_concat_fill_and_audio_fades(self):
        graph, video_map, audio_map = _editor_filter_graph(
            None,
            [{"ordinal": 0, "enabled": True, "gain_db": 0}],
            "natural",
            [(0, 5), (8, 15)],
            {"mode": "fill", "zoom": 1.2, "x": 25, "y": -10, "fade_in": 1, "fade_out": 2},
            12,
        )
        self.assertIn("concat=n=2:v=1:a=1", graph)
        self.assertIn("crop=1080:1920", graph)
        self.assertIn("afade=t=in", graph)
        self.assertIn("afade=t=out:st=10.000:d=2.000", graph)
        self.assertEqual((video_map, audio_map), ("[base]", "[outa]"))


class StorageFolderTests(unittest.TestCase):
    def test_storage_folders_are_validated_and_saved(self):
        project = Path(__file__).resolve().parent.parent
        source = project / "tests"
        output = project / "data"
        config = source / ".storage-settings-test.json"
        settings = Settings(source_root=str(source), output_dir=str(output), data_dir=str(output))
        try:
            result = update_storage_folders(settings, str(source), str(output), config)
            self.assertEqual(result["source_root"], str(source.resolve()))
            self.assertEqual(json.loads(config.read_text(encoding="utf-8"))["output_dir"], str(output.resolve()))
        finally:
            config.unlink(missing_ok=True)

    def test_clip_folder_cannot_be_inside_vod_folder(self):
        project = Path(__file__).resolve().parent.parent
        source = project / "tests"
        settings = Settings(source_root=str(source), output_dir=str(project / "data"), data_dir=str(project / "data"))
        with self.assertRaisesRegex(ValueError, "cannot be inside"):
            update_storage_folders(settings, str(source), str(source / "clips"), source / ".unused-settings.json")

class PerVodOutputFolderTests(unittest.TestCase):
    def test_vod_folder_name_defaults_to_filename_and_cleans_windows_characters(self):
        self.assertEqual(safe_vod_folder_name("", "Lethal Company 2026-08-09.mkv"), "Lethal Company 2026-08-09")
        self.assertEqual(safe_vod_folder_name('Boys: Night / Chaos?', "fallback.mkv"), "Boys - Night - Chaos -")
        self.assertEqual(safe_vod_folder_name("CON", "fallback.mkv"), "CON clips")

    def test_nested_export_url_round_trips_under_output_root(self):
        root = Path(__file__).resolve().parent.parent / "data"
        clip = root / "Lethal Company Boys Night" / "clip-0001-v01.mp4"
        url = export_media_url(root, clip)
        self.assertEqual(url, "/media/export/Lethal%20Company%20Boys%20Night/clip-0001-v01.mp4")
        encoded = url.removeprefix("/media/export/")
        self.assertEqual(export_media_path(root, encoded), clip.resolve())

    def test_nested_export_path_cannot_escape_output_root(self):
        root = Path(__file__).resolve().parent.parent / "data"
        with self.assertRaisesRegex(ValueError, "Invalid export path"):
            export_media_path(root, "../outside.mp4")

class DirectoryListingTests(unittest.TestCase):
    def test_this_pc_lists_available_drives(self):
        listing = _directory_listing("")
        self.assertTrue(listing["drives"])
        self.assertTrue(any(item["path"].upper().startswith("C:\\") for item in listing["folders"]))

    def test_directory_listing_returns_child_folders_and_parent(self):
        project = Path(__file__).resolve().parent.parent
        listing = _directory_listing(str(project))
        names = {item["name"] for item in listing["folders"]}
        self.assertFalse(listing["drives"])
        self.assertIn("clipmaker", names)
        self.assertEqual(listing["parent"], str(project.parent))

    def test_directory_listing_rejects_missing_folder(self):
        with self.assertRaisesRegex(ValueError, "Folder does not exist"):
            _directory_listing(r"Z:\\this-folder-should-not-exist")