import io,shutil,unittest,uuid
from pathlib import Path
from unittest.mock import patch
from clipmaker.settings import Settings
from clipmaker import studio_upload


class UploadTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).parent/('upload-fixture-'+uuid.uuid4().hex);self.root.mkdir()
        self.settings=Settings(data_dir=str(self.root),output_dir=str(self.root))
        self.meta={'duration_sec':10,'width':1920,'height':1080,'audio_streams':[{'ordinal':0}],'chapters':[]}
    def tearDown(self):
        for p in sorted(self.root.rglob('*'),key=lambda p:len(p.parts),reverse=True):
            if p.is_dir():p.rmdir()
            else:p.unlink()
        self.root.rmdir()
    def test_browser_copy_retains_display_name_and_streams_bounded_chunks(self):
        data=b'synthetic media'*(1024*180)
        class Input(io.BytesIO):
            def read(self,n=-1):
                self.assert_size(n);return super().read(n)
            def assert_size(self,n):
                if n<0 or n>1024*1024:raise AssertionError('Whole media buffered in memory')
        with patch('clipmaker.studio.probe_video',return_value=self.meta):
            a=studio_upload.receive(self.settings,Input(data),'a video with spaces.mp4',len(data))
        self.assertEqual(a['name'],'a video with spaces.mp4')
        self.assertEqual(Path(a['path']).read_bytes(),data)
        self.assertEqual(Path(a['path']).parent,self.root/'studio-imports')
    def test_paths_and_unsupported_types_cannot_create_files(self):
        for name in ['../escape.mp4',r'..\escape.mp4',r'C:\private.mp4','secret.env','run.exe']:
            with self.subTest(name=name),self.assertRaises(ValueError):studio_upload.receive(self.settings,io.BytesIO(b'x'),name,1)
        self.assertFalse((self.root/'studio-imports').exists())
    def test_interrupted_copy_preserves_partial_without_registering_asset(self):
        with self.assertRaisesRegex(ValueError,'whole file'):
            studio_upload.receive(self.settings,io.BytesIO(b'partial'),'video.mp4',20)
        self.assertEqual(next((self.root/'studio-imports').glob('*.part')).read_bytes(),b'partial')
        self.assertFalse((self.root/'clipmaker.db').exists())
    def test_same_filename_imports_do_not_overwrite_another_copy(self):
        with patch('clipmaker.studio.probe_video',return_value=self.meta):
            a=studio_upload.receive(self.settings,io.BytesIO(b'one'),'video.mp4',3)
            b=studio_upload.receive(self.settings,io.BytesIO(b'two'),'video.mp4',3)
        self.assertNotEqual(a['path'],b['path']);self.assertEqual(Path(a['path']).read_bytes(),b'one')
    def test_insufficient_space_rejects_before_writing(self):
        with patch('clipmaker.studio_upload.shutil.disk_usage',return_value=shutil._ntuple_diskusage(100,100,0)):
            with self.assertRaisesRegex(ValueError,'free space'):studio_upload.receive(self.settings,io.BytesIO(b'x'),'video.mp4',1)
        self.assertFalse(list((self.root/'studio-imports').iterdir()))

if __name__=='__main__':unittest.main()
