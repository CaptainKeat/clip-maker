from __future__ import annotations

import json
import mimetypes
import re
import string
import threading
import time
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .analyze import analyze_batch, analyze_video
from .caption_ai import generate_bulk_captions
from .db import database
from .editor import editor_payload, regenerate_editor_captions, render_editor_variant, save_editor_asset, save_editor_draft
from . import studio_server
from .media import scan_library
from .lmstudio import lmstudio_state, set_lmstudio_mode
from .postiz import liked_exports, list_integrations, schedule_bulk, scheduled_exports
from .render import (
    combine_candidates,
    decide_candidate,
    export_vertical,
    generate_preview,
    generate_variant,
    prepare_batch,
    retry_candidate,
)
from .settings import Settings, save_settings, update_storage_folders
from .storage import export_media_path, export_media_url, safe_vod_folder_name
from .taste import import_examples, list_examples
from .transcribe import transcribe_candidate
from .vod_preview import generate_vod_preview, ready_preview


STATIC_DIR = Path(__file__).resolve().parent / "static"
JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def _job_kind(job_id: str) -> str:
    if job_id.startswith("vod-preview-"):
        return "vod_preview"
    if job_id.startswith("variant-") or job_id.startswith("prepare-batch") or job_id.startswith("combine-"):
        return "reel"
    if job_id.startswith("publish-"):
        return "publish"
    if job_id.startswith("caption-") or job_id.startswith("editor-caption-"):
        return "caption"
    if job_id.startswith("taste-"):
        return "taste"
    if job_id.startswith("analyze"):
        return "analysis"
    if job_id == "scan":
        return "scan"
    return "job"


def _candidate_filters(query: dict) -> tuple[str, list]:
    clauses = []
    params = []
    status = query.get("status", ["pending"])[0]
    if status != "all":
        clauses.append("c.status = ?")
        params.append(status)
    video_value = query.get("video_id", [""])[0]
    if video_value:
        clauses.append("c.video_id = ?")
        params.append(int(video_value))
    where = "WHERE " + " AND ".join(clauses) if clauses else ""
    return where, params


def _library_path_pattern(settings: Settings) -> str:
    return str(Path(settings.source_root).resolve()).rstrip("\\/") + "\\%"


def _batch_limit(value: object) -> int:
    if str(value).strip().lower() == "all":
        return 1000
    return min(1000, max(1, int(value)))


def _analyze_and_optionally_build(
    settings: Settings,
    video_id: int,
    auto_build: int = 0,
    chapter_indices: list[int] | None = None,
    progress=None,
) -> int:
    found = analyze_video(settings, video_id, progress=progress, chapter_indices=chapter_indices)
    if auto_build:
        if progress:
            label = "all" if auto_build >= 1000 else str(auto_build)
            progress(f"analysis complete; automatically building {label} best reel candidates")
        prepare_batch(settings, auto_build, video_id, progress=progress)
    return found


def _unit_progress(job: dict, fraction: float) -> int:
    current = max(1, int(job.get("current") or 1))
    total = max(1, int(job.get("total") or 1))
    return min(99, max(2, round(((current - 1) + fraction) / total * 100)))


def _update_progress(job_id: str, message: str) -> None:
    with JOBS_LOCK:
        job = JOBS[job_id]
        lower = message.lower()
        job["raw_message"] = message
        job["updated_at"] = time.time()

        if job_id.startswith("project-export-"):
            percent = re.search(r"Project export (\d+)%",message)
            if percent:
                job.update(progress=int(percent[1]),title="Exporting project",stage="Rendering",detail=message)
                return

        match = re.search(r"building cut (\d+)/(\d+)(?:.*candidate (\d+))?", lower)
        if match:
            job["current"] = int(match.group(1))
            job["total"] = int(match.group(2))
            if match.group(3):
                job["candidate_id"] = int(match.group(3))
            job["title"] = f"Making reel {job['current']} of {job['total']}"
            job["detail"] = "Choosing the best edit recipe for this moment."
            job["stage"] = "Preparing the cut"
            job["progress"] = _unit_progress(job, 0.05)
            return

        match = re.search(r"batch (\d+)/(\d+): (.+?) \(", message, re.I)
        if match:
            job["current"] = int(match.group(1))
            job["total"] = int(match.group(2))
            job["title"] = f"Scanning VOD {job['current']} of {job['total']}"
            job["detail"] = match.group(3)
            job["stage"] = "Listening for standout moments"
            job["progress"] = _unit_progress(job, 0.08)
            return

        match = re.search(r"\[(\d+)/(\d+)\] (?:unchanged|scanned): (.+)", message, re.I)
        if match:
            job["current"] = int(match.group(1))
            job["total"] = int(match.group(2))
            job["title"] = "Refreshing the footage library"
            job["detail"] = match.group(3)
            job["stage"] = f"Checking file {job['current']} of {job['total']}"
            job["progress"] = min(99, round(job["current"] / job["total"] * 100))
            return

        match = re.search(r"caption (\d+)/(\d+): writing", lower)
        if match:
            job["current"] = int(match.group(1))
            job["total"] = int(match.group(2))
            job["title"] = "Writing caption {}/{}".format(job["current"], job["total"])
            job["detail"] = "Turning this reel into an editable social caption."
            job["stage"] = "Writing captions"
            job["progress"] = _unit_progress(job, 0.45)
            return

        match = re.search(r"publishing reel (\d+)/(\d+): (uploading|scheduling)", lower)
        if match:
            job["current"] = int(match.group(1))
            job["total"] = int(match.group(2))
            phase = match.group(3)
            job["title"] = "Sending reel {}/{} to Postiz".format(job["current"], job["total"])
            job["detail"] = "Uploading the video file." if phase == "uploading" else "Creating the scheduled social posts."
            job["stage"] = "Uploading" if phase == "uploading" else "Scheduling"
            job["progress"] = _unit_progress(job, 0.28 if phase == "uploading" else 0.82)
            return

        match = re.search(r"ranking transcript (\d+)/(\d+): candidate (\d+)", lower)
        if match:
            job["current"] = int(match.group(1))
            job["total"] = int(match.group(2))
            job["candidate_id"] = int(match.group(3))
            job["title"] = f"Reading moment {job['current']} of {job['total']}"
            job["detail"] = "Matching the conversation to laughter and audio reactions."
            job["stage"] = "Understanding every possible clip"
            job["progress"] = min(82, round(40 + job["current"] / job["total"] * 42))
            return

        match = re.search(r"ai reviewing moment (\d+)/(\d+): candidate (\d+)", lower)
        if match:
            job["current"] = int(match.group(1))
            job["total"] = int(match.group(2))
            job["candidate_id"] = int(match.group(3))
            job["title"] = f"AI judging moment {job['current']} of {job['total']}"
            job["detail"] = "Finding the setup before the laugh and keeping the full landing."
            job["stage"] = "Ranking the strongest moments"
            job["progress"] = min(98, round(82 + job["current"] / job["total"] * 16))
            return

        if "loading local parakeet model" in lower:
            job["title"] = "Building fresh subtitles"
            job["detail"] = "Loading the speech-timing engine. Your existing subtitles stay in place until this finishes."
            job["stage"] = "Loading subtitle engine"
            job["progress"] = 8
        elif "editor subtitles ready" in lower:
            job["title"] = "Subtitles are almost ready"
            job["detail"] = "Saving the new words and exact timing into the editor."
            job["stage"] = "Finishing subtitles"
            job["progress"] = 97
        elif "extracting audio track" in lower:
            if job.get("kind") == "analysis" and int(job.get("total") or 1) > 1:
                job["title"] = f"Reading moment {job.get('current', 1)} of {job.get('total', 1)}"
                job["detail"] = "Separating the likely mic and game tracks for transcription."
                job["stage"] = "Understanding every possible clip"
                job["progress"] = min(82, round(40 + ((job.get("current", 1) - 0.8) / job["total"]) * 42))
            else:
                job["title"] = (
                    f"Making reel {job.get('current', 1)} of {job.get('total', 1)}"
                    if int(job.get("total") or 1) > 1
                    else "Making your reel"
                )
                job["detail"] = "Separating dialogue and game audio for a cleaner result."
                job["stage"] = "Preparing audio"
                job["progress"] = _unit_progress(job, 0.18)
        elif "whisper.cpp" in lower:
            job["detail"] = "Listening for dialogue and lining captions up with the action."
            job["stage"] = "Timing captions"
            job["progress"] = _unit_progress(job, 0.42)
        elif "parakeet chunk" in lower:
            match = re.search(r"(\d+)/(\d+)", lower)
            fraction = int(match.group(1)) / int(match.group(2)) if match else 0.5
            if job.get("kind") == "analysis" and int(job.get("total") or 1) > 1:
                job["detail"] = "Reading the words and listening for laughter or reactions."
                job["stage"] = "Understanding every possible clip"
                completed = (job.get("current", 1) - 1 + fraction) / job["total"]
                job["progress"] = min(82, round(40 + completed * 42))
            else:
                job["title"] = "Timing subtitle "+str(match.group(1) if match else 1)+" of "+str(match.group(2) if match else 1)
                job["detail"] = "Listening for dialogue and lining captions up with the action."
                job["stage"] = "Timing captions"
                job["progress"] = _unit_progress(job, 0.15 + fraction * 0.75)
        elif "rendering attempt" in lower:
            job["detail"] = message.split(":", 1)[-1].strip()
            job["stage"] = "Rendering the vertical reel"
            job["progress"] = _unit_progress(job, 0.76)
        elif "attempt" in lower and "ready for review" in lower:
            job["detail"] = "Finishing the video file and adding it to your review queue."
            job["stage"] = "Finalizing"
            job["progress"] = _unit_progress(job, 0.96)
        elif "track " in lower and "median" in lower:
            job["detail"] = "Measuring reactions across the available audio tracks."
            job["stage"] = "Listening for standout moments"
            job["progress"] = _unit_progress(job, 0.52)
        elif "created " in lower and "candidate windows" in lower:
            job["detail"] = message.replace("candidate windows", "possible moments")
            job["stage"] = "Preparing transcripts"
            job["progress"] = 40
        elif "preparing vod preview" in lower:
            job["title"] = "Preparing your VOD preview"
            job["detail"] = "Making a lightweight 45-second sample from the middle of the recording."
            job["stage"] = "Creating preview"
            job["progress"] = 45
        elif "vod preview ready" in lower:
            job["detail"] = "The preview is ready. Analysis has not started."
            job["stage"] = "Finalizing preview"
            job["progress"] = 96
        elif "warning" in lower or "error" in lower:
            job["detail"] = message
            job["stage"] = "Continuing after a warning"
        else:
            job["detail"] = message
            job["stage"] = "Working"


def _job(job_id: str, target, *args) -> None:
    kind = _job_kind(job_id)
    candidate_match = re.fullmatch(r"variant-(\d+)", job_id)
    initial_title = {
        "reel": "Getting the reel ready",
        "analysis": "Getting ready to scan footage",
        "scan": "Getting ready to refresh the library",
        "publish": "Getting the selected reels ready for Postiz",
        "caption": "Getting ready to write captions",
        "taste": "Getting comedy references ready",
        "vod_preview": "Preparing your VOD preview",
    }.get(kind, "Getting started")
    if job_id.startswith("editor-caption-"):
        initial_title = "Preparing auto subtitles"
    with JOBS_LOCK:
        JOBS[job_id] = {
            "id": job_id,
            "kind": kind,
            "status": "running",
            "title": initial_title,
            "detail": "Starting the local pipeline.",
            "stage": "Starting",
            "progress": 2,
            "current": 1,
            "total": 1,
            "candidate_id": int(candidate_match.group(1)) if candidate_match else None,
            "started_at": time.time(),
            "updated_at": time.time(),
        }

    def progress(message: str) -> None:
        _update_progress(job_id, message)

    try:
        result = target(*args, progress=progress)
        with JOBS_LOCK:
            job = JOBS[job_id]
            job.update(
                {
                    "status": "complete",
                    "title": (
                        f"{result} reels are ready to review"
                        if job_id.startswith("prepare-batch")
                        else (
                            "Reel ready to review"
                            if kind == "reel"
                            else (
                                "Moment scan finished"
                                if kind == "analysis"
                                else (
                                    "VOD preview ready"
                                    if kind == "vod_preview"
                                    else ("Subtitles ready" if job_id.startswith("editor-caption-") else "Library is up to date")
                                )
                            )
                        )
                    ),
                    "detail": (
                        "Watch the finished cut and choose Like, Hate, or Try Again."
                        if kind == "reel"
                        else (
                            "Watch the sample, then choose whether to analyze this VOD."
                            if kind == "vod_preview"
                            else ("The new subtitles are ready to edit." if job_id.startswith("editor-caption-") else "The results are now in the dashboard.")
                        )
                    ),
                    "stage": "Complete",
                    "progress": 100,
                    "completed_at": time.time(),
                    "updated_at": time.time(),
                    "result": result.__dict__ if hasattr(result, "__dict__") else str(result),
                }
            )
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS[job_id]
            job.update(
                {
                    "status": "error",
                    "title": "Publishing failed" if kind == "publish" else ("Reel failed" if kind == "reel" else ("VOD preview failed" if kind == "vod_preview" else "Job failed")),
                    "detail": str(exc),
                    "stage": "Needs attention",
                    "error": str(exc),
                    "completed_at": time.time(),
                    "updated_at": time.time(),
                    "traceback": traceback.format_exc(),
                }
            )


def _start_job(job_id: str, target, *args) -> bool:
    with JOBS_LOCK:
        existing = JOBS.get(job_id)
        if existing and existing["status"] == "running":
            return False
    thread = threading.Thread(target=_job, args=(job_id, target, *args), daemon=True)
    thread.start()
    return True


def _directory_listing(requested_path: str = "") -> dict:
    """Return Windows drives or the child folders of one absolute directory."""
    if not str(requested_path or "").strip():
        drives = []
        for letter in string.ascii_uppercase:
            drive = Path(f"{letter}:\\")
            if drive.exists():
                drives.append({"name": f"{letter}:", "path": str(drive), "drive": True})
        return {"current": "This PC", "parent": None, "folders": drives, "drives": True}

    current = Path(str(requested_path).strip()).expanduser()
    if not current.is_absolute() or not current.exists() or not current.is_dir():
        raise ValueError(f"Folder does not exist: {current}")
    current = current.resolve()
    folders = []
    try:
        for child in current.iterdir():
            try:
                if child.is_dir():
                    folders.append({"name": child.name, "path": str(child), "drive": False})
            except (OSError, PermissionError):
                continue
    except PermissionError as exc:
        raise ValueError(f"Windows denied access to: {current}") from exc
    folders.sort(key=lambda item: item["name"].casefold())
    parent = current.parent
    return {
        "current": str(current),
        "parent": str(parent) if parent != current else "",
        "folders": folders,
        "drives": False,
    }

class ClipMakerHandler(BaseHTTPRequestHandler):
    settings: Settings

    def log_message(self, format_string: str, *args) -> None:
        print(f"[web] {self.address_string()} {format_string % args}")

    def _json(self, payload, status=HTTPStatus.OK) -> None:
        data = json.dumps(payload, default=dict).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0"))
        if not length:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _serve_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        size = path.stat().st_size
        start = 0
        end = size - 1
        range_header = self.headers.get("Range")
        if range_header:
            match = re.match(r"bytes=(\d*)-(\d*)", range_header)
            if match:
                if match.group(1):
                    start = int(match.group(1))
                if match.group(2):
                    end = min(end, int(match.group(2)))
        length = max(0, end - start + 1)
        self.send_response(HTTPStatus.PARTIAL_CONTENT if range_header else HTTPStatus.OK)
        self.send_header(
            "Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if range_header:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with path.open("rb") as handle:
            handle.seek(start)
            remaining = length
            while remaining:
                chunk = handle.read(min(1024 * 1024, remaining))
                if not chunk:
                    break
                try: self.wfile.write(chunk)
                except ConnectionError: return  # Preview changes can cancel a media response.
                remaining -= len(chunk)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_file(STATIC_DIR / "studio.html")
        elif parsed.path in {"/studio.js","/studio.css","/studio-connectors.js","/studio-editing.js","/studio-workspace.js","/studio-effects.js","/studio-handheld.js"}:
            self._serve_file(STATIC_DIR / parsed.path[1:])
        elif parsed.path == '/clipmaker.ico':
            self._serve_file(Path(getattr(__import__('sys'),'_MEIPASS',Path(__file__).resolve().parent.parent))/'assets'/'clipmaker.ico')
        elif parsed.path.startswith("/studio/"):
            try:
                if not studio_server.get(self,parsed): self.send_error(HTTPStatus.NOT_FOUND)
            except (ValueError,OSError) as error:
                self._json({"error":str(error)},HTTPStatus.BAD_REQUEST)
        elif parsed.path == "/ai":
            self._serve_file(STATIC_DIR / "index.html")
        elif parsed.path == "/app.css":
            self._serve_file(STATIC_DIR / "app.css")
        elif parsed.path == "/app.js":
            self._serve_file(STATIC_DIR / "app.js")
        elif parsed.path == "/publish.js":
            self._serve_file(STATIC_DIR / "publish.js")
        elif parsed.path == "/editor.js":
            self._serve_file(STATIC_DIR / "editor.js")
        elif parsed.path == "/motion-captions.js":
            self._serve_file(STATIC_DIR / "motion-captions.js")
        elif parsed.path == "/api/stats":
            self._stats()
        elif parsed.path == "/api/settings/audio":
            self._json({"mic_audio_ordinal": int(self.settings.mic_audio_ordinal)})
        elif parsed.path == "/api/settings/folders":
            source = Path(self.settings.source_root)
            output = Path(self.settings.output_dir)
            self._json({
                "source_root": self.settings.source_root,
                "output_dir": self.settings.output_dir,
                "source_exists": source.exists() and source.is_dir(),
                "output_exists": output.exists() and output.is_dir(),
            })
        elif parsed.path == "/api/lmstudio":
            if not self.settings.ai_enabled:
                self._json({"ok":True,"model_loaded":False,"server_up":False,"loaded_models":[],"mode":"off"})
                return
            try:
                self._json(lmstudio_state(self.settings))
            except Exception as exc:
                self._json({"ok": False, "error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        elif parsed.path == "/api/videos":
            self._videos(parse_qs(parsed.query))
        elif parsed.path == "/api/candidates":
            self._candidates(parse_qs(parsed.query))
        elif (match := re.fullmatch(r"/api/candidates/(\d+)/editor", parsed.path)):
            self._json(editor_payload(self.settings, int(match.group(1))))
        elif parsed.path == "/api/exports":
            self._json(liked_exports(self.settings))
        elif parsed.path == "/api/scheduled":
            self._json(scheduled_exports(self.settings))
        elif parsed.path == "/api/postiz/integrations":
            try:
                self._json({"configured": True, "integrations": list_integrations(self.settings)})
            except Exception as exc:
                self._json({"configured": False, "integrations": [], "error": str(exc)}, HTTPStatus.SERVICE_UNAVAILABLE)
        elif parsed.path == "/api/jobs":
            with JOBS_LOCK:
                self._json(JOBS.copy())
        elif parsed.path == "/api/taste/examples":
            self._json(list_examples(self.settings))
        elif parsed.path.startswith("/media/preview/"):
            name = Path(parsed.path).name
            self._serve_file(self.settings.preview_dir / name)
        elif parsed.path.startswith("/media/vod-preview/"):
            name = Path(parsed.path).name
            self._serve_file(self.settings.vod_preview_dir / name)
        elif parsed.path.startswith("/media/editor-asset/"):
            name = Path(parsed.path).name
            self._serve_file(Path(self.settings.data_dir) / "editor-assets" / name)
        elif parsed.path.startswith("/media/export/"):
            encoded_path = parsed.path.removeprefix("/media/export/")
            try:
                self._serve_file(export_media_path(self.settings.output_dir, encoded_path))
            except ValueError:
                self.send_error(HTTPStatus.BAD_REQUEST)
        elif (match := re.fullmatch(r"/media/source/(\d+)", parsed.path)):
            with database(self.settings.db_path) as connection:
                source = connection.execute("SELECT path FROM videos WHERE id=?", (int(match.group(1)),)).fetchone()
            if not source:
                self.send_error(HTTPStatus.NOT_FOUND)
            else:
                self._serve_file(Path(source["path"]))
        else:
            self.send_error(HTTPStatus.NOT_FOUND)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        try:
            origin = self.headers.get("Origin")
            if origin and origin != "http://" + self.headers.get("Host", ""):
                self._json({"error":"Requests must come from this Clip Maker window"},HTTPStatus.FORBIDDEN)
                return
            if studio_server.post(self,parsed,_start_job,JOBS,JOBS_LOCK): return
            ai_action = (parsed.path in {"/api/analyze","/api/analyze-batch","/api/prepare-batch","/api/taste/import","/api/captions/generate"}
                         or parsed.path.startswith("/api/lmstudio/")
                         or re.search(r"/(subtitles|transcribe|prepare|retry)$",parsed.path))
            if ai_action and not self.settings.ai_enabled:
                self._json({"error":"AI tools are off. Enable them in Settings if you want to use this tool."},HTTPStatus.CONFLICT)
                return
            if parsed.path == "/api/settings/folders/list":
                body = self._body()
                self._json(_directory_listing(str(body.get("path") or "")))
                return
            if parsed.path == "/api/settings/folders":
                with JOBS_LOCK:
                    running = any(job.get("status") == "running" for job in JOBS.values())
                if running:
                    self._json({"error": "Wait for the current Clip Maker job to finish before changing folders"}, HTTPStatus.CONFLICT)
                    return
                body = self._body()
                result = update_storage_folders(
                    self.settings,
                    str(body.get("source_root") or ""),
                    str(body.get("output_dir") or ""),
                )
                result.update({"source_exists": True, "output_exists": True})
                self._json(result)
                return
            if parsed.path == "/api/settings/audio":
                body = self._body()
                ordinal = int(body.get("mic_audio_ordinal", -1))
                if ordinal < -1 or ordinal > 31:
                    raise ValueError("Microphone track must be Auto or a valid OBS audio track")
                self.settings.mic_audio_ordinal = ordinal
                save_settings(self.settings)
                self._json({"mic_audio_ordinal": ordinal})
                return
            match = re.fullmatch(r"/api/lmstudio/(ai|gaming)", parsed.path)
            if match:
                self._json(set_lmstudio_mode(self.settings, match.group(1)))
                return
            if parsed.path == "/api/taste/import":
                body = self._body()
                urls = body.get("urls") or []
                job_id = f"taste-{time.time_ns()}"
                started = _start_job(
                    job_id, import_examples, self.settings,
                    [str(item) for item in urls],
                    str(body.get("creator") or ""),
                    str(body.get("notes") or ""),
                )
                self._json({"started": started, "job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/captions/generate":
                body = self._body()
                job_id = f"caption-{time.time_ns()}"
                started = _start_job(
                    job_id, generate_bulk_captions, self.settings,
                    [int(item) for item in body.get("candidate_ids", [])],
                    str(body.get("tone") or "funny"),
                )
                self._json({"started": started, "job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/postiz/schedule":
                body = self._body()
                job_id = f"publish-{time.time_ns()}"
                started = _start_job(
                    job_id, schedule_bulk, self.settings,
                    [int(item) for item in body.get("candidate_ids", [])],
                    [str(item) for item in body.get("integration_ids", [])],
                    str(body.get("caption_template") or ""),
                    str(body.get("schedule_type") or "schedule"),
                    body.get("start_at"),
                    float(body.get("interval_hours") or 24),
                    {str(key): str(value) for key, value in (body.get("captions") or {}).items()},
                )
                self._json({"started": started, "job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/scan":
                started = _start_job("scan", scan_library, self.settings)
                self._json({"started": started}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/analyze-batch":
                body = self._body()
                limit = min(25, max(1, int(body.get("limit", 10))))
                started = _start_job(
                    "analyze-batch", analyze_batch, self.settings, limit
                )
                self._json({"started": started}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/combine":
                body = self._body()
                candidate_ids = [int(item) for item in body.get("candidate_ids", [])]
                job_id = f"combine-{time.time_ns()}"
                started = _start_job(job_id, combine_candidates, self.settings, candidate_ids)
                self._json({"started": started, "job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/prepare-batch":
                body = self._body()
                limit = _batch_limit(body.get("limit", 3))
                video_id = int(body["video_id"]) if body.get("video_id") else None
                job_id = f"prepare-batch-{video_id or 'all'}-{time.time_ns()}"
                started = _start_job(job_id, prepare_batch, self.settings, limit, video_id)
                self._json({"started": started, "job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            if parsed.path == "/api/analyze":
                body = self._body()
                video_id = int(body["video_id"])
                with database(self.settings.db_path) as connection:
                    video = connection.execute(
                        "SELECT name, clip_folder_name FROM videos WHERE id=?", (video_id,)
                    ).fetchone()
                    if not video:
                        raise ValueError(f"VOD {video_id} does not exist")
                    requested_folder = body.get("clip_folder_name")
                    clip_folder_name = safe_vod_folder_name(
                        requested_folder if requested_folder is not None else video["clip_folder_name"],
                        str(video["name"]),
                    )
                    connection.execute(
                        "UPDATE videos SET clip_folder_name=? WHERE id=?",
                        (clip_folder_name, video_id),
                    )
                auto_build = (
                    _batch_limit(body["auto_build"])
                    if str(body.get("auto_build", "0")).strip().lower() != "0"
                    else 0
                )
                chapter_indices = [int(item) for item in body.get("chapter_indices", [])] if "chapter_indices" in body else None
                started = _start_job(
                    f"analyze-{video_id}",
                    _analyze_and_optionally_build,
                    self.settings,
                    video_id,
                    auto_build,
                    chapter_indices,
                )
                self._json(
                    {"started": started, "clip_folder_name": clip_folder_name},
                    HTTPStatus.ACCEPTED,
                )
                return
            match = re.fullmatch(r"/api/videos/(\d+)/preview", parsed.path)
            if match:
                video_id = int(match.group(1))
                job_id = f"vod-preview-{video_id}"
                started = _start_job(job_id, generate_vod_preview, self.settings, video_id)
                self._json({"started": started, "job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            asset_match = re.fullmatch(r"/api/candidates/(\d+)/editor/asset", parsed.path)
            if asset_match:
                self._json(save_editor_asset(self.settings, int(asset_match.group(1)), self._body()))
                return
            editor_match = re.fullmatch(r"/api/candidates/(\d+)/editor/(save|subtitles|render)", parsed.path)
            if editor_match:
                candidate_id = int(editor_match.group(1))
                editor_action = editor_match.group(2)
                if editor_action == "save":
                    self._json(save_editor_draft(self.settings, candidate_id, self._body()))
                elif editor_action == "subtitles":
                    job_id = f"editor-caption-{candidate_id}-{time.time_ns()}"
                    started = _start_job(job_id, regenerate_editor_captions, self.settings, candidate_id)
                    self._json({"started": started, "job_id": job_id}, HTTPStatus.ACCEPTED)
                else:
                    job_id = f"variant-{candidate_id}"
                    started = _start_job(job_id, render_editor_variant, self.settings, candidate_id)
                    self._json({"started": started, "job_id": job_id}, HTTPStatus.ACCEPTED)
                return
            match = re.fullmatch(r"/api/candidates/(\d+)/(status|preview|transcribe|export|prepare|decision|retry)", parsed.path)
            if not match:
                self.send_error(HTTPStatus.NOT_FOUND)
                return
            candidate_id = int(match.group(1))
            action = match.group(2)
            if action == "prepare":
                started = _start_job(
                    f"variant-{candidate_id}", generate_variant, self.settings, candidate_id
                )
                self._json({"started": started}, HTTPStatus.ACCEPTED)
            elif action == "decision":
                body = self._body()
                decision = str(body.get("decision") or "")
                reason_tags = body.get("reason_tags") or []
                if not isinstance(reason_tags, list):
                    reason_tags = []
                decide_candidate(
                    self.settings,
                    candidate_id,
                    decision,
                    str(body.get("reason") or ""),
                    [str(tag) for tag in reason_tags],
                )
                self._json({"ok": True})
            elif action == "retry":
                started = _start_job(
                    f"variant-{candidate_id}", retry_candidate, self.settings, candidate_id
                )
                self._json({"started": started}, HTTPStatus.ACCEPTED)
            elif action == "status":
                status = self._body().get("status")
                if status not in {"pending", "approved", "rejected"}:
                    self._json({"error": "Invalid status"}, HTTPStatus.BAD_REQUEST)
                    return
                with database(self.settings.db_path) as connection:
                    connection.execute(
                        """
                        UPDATE candidates SET status=?, updated_at=CURRENT_TIMESTAMP
                        WHERE id=?
                        """,
                        (status, candidate_id),
                    )
                self._json({"ok": True})
            elif action == "preview":
                path = generate_preview(self.settings, candidate_id)
                self._json({"url": f"/media/preview/{path.name}"})
            elif action == "transcribe":
                started = _start_job(
                    f"transcribe-{candidate_id}",
                    transcribe_candidate,
                    self.settings,
                    candidate_id,
                )
                self._json({"started": started}, HTTPStatus.ACCEPTED)
            elif action == "export":
                path = export_vertical(self.settings, candidate_id)
                self._json({"url": export_media_url(self.settings.output_dir, path)})
        except Exception as exc:
            self._json({"error": str(exc)}, HTTPStatus.INTERNAL_SERVER_ERROR)

    def _stats(self) -> None:
        with database(self.settings.db_path) as connection:
            row = connection.execute(
                """
                SELECT
                    COUNT(*) AS videos,
                    SUM(CASE WHEN duplicate_of IS NULL THEN 1 ELSE 0 END) AS unique_videos,
                    SUM(CASE WHEN duplicate_of IS NOT NULL THEN 1 ELSE 0 END) AS duplicates,
                    SUM(CASE WHEN duplicate_of IS NOT NULL THEN size_bytes ELSE 0 END)
                        AS duplicate_bytes,
                    SUM(CASE WHEN duplicate_of IS NULL THEN duration_sec ELSE 0 END)
                        AS duration_sec,
                    SUM(CASE WHEN status = 'analyzed' THEN 1 ELSE 0 END) AS analyzed
                FROM videos
                WHERE path LIKE ?
                """,
                (_library_path_pattern(self.settings),),
            ).fetchone()
            candidate_rows = connection.execute(
                """
                SELECT c.status, COUNT(*) AS count
                FROM candidates c JOIN videos v ON v.id = c.video_id
                WHERE v.path LIKE ?
                GROUP BY c.status
                """,
                (_library_path_pattern(self.settings),),
            ).fetchall()
        payload = dict(row)
        payload["candidates"] = {item["status"]: item["count"] for item in candidate_rows}
        self._json(payload)

    def _videos(self, query: dict) -> None:
        limit = min(500, max(1, int(query.get("limit", ["250"])[0])))
        with database(self.settings.db_path) as connection:
            rows = connection.execute(
                """
                SELECT id, path, mtime_ns, name, rel_path, duration_sec, size_bytes, status,
                       audio_streams_json, chapters_json, clip_folder_name, last_error,
                       (SELECT COUNT(*) FROM candidates c WHERE c.video_id = videos.id) AS candidate_count
                FROM videos
                WHERE duplicate_of IS NULL AND path LIKE ?
                ORDER BY duration_sec DESC
                LIMIT ?
                """,
                (_library_path_pattern(self.settings), limit),
            ).fetchall()
        payload = []
        for row in rows:
            item = dict(row)
            source_path = item.pop("path")
            source_mtime_ns = item.pop("mtime_ns")
            item["audio_streams"] = json.loads(item.pop("audio_streams_json"))
            item["chapters"] = json.loads(item.pop("chapters_json") or "[]")
            item["clip_folder_name"] = safe_vod_folder_name(
                item.get("clip_folder_name"), item["name"]
            )
            preview = ready_preview(self.settings, item["id"], source_path, source_mtime_ns)
            item["preview_url"] = f"/media/vod-preview/{preview.name}" if preview else None
            payload.append(item)
        self._json(payload)

    def _candidates(self, query: dict) -> None:
        limit = min(250, max(1, int(query.get("limit", ["100"])[0])))
        where, params = _candidate_filters(query)
        where += (" AND " if where else "WHERE ") + "v.path LIKE ?"
        params.extend([_library_path_pattern(self.settings), limit])
        with database(self.settings.db_path) as connection:
            rows = connection.execute(
                f"""
                SELECT c.*, v.name AS video_name, v.rel_path AS video_rel_path,
                       (SELECT COUNT(*) FROM publish_jobs p WHERE p.candidate_id=c.id) AS publish_count,
                       (SELECT GROUP_CONCAT(DISTINCT p.platform) FROM publish_jobs p WHERE p.candidate_id=c.id) AS publish_platforms
                FROM candidates c
                JOIN videos v ON v.id = c.video_id
                {where}
                ORDER BY c.score DESC, c.id
                LIMIT ?
                """,
                params,
            ).fetchall()
        payload = []
        candidate_ids = [int(row["id"]) for row in rows]
        variants_by_candidate: dict[int, list[dict]] = {candidate_id: [] for candidate_id in candidate_ids}
        if candidate_ids:
            placeholders = ",".join("?" for _ in candidate_ids)
            with database(self.settings.db_path) as connection:
                variant_rows = connection.execute(
                    f"""
                    SELECT * FROM candidate_variants
                    WHERE candidate_id IN ({placeholders})
                    ORDER BY candidate_id, attempt DESC
                    """,
                    candidate_ids,
                ).fetchall()
            for variant_row in variant_rows:
                variant = dict(variant_row)
                if variant.get("output_path"):
                    variant["url"] = export_media_url(self.settings.output_dir, variant["output_path"])
                variants_by_candidate[int(variant["candidate_id"])].append(variant)
        for row in rows:
            item = dict(row)
            if item.get("preview_path"):
                item["preview_url"] = f"/media/preview/{Path(item['preview_path']).name}"
            if item.get("export_path"):
                item["export_url"] = export_media_url(self.settings.output_dir, item["export_path"])
            item["variants"] = variants_by_candidate.get(int(item["id"]), [])
            item["active_variant"] = item["variants"][0] if item["variants"] else None
            payload.append(item)
        self._json(payload)


def serve(settings: Settings, open_browser: bool = True) -> None:
    settings.ensure_directories()
    handler = type("ConfiguredClipMakerHandler", (ClipMakerHandler,), {"settings": settings})
    addresses = [(settings.host, settings.port)]
    if settings.tailscale_host and settings.tailscale_host != settings.host:
        addresses.append((settings.tailscale_host, settings.port))
    servers = []
    bound_addresses = []
    for index, address in enumerate(addresses):
        try:
            servers.append(ThreadingHTTPServer(address, handler))
            bound_addresses.append(address)
        except OSError as exc:
            if index == 0:
                raise
            print(f"[web] Skipping unavailable secondary address {address[0]}:{address[1]}: {exc}")
    local_url = f"http://{settings.host}:{settings.port}"
    print("Clip Maker is running at " + " and ".join(
        f"http://{host}:{port}" for host, port in bound_addresses
    ))
    for extra_server in servers[1:]:
        threading.Thread(target=extra_server.serve_forever, daemon=True).start()
    if open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(local_url)).start()
    try:
        servers[0].serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Clip Maker.")
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()

