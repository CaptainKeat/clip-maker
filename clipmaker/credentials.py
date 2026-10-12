"""Provider credentials encrypted for the current Windows account with DPAPI."""
import ctypes
import json
import os
import threading
from pathlib import Path

LOCK = threading.Lock()


def _crypt(value, decrypt=False):
    if os.name != 'nt':
        raise RuntimeError('Saving API keys requires the Windows installed app')
    from ctypes import wintypes
    class Blob(ctypes.Structure):
        _fields_ = [('size', wintypes.DWORD), ('data', ctypes.POINTER(ctypes.c_ubyte))]
    buffer = ctypes.create_string_buffer(value)
    source = Blob(len(value), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    result = Blob()
    library = ctypes.WinDLL('crypt32', use_last_error=True)
    function = library.CryptUnprotectData if decrypt else library.CryptProtectData
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(result)):
        raise RuntimeError('Windows could not unlock this API key. Enter it again in Settings.')
    try:
        return ctypes.string_at(result.data, result.size)
    finally:
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.LocalFree.argtypes = [ctypes.c_void_p]
        kernel.LocalFree(ctypes.cast(result.data, ctypes.c_void_p))


def _path(settings):
    return Path(settings.data_dir) / 'provider-credentials.json'


def _read(settings):
    path = _path(settings)
    if not path.exists(): return {}
    return json.loads(path.read_text(encoding='utf-8'))


def has(settings, profile_id):
    with LOCK: return bool(_read(settings).get(profile_id))


def get(settings, profile_id):
    with LOCK: value = _read(settings).get(profile_id)
    return _crypt(bytes.fromhex(value), True).decode('utf-8') if value else ''


def put(settings, profile_id, value):
    if not isinstance(value, str) or len(value) > 4096 or any(c in value for c in '\r\n\0'):
        raise ValueError('Enter a valid API key')
    if value and not value.strip(): raise ValueError('Enter a valid API key')
    with LOCK:
        values = _read(settings)
        if value: values[profile_id] = _crypt(value.strip().encode('utf-8')).hex()
        else: values.pop(profile_id, None)
        path = _path(settings);path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix('.tmp')
        temporary.write_text(json.dumps(values), encoding='utf-8');temporary.replace(path)
