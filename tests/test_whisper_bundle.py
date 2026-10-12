import hashlib,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from clipmaker import whisper_setup as W
from clipmaker.settings import Settings

class BundledWhisper(unittest.TestCase):
    def fixture(self,root):
        files={}
        for name,data in [('bin/whisper-cli.exe',b'engine'),('models/ggml-base-q5_1.bin',b'model')]:
            path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);files[name]=hashlib.sha256(data).hexdigest()
        (root/'manifest.json').write_text(json.dumps({'version':'test','files':files}))
    def test_missing_desktop_paths_use_complete_offline_bundle(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(W,'bundle_root',return_value=Path(directory)):
            root=Path(directory);self.fixture(root);s=Settings(whisper_cli='missing-engine',whisper_model='missing-model')
            state=W.status(s);self.assertTrue(state['ready']);self.assertEqual(state['source'],'bundled')
            command=W.command(s,root/'speech.wav',root/'out');self.assertIn('-ng',command);self.assertEqual(command[command.index('-m')+1],str(root/'models/ggml-base-q5_1.bin'))
            self.assertEqual(s.whisper_cli,'missing-engine')
    def test_valid_desktop_setup_stays_selected_unless_builtin_is_chosen(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(W,'bundle_root',return_value=Path(directory)):
            root=Path(directory);self.fixture(root);cli=root/'custom.exe';model=root/'custom.bin';cli.write_bytes(b'custom');model.write_bytes(b'custom')
            s=Settings(whisper_cli=str(cli),whisper_model=str(model));self.assertEqual(W.status(s)['source'],'custom')
            s.whisper_cli=s.whisper_model='bundled';self.assertEqual(W.status(s)['source'],'bundled')
    def test_corrupted_model_is_rejected_and_does_not_start_a_process(self):
        with tempfile.TemporaryDirectory() as directory,patch.object(W,'bundle_root',return_value=Path(directory)):
            root=Path(directory);self.fixture(root);s=Settings(whisper_cli='bundled',whisper_model='bundled');self.assertTrue(W.status(s)['ready'])
            (root/'models/ggml-base-q5_1.bin').write_bytes(b'corrupt');self.assertFalse(W.status(s)['ready'])
            with self.assertRaises(ValueError):W.command(s,root/'audio.wav',root/'out')
    def test_quantized_english_model_rejects_other_languages(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);cli=root/'custom.exe';model=root/'ggml-base.en-q5_1.bin';cli.write_bytes(b'custom');model.write_bytes(b'custom')
            s=Settings(whisper_cli=str(cli),whisper_model=str(model),language='ja');self.assertFalse(W.status(s)['ready'])

if __name__=='__main__':unittest.main()
