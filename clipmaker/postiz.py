from __future__ import annotations

import json
import mimetypes
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .db import database
from .settings import Settings


def _postiz_config(settings: Settings) -> dict[str, str]:
    values = dict(os.environ)
    env_path = Path(settings.postiz_env_file)
    if env_path.exists():
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values.setdefault(key.strip(), value.strip().strip("'\""))
    return {
        "url": values.get("POSTIZ_API_URL", "").rstrip("/"),
        "key": values.get("POSTIZ_API_KEY", ""),
        "youtube": values.get("POSTIZ_YOUTUBE_INTEGRATION_ID", ""),
        "instagram": values.get("POSTIZ_INSTAGRAM_INTEGRATION_ID", ""),
        "tiktok": values.get("POSTIZ_TIKTOK_INTEGRATION_ID", ""),
    }


def _request(settings: Settings, path: str, *, method: str = "GET", body=None, headers=None):
    config = _postiz_config(settings)
    if not config["url"] or not config["key"]:
        raise RuntimeError("Postiz is not configured")
    request_headers = {"Authorization": config["key"], **(headers or {})}
    data = body
    if body is not None and not isinstance(body, bytes):
        data = json.dumps(body).encode("utf-8")
        request_headers["Content-Type"] = "application/json"
    request = Request(
        f"{config['url']}{path}",
        data=data,
        headers=request_headers,
        method=method,
    )
    try:
        with urlopen(request, timeout=120) as response:
            payload = response.read().decode("utf-8")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"Postiz returned {exc.code}: {detail}") from exc
    return json.loads(payload) if payload else None


def list_integrations(settings: Settings) -> list[dict]:
    config = _postiz_config(settings)
    if not config["url"] or not config["key"]:
        return []
    payload = _request(settings, "/integrations")
    rows = payload if isinstance(payload, list) else (payload or {}).get("integrations", [])
    configured = {
        config["youtube"]: "youtube",
        config["instagram"]: "instagram",
        config["tiktok"]: "tiktok",
    }
    result = []
    for item in rows:
        integration_id = str(item.get("id") or "")
        haystack = " ".join(
            str(item.get(key) or "") for key in ("identifier", "provider", "type", "name")
        ).lower()
        platform = configured.get(integration_id)
        if not platform:
            platform = next(
                (name for name in ("youtube", "instagram", "tiktok") if name in haystack),
                "other",
            )
        result.append(
            {
                "id": integration_id,
                "name": item.get("name") or item.get("identifier") or platform.title(),
                "platform": platform,
                "picture": item.get("picture"),
            }
        )
    return result


def liked_exports(settings: Settings) -> list[dict]:
    with database(settings.db_path) as connection:
        rows = connection.execute(
            """
            SELECT c.id AS candidate_id, v.id AS variant_id, v.attempt, v.recipe_label,
                   v.output_path, source.name AS video_name,
                   draft.caption AS caption_draft, draft.tone AS caption_tone,
                   (SELECT COUNT(*) FROM publish_jobs p WHERE p.variant_id=v.id) AS publish_count
            FROM candidates c
            JOIN videos source ON source.id=c.video_id
            LEFT JOIN caption_drafts draft ON draft.candidate_id=c.id
            JOIN candidate_variants v ON v.id=(
                SELECT newest.id FROM candidate_variants newest
                WHERE newest.candidate_id=c.id AND newest.decision='liked'
                ORDER BY newest.attempt DESC LIMIT 1
            )
            WHERE c.status='approved' AND v.output_path IS NOT NULL
            ORDER BY c.updated_at DESC
            """
        ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["url"] = "/media/export/" + Path(item["output_path"]).name
    return result


def scheduled_exports(settings: Settings) -> list[dict]:
    with database(settings.db_path) as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.candidate_id, p.variant_id, p.platform, p.integration_id,
                   p.scheduled_for, p.status, p.created_at,
                   variant.attempt, variant.recipe_label, variant.output_path,
                   source.name AS video_name
            FROM publish_jobs p
            JOIN candidate_variants variant ON variant.id=p.variant_id
            JOIN candidates candidate ON candidate.id=p.candidate_id
            JOIN videos source ON source.id=candidate.video_id
            ORDER BY p.created_at DESC, p.id DESC
            """
        ).fetchall()
    result = [dict(row) for row in rows]
    for item in result:
        item["url"] = "/media/export/" + Path(item["output_path"]).name
    return result


def _upload(settings: Settings, path: Path) -> dict:
    boundary = f"----clipmaker-{uuid.uuid4().hex}"
    content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    prefix = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode()
    body = prefix + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    return _request(
        settings,
        "/upload",
        method="POST",
        body=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )


def _platform_settings(platform: str, title: str) -> dict:
    if platform == "youtube":
        return {
            "__type": "youtube",
            "title": title[:95],
            "type": "public",
            "selfDeclaredMadeForKids": "no",
            "tags": [{"value": "gaming", "label": "gaming"}],
        }
    if platform == "instagram":
        return {"__type": "instagram-standalone", "post_type": "post"}
    if platform == "tiktok":
        return {
            "__type": "tiktok",
            "title": title[:90],
            "privacy_level": "PUBLIC_TO_EVERYONE",
            "duet": False,
            "stitch": False,
            "comment": True,
            "autoAddMusic": "no",
            "brand_content_toggle": False,
            "brand_organic_toggle": False,
            "video_made_with_ai": False,
            "content_posting_method": "DIRECT_POST",
        }
    raise ValueError(f"Unsupported Postiz destination: {platform}")


def schedule_bulk(
    settings: Settings,
    candidate_ids: list[int],
    integration_ids: list[str],
    caption_template: str,
    schedule_type: str,
    start_at: str | None,
    interval_hours: float = 24,
    captions: dict[str, str] | None = None,
    progress: Callable[[str], None] | None = None,
) -> int:
    if not candidate_ids or not integration_ids:
        raise ValueError("Select at least one reel and one destination")
    integrations = {item["id"]: item for item in list_integrations(settings)}
    selected = [integrations[item] for item in integration_ids if item in integrations]
    if len(selected) != len(integration_ids):
        raise ValueError("One or more Postiz destinations are unavailable")
    unsupported = [item["platform"] for item in selected if item["platform"] == "other"]
    if unsupported:
        raise ValueError("One or more selected Postiz destinations are unsupported")

    exports = {item["candidate_id"]: item for item in liked_exports(settings)}
    chosen = [exports[item] for item in candidate_ids if item in exports]
    if len(chosen) != len(candidate_ids):
        raise ValueError("Only liked reels can be scheduled")

    if schedule_type == "now":
        first_time = datetime.now(timezone.utc)
    else:
        if not start_at:
            raise ValueError("Choose a starting date and time")
        first_time = datetime.fromisoformat(start_at.replace("Z", "+00:00"))
        if first_time.tzinfo is None:
            first_time = first_time.astimezone()
        first_time = first_time.astimezone(timezone.utc)

    completed = 0
    for index, item in enumerate(chosen):
        if progress:
            progress(f"publishing reel {index + 1}/{len(chosen)}: uploading")
        media = _upload(settings, Path(item["output_path"]))
        when = first_time + timedelta(hours=interval_hours * index)
        title = Path(item["video_name"]).stem.replace("_", " ").strip()
        caption = (captions or {}).get(str(item["candidate_id"])) or (captions or {}).get(item["candidate_id"])
        caption = caption or (caption_template or "{video}\n\n#gaming #gamingclips").replace(
            "{video}", title
        )
        posts = [
            {
                "integration": {"id": integration["id"]},
                "value": [{"content": caption, "image": [media]}],
                "settings": _platform_settings(integration["platform"], title),
            }
            for integration in selected
        ]
        if progress:
            progress(f"publishing reel {index + 1}/{len(chosen)}: scheduling")
        result = _request(
            settings,
            "/posts",
            method="POST",
            body={
                "type": schedule_type,
                "date": when.isoformat().replace("+00:00", "Z"),
                "shortLink": False,
                "tags": [],
                "posts": posts,
            },
        )
        with database(settings.db_path) as connection:
            for integration in selected:
                connection.execute(
                    """
                    INSERT INTO publish_jobs(
                        candidate_id, variant_id, platform, integration_id,
                        postiz_result_json, scheduled_for, status
                    ) VALUES (?, ?, ?, ?, ?, ?, 'scheduled')
                    """,
                    (
                        item["candidate_id"],
                        item["variant_id"],
                        integration["platform"],
                        integration["id"],
                        json.dumps(result),
                        when.isoformat(),
                    ),
                )
        completed += 1
    return completed
