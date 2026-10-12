from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS videos (
    id INTEGER PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    rel_path TEXT NOT NULL,
    name TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    mtime_ns INTEGER NOT NULL,
    quick_hash TEXT,
    duration_sec REAL NOT NULL DEFAULT 0,
    container TEXT,
    width INTEGER,
    height INTEGER,
    fps REAL,
    video_codec TEXT,
    audio_streams_json TEXT NOT NULL DEFAULT '[]',
    chapters_json TEXT NOT NULL DEFAULT '[]',
    clip_folder_name TEXT NOT NULL DEFAULT '',
    duplicate_of INTEGER REFERENCES videos(id),
    status TEXT NOT NULL DEFAULT 'ready',
    last_error TEXT,
    discovered_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    probed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_videos_hash ON videos(quick_hash);
CREATE INDEX IF NOT EXISTS idx_videos_duplicate ON videos(duplicate_of);

CREATE TABLE IF NOT EXISTS audio_features (
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    audio_ordinal INTEGER NOT NULL,
    stream_index INTEGER NOT NULL,
    frames_json TEXT NOT NULL,
    median_db REAL,
    p95_db REAL,
    active_ratio REAL,
    analyzed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(video_id, audio_ordinal)
);

CREATE TABLE IF NOT EXISTS candidates (
    id INTEGER PRIMARY KEY,
    video_id INTEGER NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    start_sec REAL NOT NULL,
    end_sec REAL NOT NULL,
    score REAL NOT NULL,
    energy_score REAL NOT NULL DEFAULT 0,
    peak_sec REAL NOT NULL DEFAULT 0,
    transcript_score REAL NOT NULL DEFAULT 0,
    reason TEXT NOT NULL,
    track_scores_json TEXT NOT NULL DEFAULT '{}',
    audio_events_json TEXT NOT NULL DEFAULT '[]',
    transcript TEXT NOT NULL DEFAULT '',
    transcript_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    preview_path TEXT,
    export_path TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(video_id, start_sec, end_sec)
);

CREATE INDEX IF NOT EXISTS idx_candidates_status_score
ON candidates(status, score DESC);

CREATE TABLE IF NOT EXISTS candidate_variants (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    attempt INTEGER NOT NULL,
    recipe_key TEXT NOT NULL,
    recipe_label TEXT NOT NULL,
    clip_start_sec REAL NOT NULL,
    clip_end_sec REAL NOT NULL,
    caption_preset TEXT NOT NULL,
    audio_preset TEXT NOT NULL,
    audio_ordinal INTEGER NOT NULL DEFAULT 0,
    output_path TEXT,
    decision TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(candidate_id, attempt)
);

CREATE INDEX IF NOT EXISTS idx_candidate_variants_candidate
ON candidate_variants(candidate_id, attempt DESC);

CREATE TABLE IF NOT EXISTS clip_edit_drafts (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    base_variant_id INTEGER REFERENCES candidate_variants(id) ON DELETE SET NULL,
    source_start_sec REAL NOT NULL,
    source_end_sec REAL NOT NULL,
    caption_preset TEXT NOT NULL DEFAULT 'punch',
    audio_preset TEXT NOT NULL DEFAULT 'natural',
    captions_json TEXT NOT NULL DEFAULT '[]',
    audio_mix_json TEXT NOT NULL DEFAULT '[]',
    crop_json TEXT NOT NULL DEFAULT '{}',
    cuts_json TEXT NOT NULL DEFAULT '[]',
    segments_json TEXT NOT NULL DEFAULT '[]',
    overlays_json TEXT NOT NULL DEFAULT '[]',
    sfx_json TEXT NOT NULL DEFAULT '[]',
    color_json TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS review_feedback (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES candidate_variants(id) ON DELETE CASCADE,
    verdict TEXT NOT NULL,
    edit_quality INTEGER,
    moment_quality INTEGER,
    social_ready INTEGER,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(candidate_id, variant_id)
);

CREATE INDEX IF NOT EXISTS idx_review_feedback_verdict
ON review_feedback(verdict, candidate_id);

CREATE TABLE IF NOT EXISTS publish_jobs (
    id INTEGER PRIMARY KEY,
    candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
    variant_id INTEGER NOT NULL REFERENCES candidate_variants(id) ON DELETE CASCADE,
    platform TEXT NOT NULL,
    integration_id TEXT NOT NULL,
    postiz_result_json TEXT,
    scheduled_for TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'scheduled',
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_publish_jobs_variant
ON publish_jobs(variant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS caption_drafts (
    candidate_id INTEGER PRIMARY KEY REFERENCES candidates(id) ON DELETE CASCADE,
    caption TEXT NOT NULL,
    tone TEXT NOT NULL DEFAULT 'funny',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS taste_examples (
    id INTEGER PRIMARY KEY,
    creator TEXT NOT NULL DEFAULT '',
    source_url TEXT NOT NULL UNIQUE,
    source_title TEXT NOT NULL DEFAULT '',
    local_path TEXT,
    transcript TEXT NOT NULL DEFAULT '',
    notes TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'queued',
    error TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    analyzed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_taste_examples_status
ON taste_examples(status, created_at DESC);

CREATE TABLE IF NOT EXISTS archived_taste_feedback (
    id INTEGER PRIMARY KEY,
    source_candidate_id INTEGER NOT NULL,
    transcript TEXT NOT NULL,
    moment_quality INTEGER NOT NULL CHECK(moment_quality IN (0, 1)),
    verdict TEXT NOT NULL DEFAULT '',
    reason_text TEXT NOT NULL DEFAULT '',
    archived_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_candidate_id, moment_quality)
);
"""


def connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA)
    columns = {str(row["name"]) for row in connection.execute("PRAGMA table_info(candidates)")}
    if "taste_score" not in columns:
        connection.execute("ALTER TABLE candidates ADD COLUMN taste_score REAL NOT NULL DEFAULT 0")
    if "peak_sec" not in columns:
        connection.execute(
            "ALTER TABLE candidates ADD COLUMN peak_sec REAL NOT NULL DEFAULT 0"
        )
    if "audio_events_json" not in columns:
        connection.execute(
            "ALTER TABLE candidates ADD COLUMN audio_events_json TEXT NOT NULL DEFAULT '[]'"
        )
    draft_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(clip_edit_drafts)")
    }
    for name, default in (
        ("segments_json", "[]"),
        ("overlays_json", "[]"),
        ("sfx_json", "[]"),
        ("color_json", "{}"),
    ):
        if name not in draft_columns:
            connection.execute(
                f"ALTER TABLE clip_edit_drafts ADD COLUMN {name} TEXT NOT NULL DEFAULT '{default}'"
            )
    video_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(videos)")
    }
    if "chapters_json" not in video_columns:
        # Keep existing rows NULL so the next library scan knows they have
        # never been checked for OBS chapter metadata.
        connection.execute("ALTER TABLE videos ADD COLUMN chapters_json TEXT")
    if "clip_folder_name" not in video_columns:
        connection.execute(
            "ALTER TABLE videos ADD COLUMN clip_folder_name TEXT NOT NULL DEFAULT ''"
        )
    feedback_columns = {
        str(row["name"])
        for row in connection.execute("PRAGMA table_info(review_feedback)")
    }
    if "reason_text" not in feedback_columns:
        connection.execute(
            "ALTER TABLE review_feedback ADD COLUMN reason_text TEXT NOT NULL DEFAULT ''"
        )
    if "reason_tags_json" not in feedback_columns:
        connection.execute(
            "ALTER TABLE review_feedback ADD COLUMN reason_tags_json TEXT NOT NULL DEFAULT '[]'"
        )
    return connection


@contextmanager
def database(path: Path) -> Iterator[sqlite3.Connection]:
    connection = connect(path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()

