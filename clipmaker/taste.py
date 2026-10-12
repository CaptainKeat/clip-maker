from __future__ import annotations

import math
import re
import shutil
import subprocess
from collections import Counter
from pathlib import Path
from typing import Callable

from .db import database
from .media import CREATE_NO_WINDOW
from .settings import Settings
from .transcribe import LAUGHTER_WORDS, REACTION_WORDS, _transcribe_parakeet, _transcribe_whisper, music_repetition_penalty

STOPWORDS = {"the", "and", "that", "this", "with", "you", "your", "for", "are", "was", "but", "not", "have", "just", "like", "what", "when", "they", "from", "there", "then", "will", "would", "could", "about", "into", "out", "get", "got", "its"}
PROFANITY = re.compile(r"\b(?:fuck|shit|bitch|ass|damn|wtf)\b", re.I)


def _feature_vector(text: str) -> Counter[str]:
    tokens = [token for token in re.findall(r"[a-z']+", text.lower()) if len(token) > 2 and token not in STOPWORDS]
    vector: Counter[str] = Counter(tokens)
    word_count = max(1, len(tokens))
    vector["__laughter"] = round(len(LAUGHTER_WORDS.findall(text)) / word_count * 40, 3)
    vector["__reaction"] = round(len(REACTION_WORDS.findall(text)) / word_count * 18, 3)
    vector["__profanity"] = round(len(PROFANITY.findall(text)) / word_count * 22, 3)
    vector["__questions"] = round(text.count("?") / word_count * 25, 3)
    vector["__exclaims"] = round(text.count("!") / word_count * 25, 3)
    return vector


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    common = left.keys() & right.keys()
    dot = sum(float(left[key]) * float(right[key]) for key in common)
    left_norm = math.sqrt(sum(float(value) ** 2 for value in left.values()))
    right_norm = math.sqrt(sum(float(value) ** 2 for value in right.values()))
    return dot / (left_norm * right_norm) if left_norm and right_norm else 0.0


def taste_match(connection, text: str) -> tuple[float, int]:
    positives = [str(row["transcript"]) for row in connection.execute("""
        SELECT transcript FROM taste_examples WHERE status='ready' AND trim(transcript) <> ''
        UNION ALL
        SELECT c.transcript FROM review_feedback f JOIN candidates c ON c.id=f.candidate_id
        WHERE f.moment_quality=1 AND trim(c.transcript) <> ''
        UNION ALL
        SELECT transcript FROM archived_taste_feedback
        WHERE moment_quality=1 AND trim(transcript) <> ''
    """)]
    negatives = [str(row["transcript"]) for row in connection.execute("""
        SELECT c.transcript FROM review_feedback f JOIN candidates c ON c.id=f.candidate_id
        WHERE f.moment_quality=0 AND trim(c.transcript) <> ''
        UNION ALL
        SELECT transcript FROM archived_taste_feedback
        WHERE moment_quality=0 AND trim(transcript) <> ''
    """)]
    if not positives and not negatives:
        return 0.0, 0
    candidate = _feature_vector(text)
    positive_scores = sorted((_cosine(candidate, _feature_vector(item)) for item in positives), reverse=True)
    negative_scores = sorted((_cosine(candidate, _feature_vector(item)) for item in negatives), reverse=True)
    positive = sum(positive_scores[:3]) / max(1, min(3, len(positive_scores)))
    negative = sum(negative_scores[:3]) / max(1, min(3, len(negative_scores)))
    return max(0.0, min(1.0, 0.5 + (positive - negative) * 0.75)), len(positives) + len(negatives)


def rescore_pending(settings: Settings) -> int:
    with database(settings.db_path) as connection:
        rows = connection.execute("SELECT id, transcript, energy_score, transcript_score FROM candidates WHERE status='pending' AND trim(transcript) <> ''").fetchall()
        updated = 0
        for row in rows:
            taste_score, evidence = taste_match(connection, str(row["transcript"]))
            if not evidence:
                continue
            score = min(1.0, float(row["energy_score"]) * 0.36 + float(row["transcript_score"]) * 0.34 + taste_score * 0.30)
            connection.execute("UPDATE candidates SET taste_score=?, score=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (taste_score, score, int(row["id"])))
            updated += 1
    return updated


def list_examples(settings: Settings) -> list[dict]:
    with database(settings.db_path) as connection:
        rows = connection.execute("""
            SELECT id, creator, source_url, source_title, notes, status, error,
                   length(transcript) AS transcript_chars, created_at, analyzed_at
            FROM taste_examples ORDER BY id DESC
        """).fetchall()
    return [dict(row) for row in rows]


def import_examples(settings: Settings, urls: list[str], creator: str = "", notes: str = "", progress: Callable[[str], None] | None = None) -> int:
    cleaned = list(dict.fromkeys(url.strip() for url in urls if url.strip()))
    if not cleaned:
        raise ValueError("Paste at least one public clip URL")
    if len(cleaned) > 20:
        raise ValueError("Import up to 20 references at a time")
    for url in cleaned:
        if not re.match(r"^https?://", url, re.I):
            raise ValueError(f"Not a web URL: {url}")
    reference_dir = Path(settings.data_dir) / "taste-references"
    reference_dir.mkdir(parents=True, exist_ok=True)
    ytdlp = shutil.which("yt-dlp")
    if not ytdlp:
        raise RuntimeError("yt-dlp is not installed")
    completed = 0
    for index, url in enumerate(cleaned, start=1):
        if progress:
            progress(f"reference {index}/{len(cleaned)}: downloading")
        with database(settings.db_path) as connection:
            connection.execute("""
                INSERT INTO taste_examples(creator, source_url, notes, status, error)
                VALUES (?, ?, ?, 'downloading', NULL)
                ON CONFLICT(source_url) DO UPDATE SET creator=excluded.creator, notes=excluded.notes, status='downloading', error=NULL
            """, (creator.strip(), url, notes.strip()))
            example_id = int(connection.execute("SELECT id FROM taste_examples WHERE source_url=?", (url,)).fetchone()["id"])
        output_template = str(reference_dir / f"reference-{example_id}.%(ext)s")
        try:
            result = subprocess.run([ytdlp, "--no-playlist", "--max-filesize", "250M", "--match-filter", "duration <= 300", "--print", "after_move:filepath", "--print", "title", "-o", output_template, url], check=True, capture_output=True, text=True, creationflags=CREATE_NO_WINDOW)
            output_lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
            media_path = next((Path(line) for line in reversed(output_lines) if Path(line).exists()), None)
            if media_path is None:
                matches = list(reference_dir.glob(f"reference-{example_id}.*"))
                media_path = matches[0] if matches else None
            if media_path is None:
                raise RuntimeError("The platform did not return a downloadable clip")
            title = next((line for line in output_lines if line != str(media_path)), media_path.stem)
            if progress:
                progress(f"reference {index}/{len(cleaned)}: transcribing")
            wav_path = settings.work_dir / f"taste-reference-{example_id}.wav"
            subprocess.run([settings.ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(media_path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav_path)], check=True, creationflags=CREATE_NO_WINDOW)
            try:
                if settings.transcription_engine == "parakeet":
                    _payload, transcript = _transcribe_parakeet(settings, wav_path, progress)
                else:
                    _payload, transcript = _transcribe_whisper(settings, wav_path, settings.work_dir / f"taste-reference-{example_id}-transcript", progress)
            finally:
                wav_path.unlink(missing_ok=True)
            if music_repetition_penalty(transcript) > 0.75:
                raise RuntimeError("This looks music-heavy rather than a usable comedy reference")
            with database(settings.db_path) as connection:
                connection.execute("""
                    UPDATE taste_examples SET source_title=?, local_path=?, transcript=?, status='ready', error=NULL, analyzed_at=CURRENT_TIMESTAMP WHERE id=?
                """, (title, str(media_path), transcript, example_id))
            completed += 1
        except Exception as exc:
            with database(settings.db_path) as connection:
                connection.execute("UPDATE taste_examples SET status='error', error=? WHERE id=?", (str(exc)[-800:], example_id))
            if progress:
                progress(f"reference {index}/{len(cleaned)} warning: {exc}")
    rescored = rescore_pending(settings)
    if progress:
        progress(f"taste library ready: {completed} imported, {rescored} moments rescored")
    if not completed:
        raise RuntimeError("None of those links could be imported; check the reference list for details")
    return completed
