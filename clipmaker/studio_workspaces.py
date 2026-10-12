"""Device-local workspace preferences shared across desktop server ports."""
import json
import math
import sqlite3
import threading
from contextlib import closing
from pathlib import Path

PANELS={'sources','source','program','controls','effects','bookmarks','timeline'}
LOCK=threading.Lock()

def validate(raw):
    if not isinstance(raw,dict) or len(json.dumps(raw).encode('utf-8'))>262144:raise ValueError('Workspace preferences are too large or invalid')
    result={'selected':str(raw.get('selected') or 'editing'),'saved':{},'working':{}}
    def layout(value):
        if not isinstance(value,dict):raise ValueError('Invalid workspace layout')
        seen=set();count=0
        def number(value,default,low,high):
            try:n=float(value)
            except (ValueError,TypeError):n=default
            if not math.isfinite(n):raise ValueError('Workspace dimensions must be finite')
            return max(low,min(high,n))
        def node(value,depth=0):
            nonlocal count
            if value is None:return None
            count+=1
            if not isinstance(value,dict) or depth>12 or count>40:raise ValueError('Invalid workspace tree')
            identity=str(value.get('id') or '')[:80]
            if value.get('kind')=='split':
                a=node(value.get('a'),depth+1);b=node(value.get('b'),depth+1)
                if not a or not b:raise ValueError('Both sides of a panel split must exist')
                return {'kind':'split','id':identity,'axis':'y' if value.get('axis')=='y' else 'x','ratio':number(value.get('ratio'),.5,.08,.92),'a':a,'b':b}
            values=value.get('panels')
            if value.get('kind')!='tabs' or not isinstance(values,list) or not values:raise ValueError('Invalid panel tabs')
            for panel in values:
                if not isinstance(panel,str) or panel not in PANELS or panel in seen:raise ValueError('Panels must be known and appear only once')
                seen.add(panel)
            active=value.get('active');return {'kind':'tabs','id':identity,'panels':values,'active':active if active in values else values[0]}
        tree=node(value.get('tree'));floats=value.get('floats',[])
        if not isinstance(floats,list) or len(floats)>7:raise ValueError('Invalid floating panels')
        windows=[]
        for f in floats:
            if not isinstance(f,dict):raise ValueError('Invalid floating panel')
            n=node(f.get('node'))
            if not n:raise ValueError('A floating window needs a panel')
            windows.append({'id':str(f.get('id') or '')[:80],'node':n,'x':number(f.get('x'),30,0,4000),'y':number(f.get('y'),30,0,2400),'w':number(f.get('w'),360,180,2400),'h':number(f.get('h'),300,140,1800)})
        if not tree and not windows:raise ValueError('Keep one panel open')
        return {'tree':tree,'floats':windows,'closed':sorted(PANELS-seen),'maximized':None}
    for field in ('saved','working'):
        values=raw.get(field)
        if not isinstance(values,dict) or len(values)>20:raise ValueError('Keep up to 20 workspaces')
        for name,value in values.items():
            if not isinstance(name,str) or not name.strip() or len(name)>60 or name in {'__proto__','constructor','prototype'}:raise ValueError('Invalid workspace name')
            result[field][name]=layout(value)
    if set(result['saved'])!=set(result['working']):raise ValueError('Each workspace needs a saved and a working layout')
    if result['selected'] not in result['saved']:raise ValueError('Selected workspace is unavailable')
    return result

def read(settings):
    path=Path(settings.data_dir)/'studio-workspaces.db'
    if not path.exists():return {'state':None,'revision':0}
    with closing(sqlite3.connect(path,timeout=15)) as db:
        try:row=db.execute('SELECT revision,state_json FROM workspace_preferences WHERE id=1').fetchone()
        except sqlite3.OperationalError as error:
            if 'no such table' in str(error):return {'state':None,'revision':0}
            raise
    return {'state':validate(json.loads(row[1])),'revision':row[0]} if row else {'state':None,'revision':0}

def save(settings,body,revision=None):
    state=validate(body);path=Path(settings.data_dir)/'studio-workspaces.db';path.parent.mkdir(parents=True,exist_ok=True)
    with LOCK:
        with closing(sqlite3.connect(path,timeout=15)) as db, db:
            db.execute('CREATE TABLE IF NOT EXISTS workspace_preferences(id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,state_json TEXT NOT NULL,previous_json TEXT)')
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT revision,state_json FROM workspace_preferences WHERE id=1').fetchone();current=row[0] if row else 0
            if revision is not None and (type(revision) is not int or revision!=current):raise ValueError('Workspace changed in another window; reload and merge before saving')
            next_revision=current+1
            db.execute('INSERT INTO workspace_preferences(id,revision,state_json,previous_json) VALUES(1,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,state_json=excluded.state_json,previous_json=excluded.previous_json',(next_revision,json.dumps(state,ensure_ascii=False),row[1] if row else None))
    return {'saved':True,'revision':next_revision}
