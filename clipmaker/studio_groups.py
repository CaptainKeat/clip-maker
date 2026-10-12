"""Bounded, stable media-group membership; independent of audio/video links."""
def normalize_groups(raw, recipe):
    types=('clips','captions','overlays','sounds')
    entries={(kind,str(c['id'])):c for kind in types for c in recipe[kind]}
    names={};fallback={}
    for group in (raw if isinstance(raw,list) else [])[:100]:
        if not isinstance(group,dict):continue
        identity=str(group.get('id') or '')[:80]
        if not identity:continue
        names[identity]=str(group.get('name') or 'Group').strip()[:80] or 'Group'
        for ref in (group.get('members') if isinstance(group.get('members'),list) else [])[:800]:
            if isinstance(ref,dict) and (ref.get('type'),str(ref.get('id') or '')) in entries:fallback[(ref['type'],str(ref['id']))]=identity
    groups={}
    for ref,item in entries.items():
        identity=str(item.get('group_id') or fallback.get(ref) or '')[:80]
        if identity and (identity in groups or len(groups)<100):groups.setdefault(identity,[]).append({'type':ref[0],'id':ref[1]});item['group_id']=identity
    valid={identity for identity,refs in groups.items() if len(refs)>1}
    for item in entries.values():
        if item.get('group_id') not in valid:item.pop('group_id',None)
    return [{'id':identity,'name':names.get(identity,'Group'),'members':refs} for identity,refs in groups.items() if identity in valid]
