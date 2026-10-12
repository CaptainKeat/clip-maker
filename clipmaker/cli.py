from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

from .analyze import analyze_batch, analyze_video
from .db import database
from .media import scan_library
from .render import (
    decide_candidate,
    export_vertical,
    generate_preview,
    generate_variant,
    prepare_batch,
    retry_candidate,
)
from .server import serve
from .settings import DEFAULT_CONFIG_PATH, load_settings
from .transcribe import transcribe_candidate


def _progress(message: str) -> None:
    print(message, flush=True)


def _format_duration(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    return f"{hours:,}h {minutes:02d}m"


def print_stats(settings) -> None:
    with database(settings.db_path) as connection:
        video = connection.execute(
            """
            SELECT
                COUNT(*) AS files,
                SUM(CASE WHEN duplicate_of IS NOT NULL THEN 1 ELSE 0 END) AS duplicates,
                SUM(CASE WHEN duplicate_of IS NULL THEN size_bytes ELSE 0 END) AS unique_bytes,
                SUM(CASE WHEN duplicate_of IS NULL THEN duration_sec ELSE 0 END) AS unique_duration,
                SUM(CASE WHEN status = 'analyzed' THEN 1 ELSE 0 END) AS analyzed
            FROM videos
            """
        ).fetchone()
        candidates = connection.execute(
            "SELECT status, COUNT(*) AS count FROM candidates GROUP BY status"
        ).fetchall()
    print(f"Videos:       {video['files'] or 0}")
    print(f"Duplicates:   {video['duplicates'] or 0}")
    print(f"Unique size:  {(video['unique_bytes'] or 0) / (1024**4):.2f} TiB")
    print(f"Unique time:  {_format_duration(video['unique_duration'] or 0)}")
    print(f"Analyzed:     {video['analyzed'] or 0}")
    print("Candidates:   " + (", ".join(f"{r['status']}={r['count']}" for r in candidates) or "0"))


def _candidate_ids(settings, top: int) -> list[int]:
    with database(settings.db_path) as connection:
        rows = connection.execute(
            """
            SELECT id FROM candidates
            WHERE transcript = '' AND status = 'pending'
            ORDER BY score DESC LIMIT ?
            """,
            (top,),
        ).fetchall()
    return [int(row["id"]) for row in rows]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipmaker",
        description="Mine gameplay VODs for promising social clip candidates.",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help="Path to clipmaker.json",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("config", help="Print the resolved configuration")
    subparsers.add_parser("scan", help="Inventory and ffprobe the source library")
    subparsers.add_parser("stats", help="Show inventory and candidate counts")

    analyze_parser = subparsers.add_parser("analyze", help="Analyze audio and create candidates")
    analyze_parser.add_argument("video_id", type=int)
    analyze_parser.add_argument("--max-candidates", type=int, default=50)

    batch_parser = subparsers.add_parser(
        "analyze-batch", help="Analyze the next manageable batch of recordings"
    )
    batch_parser.add_argument("--limit", type=int, default=10)
    batch_parser.add_argument("--max-candidates", type=int, default=50)

    transcribe_parser = subparsers.add_parser(
        "transcribe", help="Transcribe a candidate or top untranscribed candidates"
    )
    transcribe_group = transcribe_parser.add_mutually_exclusive_group(required=True)
    transcribe_group.add_argument("--candidate-id", type=int)
    transcribe_group.add_argument("--top", type=int)

    preview_parser = subparsers.add_parser("preview", help="Render a landscape review proxy")
    preview_parser.add_argument("candidate_id", type=int)
    export_parser = subparsers.add_parser("export", help="Render a new vertical cut attempt")
    export_parser.add_argument("candidate_id", type=int)
    retry_parser = subparsers.add_parser("retry", help="Retire the current cut and build another")
    retry_parser.add_argument("candidate_id", type=int)
    decide_parser = subparsers.add_parser("decide", help="Like or hate the current cut")
    decide_parser.add_argument("candidate_id", type=int)
    decide_parser.add_argument("decision", choices=("like", "hate"))
    prepare_batch_parser = subparsers.add_parser(
        "prepare-batch", help="Build finished cuts for the next pending candidates"
    )
    prepare_batch_parser.add_argument("--limit", type=int, default=3)

    serve_parser = subparsers.add_parser("serve", help="Launch the local review UI")
    serve_parser.add_argument("--no-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    settings = load_settings(args.config)

    if args.command == "config":
        print(json.dumps(asdict(settings), indent=2))
    elif args.command == "scan":
        result = scan_library(settings, _progress)
        print(
            f"Done: {result.discovered} videos, {result.duplicates} duplicates, "
            f"{result.errors} errors, {_format_duration(result.unique_duration_sec)} unique."
        )
    elif args.command == "stats":
        print_stats(settings)
    elif args.command == "analyze":
        count = analyze_video(settings, args.video_id, args.max_candidates, _progress)
        print(f"Created {count} candidates.")
    elif args.command == "analyze-batch":
        count = analyze_batch(
            settings,
            limit=args.limit,
            max_candidates_per_video=args.max_candidates,
            progress=_progress,
        )
        print(f"Analyzed {count} recordings.")
    elif args.command == "transcribe":
        ids = (
            [args.candidate_id]
            if args.candidate_id is not None
            else _candidate_ids(settings, args.top)
        )
        for candidate_id in ids:
            text = transcribe_candidate(settings, candidate_id, _progress)
            print(f"{candidate_id}: {text}")
    elif args.command == "preview":
        print(generate_preview(settings, args.candidate_id))
    elif args.command == "export":
        print(export_vertical(settings, args.candidate_id))
    elif args.command == "retry":
        print(retry_candidate(settings, args.candidate_id, _progress))
    elif args.command == "decide":
        decide_candidate(settings, args.candidate_id, args.decision)
        print(f"Candidate {args.candidate_id}: {args.decision}")
    elif args.command == "prepare-batch":
        count = prepare_batch(settings, args.limit, progress=_progress)
        print(f"Built {count} cuts.")
    elif args.command == "serve":
        serve(settings, open_browser=not args.no_browser)
    return 0

