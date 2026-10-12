"""Track playback/export decisions shared by the manual editor and renderer."""
from copy import deepcopy


def state(recipe, key):
    return (recipe.get('editing', {}).get('tracks') or {}).get(key, {})


def audible(recipe, key):
    tracks = recipe.get('editing', {}).get('tracks') or {}
    active={'audio', *('sound:'+(s.get('lane') or 'Audio') for s in recipe.get('sounds', []))}
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
    result['overlays'] = [] if state(recipe, 'overlays').get('hidden') else result['overlays']
    result['sounds'] = [s for s in result['sounds'] if audible(recipe, 'sound:' + (s.get('lane') or 'Audio'))]
    return result
