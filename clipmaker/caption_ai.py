from __future__ import annotations

from typing import Callable

from .db import database
from .lmstudio import lmstudio_chat
from .settings import Settings


TONE_GUIDANCE = {
    "funny": "funny and conversational",
    "chaotic": "high-energy and chaotic",
    "deadpan": "dry and deadpan",
    "clicky": "curiosity-driven without dishonest clickbait",
}


def _generate(settings: Settings, prompt: str) -> str:
    return lmstudio_chat(settings, prompt, temperature=0.8, max_tokens=180)


def generate_bulk_captions(
    settings: Settings,
    candidate_ids: list[int],
    tone: str = "funny",
    progress: Callable[[str], None] | None = None,
) -> int:
    if not candidate_ids:
        raise ValueError("Select at least one liked reel")
    placeholders = ",".join("?" for _ in candidate_ids)
    with database(settings.db_path) as connection:
        rows = connection.execute(
            f"""
            SELECT c.id, c.transcript, c.start_sec, c.end_sec, source.name AS video_name,
                   v.recipe_label
            FROM candidates c
            JOIN videos source ON source.id=c.video_id
            JOIN candidate_variants v ON v.id=(
                SELECT newest.id FROM candidate_variants newest
                WHERE newest.candidate_id=c.id AND newest.decision='liked'
                ORDER BY newest.attempt DESC LIMIT 1
            )
            WHERE c.status='approved' AND c.id IN ({placeholders})
            """,
            candidate_ids,
        ).fetchall()
    if len(rows) != len(set(candidate_ids)):
        raise ValueError("AI captions can only be generated for liked reels")

    guidance = TONE_GUIDANCE.get(tone, TONE_GUIDANCE["funny"])
    completed = 0
    for index, row in enumerate(rows, start=1):
        if progress:
            progress(f"caption {index}/{len(rows)}: writing")
        transcript = str(row["transcript"] or "").strip()[:5000]
        prompt = f"""Write one social-media caption for a short gaming reel.

Tone: {guidance}
Source recording: {row["video_name"]}
Edit style: {row["recipe_label"]}
Dialogue/transcript:
{transcript or "(No reliable dialogue was detected.)"}

Requirements:
- Return only the finished caption.
- Use 1-3 short sentences followed by 3-5 relevant hashtags.
- Hook the viewer without explaining the entire payoff.
- Sound like a real gamer, not a marketing department.
- Profanity is allowed when it naturally fits the transcript.
- Never mention AI, transcription, editing software, or confidence scores.
- Do not invent names, games, actions, or quotes that are not supported above.
"""
        caption = _generate(settings, prompt)
        with database(settings.db_path) as connection:
            connection.execute(
                """
                INSERT INTO caption_drafts(candidate_id, caption, tone)
                VALUES (?, ?, ?)
                ON CONFLICT(candidate_id) DO UPDATE SET
                    caption=excluded.caption,
                    tone=excluded.tone,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (int(row["id"]), caption, tone),
            )
        completed += 1
    return completed
