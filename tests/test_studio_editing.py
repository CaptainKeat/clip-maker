import tempfile,unittest,json
from pathlib import Path
from unittest.mock import patch
from clipmaker import studio,studio_presets,studio_waveforms
from clipmaker.settings import Settings
from clipmaker.motion_captions import normalize_motion
class EditingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(dir=Path(__file__).parent);self.root=Path(self.tmp.name);self.settings=Settings(data_dir=str(self.root),output_dir=str(self.root));self.source=self.root/'video.mp4';self.source.write_bytes(b'test')
  with patch('clipmaker.studio.probe_video',return_value={'duration_sec':8,'width':320,'height':180,'audio_streams':[{'ordinal':0},{'ordinal':1}],'chapters':[]}):self.asset=studio.import_media(self.settings,self.source)
 def tearDown(self):self.tmp.cleanup()
 def test_audio_controls_roundtrip_and_bounds(self):
  p=studio.create_project(self.settings,{'asset_id':self.asset['id']});p['recipe']['clips'][0]['audio'][1]['dialogue']=True;p['recipe']['clips'][0]['audio_fade_in']=9
  p['recipe']['sounds']=[{'asset_id':self.asset['id'],'time':0,'source_start':6,'duration':8,'audio_ordinal':1,'duck':True,'duck_ratio':99,'fade_out':9}]
  r=studio.save_project(self.settings,p['id'],p)['recipe'];self.assertTrue(r['clips'][0]['audio'][1]['dialogue']);self.assertEqual(r['clips'][0]['audio_fade_in'],4);sound=r['sounds'][0];self.assertEqual(sound['duration'],2);self.assertEqual(sound['fade_out'],1);self.assertEqual(sound['duck_ratio'],20);self.assertEqual(sound['audio_ordinal'],1)
  p['recipe']['sounds'][0]['audio_ordinal']=5
  with self.assertRaises(ValueError):studio.save_project(self.settings,p['id'],p)
 def test_preset_upsert_validates_font_and_strips_paths(self):
  a=studio_presets.save(self.settings,{'name':'Speaker','speaker':'A','motion':{'font':'Consolas','bold':False,'keyframes':[{'time':0,'x':1,'y':1}]}});b=studio_presets.save(self.settings,{'name':'Speaker','speaker':'B','motion':{'font':'InvalidFont'}})
  self.assertEqual(a['id'],b['id']);self.assertEqual(a['motion']['font'],'Consolas');self.assertFalse(a['motion']['bold']);self.assertEqual(a['motion']['keyframes'],[]);self.assertEqual(b['motion']['font'],'Arial');self.assertEqual(len(studio_presets.list_presets(self.settings)),1)
 def test_waveform_cache_invalidates_on_source_change_and_checks_ordinal(self):
  _,path=studio_waveforms._cache(self.settings,self.asset['id'],1);path.write_text(json.dumps({'peaks':[1,2],'bucket':.05}),encoding='utf-8');self.assertTrue(studio_waveforms.read(self.settings,self.asset['id'],1)['ready']);self.source.write_bytes(b'changed');self.assertFalse(studio_waveforms.read(self.settings,self.asset['id'],1)['ready'])
  with self.assertRaises(ValueError):studio_waveforms.read(self.settings,self.asset['id'],3)
 def test_cancelled_waveform_does_not_start_decoder(self):
  identity,new=studio_waveforms.reserve(self.asset['id'],0);studio_waveforms.cancel(identity)
  with patch('clipmaker.studio_waveforms.subprocess.Popen') as process:
   with self.assertRaisesRegex(RuntimeError,'cancelled'):studio_waveforms.build(self.settings,self.asset['id'],0,identity)
   process.assert_not_called()
  self.assertNotIn((self.asset['id'],0),studio_waveforms.ACTIVE)
