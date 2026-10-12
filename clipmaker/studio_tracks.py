"""Track playback/export decisions shared by the manual editor and renderer."""
from copy import deepcopy


def state(recipe, key):
    return (recipe.get('editing', {}).get('tracks') or {}).get(key, {})


def audible(recipe, key):
    tracks = recipe.get('editing', {}).get('tracks') or {}
    active={'audio', *('sound:'+(s.get('lane') or 'Audio') for s in recipe.get('sounds', []))}
    active.update('sound:'+t['id'] for t in recipe.get('editing',{}).get('lanes',[]) if t.get('kind')=='audio')
    solo = any(v.get('solo') for k, v in tracks.items() if k in active)
    current = state(recipe, key)
    return not current.get('muted') and (not solo or current.get('solo', False))


def export_view(recipe):
    result = deepcopy(recipe)
    if not audible(recipe, 'audio'):
        for clip in result['clips']:
            for stream in clip['audio']:
                stream['enabled'] = False
    result['captions'] = [] if state(recipe, 'captions').get('hidden') else result['captions']
    result['captions'] = [c for c in result['captions'] if not state(recipe,'caption:'+(c.get('lane') or '')).get('hidden')]
    result['overlays'] = [] if state(recipe, 'overlays').get('hidden') else result['overlays']
    result['overlays'] = [c for c in result['overlays'] if not state(recipe,'video:'+(c.get('lane') or 'Overlays')).get('hidden')]
    saved=[t['id'] for t in recipe.get('editing',{}).get('lanes',[]) if t.get('kind')=='video']
    legacy=list(dict.fromkeys(c.get('lane') or 'Overlays' for c in recipe.get('overlays',[]) if (c.get('lane') or 'Overlays') not in saved))
    order=legacy+saved
    result['overlays'].sort(key=lambda c:order.index(c.get('lane') or 'Overlays'))
    result['sounds'] = [s for s in result['sounds'] if not s.get('disabled') and audible(recipe, 'sound:' + (s.get('lane') or 'Audio'))]
    result['captions']=[c for c in result['captions'] if not c.get('disabled')]
    result['overlays']=[c for c in result['overlays'] if not c.get('disabled')]
    for clip in result['clips']:
        if clip.get('disabled'):
            for stream in clip['audio']:stream['enabled']=False
    return result
