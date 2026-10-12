import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from clipmaker import llm, credentials, whisper_setup, studio_transcribe
from clipmaker.settings import Settings


class Response:
    def __init__(self,value):self.value=value
    def __enter__(self):return self
    def __exit__(self,*args):pass
    def read(self,*args):return json.dumps(self.value).encode()


class Connections(unittest.TestCase):
    def profile(self,**changes):return {'id':'a'*32,'name':'Test connection','kind':'compatible','location':'local','url':'http://127.0.0.1:1234','model':'test-model',**changes}
    def test_url_rules_and_normalization(self):
        self.assertEqual(llm.validate(self.profile())['url'],'http://127.0.0.1:1234/v1')
        for value in ['http://user:password@localhost:1234','http://localhost:bad','http://localhost:1234/?key=secret','http://192.168.1.50:1234']:
            with self.subTest(value=value),self.assertRaises(ValueError):llm.validate(self.profile(url=value))
        with self.assertRaises(ValueError):llm.validate(self.profile(location='api',url='http://example.org'))
        with self.assertRaises(ValueError):llm.validate(self.profile(location='remote',url='http://192.168.1.50:1234'))
        self.assertEqual(llm.validate(self.profile(location='remote',allow_http=True,url='http://192.168.1.50:1234'))['location'],'remote')
    def test_legacy_selection_and_no_automatic_fallback(self):
        settings=Settings();self.assertEqual(llm.selected(settings)['model'],settings.lmstudio_model)
        settings.llm_active_profile='a'*32
        with self.assertRaises(ValueError):llm.selected(settings)
    @patch('clipmaker.llm.credentials.get',return_value='test-only-credential')
    @patch('clipmaker.llm.build_opener')
    def test_authenticated_chat_routes_and_model_scoping(self,opener,key):
        p=llm.validate(self.profile());settings=Settings(ai_enabled=True,llm_profiles=[p],llm_active_profile=p['id'])
        opener.return_value.open.return_value=Response({'choices':[{'message':{'content':'Connected'}}]})
        self.assertEqual(llm.chat(settings,'test'),'Connected');req=opener.return_value.open.call_args.args[0]
        self.assertEqual(req.full_url,p['url']+'/chat/completions');self.assertEqual(req.get_header('Authorization'),'Bearer test-only-credential')
        self.assertEqual(json.loads(req.data)['model'],'test-model')
        settings.llm_profiles[0]['kind']='anthropic';opener.return_value.open.return_value=Response({'content':[{'type':'text','text':'Hello'}]})
        self.assertEqual(llm.chat(settings,'test'),'Hello');req=opener.return_value.open.call_args.args[0]
        self.assertTrue(req.full_url.endswith('/messages'));self.assertEqual(req.get_header('X-api-key'),'test-only-credential')
        self.assertEqual(req.get_header('Anthropic-version'),'2023-06-01')
    @patch('clipmaker.llm.credentials.get',return_value='')
    @patch('clipmaker.llm.build_opener')
    def test_discovery_does_not_generate_and_errors_are_sanitized(self,opener,key):
        p=llm.validate(self.profile());settings=Settings(llm_profiles=[p])
        opener.return_value.open.return_value=Response({'data':[{'id':'other-model'}]})
        result=llm.discover(settings,p['id']);self.assertFalse(result['selected_listed']);self.assertTrue(result['reachable'])
        self.assertIsNone(opener.return_value.open.call_args.args[0].data)
        opener.return_value.open.side_effect=HTTPError(p['url'],401,'secret error body',{},None)
        with self.assertRaisesRegex(RuntimeError,'HTTP 401') as error:llm.discover(settings,p['id'])
        self.assertNotIn('secret',str(error.exception))
        with self.assertRaises(ValueError):llm.chat(settings,'Should never be sent')
        self.assertIsNone(llm.NoRedirect().redirect_request(None,None,None,None,None,None))
    @unittest.skipUnless(os.name=='nt','DPAPI is Windows-only')
    def test_windows_credential_roundtrip_and_no_plaintext_in_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=Settings(data_dir=directory)
            profile=llm.save(settings,self.profile(api_key='development-test-only-credential'))
            self.assertTrue(credentials.has(settings,profile['id']));self.assertEqual(credentials.get(settings,profile['id']),'development-test-only-credential')
            self.assertNotIn('development-test-only-credential',(Path(directory)/'provider-credentials.json').read_text())
            self.assertNotIn('api_key',json.dumps(settings.llm_profiles));self.assertTrue(llm.profiles(settings)[-1]['has_key'])
            llm.save(settings,self.profile(url='http://127.0.0.1:9876/v1'));self.assertFalse(credentials.has(settings,profile['id']))
    def test_whisper_controls_and_cue_speed(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cli=root/'whisper-cli.exe';cli.write_bytes(b'test');model=root/'ggml-small.bin';model.write_bytes(b'test')
            settings=Settings(whisper_cli=str(cli),whisper_model=str(model),language='ja',whisper_device='cpu',whisper_quality='accurate',whisper_threads=6,whisper_prompt='Test name',whisper_translate=True)
            args=whisper_setup.command(settings,root/'audio.wav',root/'transcript')
            self.assertIn('-ng',args);self.assertIn('-tr',args);self.assertEqual(args[args.index('-bs')+1],'8');self.assertEqual(args[args.index('-t')+1],'6')
            self.assertIn('Test name',args)
            with self.assertRaises(ValueError):whisper_setup.validate({'whisper_threads':0})
            settings.whisper_model=str(root/'ggml-small.en.bin');Path(settings.whisper_model).write_bytes(b'test')
            self.assertFalse(whisper_setup.status(settings)['ready'])
        words=[{'start':i,'end':i+.5,'text':str(i)} for i in range(8)]
        cues=studio_transcribe.cue_groups(words,2);self.assertEqual(cues[0]['start'],0);self.assertLessEqual(cues[-1]['end'],4)
    def test_transcription_reservation_and_cancel(self):
        studio_transcribe.reserve('test-project','b'*32)
        try:
            with self.assertRaises(ValueError):studio_transcribe.reserve('test-project','c'*32)
            studio_transcribe.cancel('b'*32);self.assertTrue(studio_transcribe.RUNS['b'*32]['cancelled'])
        finally:
            studio_transcribe.ACTIVE.pop('test-project',None);studio_transcribe.RUNS.pop('b'*32,None)

if __name__=='__main__':unittest.main()
