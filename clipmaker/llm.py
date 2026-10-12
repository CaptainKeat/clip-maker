"""Explicit provider profiles shared by captions and highlight refinement."""
import ipaddress
import json
import re
import uuid
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, build_opener, HTTPRedirectHandler
from . import credentials

KINDS = {'lmstudio', 'ollama', 'openai', 'openrouter', 'anthropic', 'gemini', 'compatible'}


def legacy(settings):
    return {'id':'legacy', 'name':'Existing LM Studio', 'location':'local', 'kind':'lmstudio',
            'url':settings.lmstudio_api_url, 'model':settings.lmstudio_model, 'allow_http':False}


def profiles(settings):
    values = settings.llm_profiles or [legacy(settings)]
    return [{**p, 'has_key':credentials.has(settings, p['id'])} for p in values]


def selected(settings, profile_id=None):
    identity = profile_id or settings.llm_active_profile or 'legacy'
    result = next((p for p in settings.llm_profiles if p['id']==identity), None)
    if result is None and identity=='legacy': result = legacy(settings)
    if result is None: raise ValueError('Choose an existing connection profile')
    return validate(result)


def validate(body):
    kind = str(body.get('kind','compatible'));location = str(body.get('location','local'))
    if kind not in KINDS or location not in {'local','remote','api'}: raise ValueError('Choose a provider and location')
    name = str(body.get('name','')).strip();model = str(body.get('model','')).strip()
    if not name or len(name)>80 or len(model)>200 or any(c in model for c in '\r\n\0'): raise ValueError('Enter a connection name and valid model identifier')
    raw = str(body.get('url','')).strip().rstrip('/');parts = urlsplit(raw)
    if parts.scheme not in {'http','https'} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError('Use an HTTP(S) server URL without credentials, query or fragment')
    try: parts.port
    except ValueError: raise ValueError('Use a valid server port') from None
    try:
        loopback = parts.hostname.lower()=='localhost' or ipaddress.ip_address(parts.hostname).is_loopback
    except ValueError: loopback = parts.hostname.lower()=='localhost'
    allow_http = body.get('allow_http') is True
    if location=='local' and not loopback: raise ValueError('Local connections must use localhost. Choose Remote for another computer.')
    if location=='api' and parts.scheme!='https': raise ValueError('API providers require HTTPS')
    if location=='remote' and parts.scheme=='http' and not allow_http: raise ValueError('Enable HTTP for this remote connection, or use HTTPS')
    path = parts.path.rstrip('/')
    for suffix in ['/chat/completions','/messages','/models']:
        if path.endswith(suffix): path = path[:-len(suffix)]
    if not path: path = '/api/v1' if kind=='openrouter' else '/v1'
    identity = str(body.get('id') or uuid.uuid4().hex)
    if identity!='legacy' and not re.fullmatch('[a-f0-9]{32}',identity): raise ValueError('Invalid connection identity')
    return {'id':identity,'name':name,'location':location,'kind':kind,'url':urlunsplit((parts.scheme,parts.netloc,path,'','')),
            'model':model,'allow_http':allow_http}


def save(settings, body):
    profile = validate(body)
    values = [dict(p) for p in (settings.llm_profiles or [legacy(settings)])]
    previous = next((p for p in values if p['id']==profile['id']),None)
    # Keys belong to one endpoint; changing the destination clears the old key.
    if previous and previous['url']!=profile['url'] and credentials.has(settings,profile['id']):
        credentials.put(settings,profile['id'],'')
    if 'api_key' in body and body['api_key']: credentials.put(settings,profile['id'],body['api_key'])
    if body.get('clear_key'): credentials.put(settings,profile['id'],'')
    settings.llm_profiles = [p for p in values if p['id']!=profile['id']] + [profile]
    if body.get('activate') is True: settings.llm_active_profile = profile['id']
    return profile


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs): return None


def request(settings, profile, suffix, body=None, timeout=12):
    key = credentials.get(settings,profile['id'])
    headers = {'Content-Type':'application/json','User-Agent':'Clip-Maker'}
    if profile['kind']=='anthropic':
        headers['anthropic-version']='2023-06-01'
        if key: headers['x-api-key']=key
    elif key: headers['Authorization']='Bearer '+key
    req = Request(profile['url']+suffix, data=json.dumps(body).encode('utf-8') if body is not None else None, headers=headers)
    try:
        with build_opener(NoRedirect()).open(req,timeout=timeout) as response:
            raw = response.read(2*1024*1024+1)
        if len(raw)>2*1024*1024: raise RuntimeError('Provider response is too large')
        result = json.loads(raw)
        if not isinstance(result,dict): raise ValueError('Invalid response')
        return result
    except HTTPError as error:
        # Never echo provider error bodies: they can contain credentials or prompts.
        hint = 'Check the API key.' if error.code in {401,403} else 'Check the server URL and model.'
        raise RuntimeError(f'Provider returned HTTP {error.code}. {hint}') from None
    except (URLError,TimeoutError,OSError):
        raise RuntimeError('Could not reach this provider. Check its server, address and certificate.') from None
    except (ValueError,UnicodeError): raise RuntimeError('The provider returned invalid JSON') from None


def discover(settings, profile_id):
    profile = selected(settings,profile_id)
    payload = request(settings,profile,'/models')
    models = sorted({str(m['id']) for m in payload.get('data',[]) if isinstance(m,dict) and m.get('id')})
    return {'models':models,'reachable':True,'selected_listed':profile['model'] in models,
            'message':'Server reachable. Model listing does not prove the model is loaded or can generate.'}


def chat(settings,prompt,temperature=.8,max_tokens=180,timeout=300,profile_id=None):
    if not settings.ai_enabled: raise ValueError('Enable AI tools before generating text')
    profile = selected(settings,profile_id)
    if not profile['model']: raise ValueError('Choose a model in Settings')
    body = {'model':profile['model'],'messages':[{'role':'user','content':prompt}], 'max_tokens':max_tokens,'stream':False}
    if profile['kind']=='anthropic':
        payload = request(settings,profile,'/messages',body,timeout)
        result = ''.join(str(b.get('text','')) for b in payload.get('content',[]) if b.get('type')=='text')
    else:
        if profile['kind']=='lmstudio': body.update(temperature=temperature,reasoning_effort='none')
        elif profile['kind'] not in {'openai'}: body['temperature']=temperature
        if profile['kind']=='openai':
            body['max_completion_tokens']=body.pop('max_tokens')
            if re.match(r'^(gpt-5|o[1-9])',profile['model']):
                body.update(reasoning_effort='low',max_completion_tokens=max(1024,max_tokens))
        payload = request(settings,profile,'/chat/completions',body,timeout)
        try: result = payload['choices'][0]['message']['content']
        except (KeyError,IndexError,TypeError): raise RuntimeError('Provider returned no usable message') from None
        if isinstance(result,list): result=''.join(str(v.get('text','')) for v in result if isinstance(v,dict))
    if not isinstance(result,str) or not result.strip(): raise RuntimeError('Provider returned an empty message')
    return result.strip().strip('"')
