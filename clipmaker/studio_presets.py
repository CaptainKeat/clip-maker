"""Reusable speaker styles; applying them never replaces tracked motion paths."""
import json
import uuid
from .db import database
from .motion_captions import normalize_motion

SCHEMA='CREATE TABLE IF NOT EXISTS studio_caption_presets(id TEXT PRIMARY KEY,name TEXT UNIQUE NOT NULL,speaker TEXT NOT NULL,motion_json TEXT NOT NULL)'


def list_presets(settings):
    with database(settings.db_path) as db:
        db.execute(SCHEMA)
        return [{**dict(row),'motion':json.loads(row['motion_json'])} for row in db.execute('SELECT * FROM studio_caption_presets ORDER BY name')]


def save(settings,body):
    name=str(body.get('name','')).strip()[:80];speaker=str(body.get('speaker','')).strip()[:60]
    motion=normalize_motion({**(body.get('motion') or {}),'enabled':True,'keyframes':[]},0,1)
    if not name:raise ValueError('Name this caption preset')
    with database(settings.db_path) as db:
        db.execute(SCHEMA)
        db.execute('INSERT INTO studio_caption_presets(id,name,speaker,motion_json) VALUES(?,?,?,?) ON CONFLICT(name) DO UPDATE SET speaker=excluded.speaker,motion_json=excluded.motion_json',
            (uuid.uuid4().hex,name,speaker,json.dumps(motion)))
    return next(p for p in list_presets(settings) if p["name"]==name)
