import io,json,hashlib,os,shutil,unittest,uuid,zipfile
from pathlib import Path
from unittest.mock import patch,Mock
from clipmaker import updater as U


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.root=Path(__file__).parent/('update-fixture-'+uuid.uuid4().hex);self.root.mkdir()
        self.install=self.root/'Clip Maker';self.install.mkdir()
        (self.install/'Clip Maker.exe').write_bytes(b'old');(self.install/'old.txt').write_text('keep')
        self.env=patch.dict(os.environ,{'CLIPMAKER_UPDATE_DATA':str(self.root/'receipts')});self.env.start()
    def tearDown(self):
        self.env.stop()
        for p in sorted(self.root.rglob('*'),key=lambda p:len(p.parts),reverse=True):
            if p.is_dir():p.rmdir()
            else:p.unlink()
        self.root.rmdir()
    def archive(self,names=None):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            for name,data in (names or {'Clip Maker/Clip Maker.exe':b'new','Clip Maker/_internal/core.dll':b'library'}).items():
                info=zipfile.ZipInfo(name);info.filename=name;z.writestr(info,data)
        return b.getvalue()
    def release(self,data):
        asset=U.ReleaseAsset('Clip-Maker-v1.0.0-Windows.zip','https://github.com/CaptainKeat/clip-maker/releases/download/v1.0.0/Clip-Maker-v1.0.0-Windows.zip',len(data),hashlib.sha256(data).hexdigest())
        return U.ReleaseInfo('1.0.0','v1.0.0','Release','Notes','','https://github.com/CaptainKeat/clip-maker/releases/tag/v1.0.0',asset)
    def test_staging_validated_package_keeps_current_install_and_progress(self):
        data=self.archive();seen=[]
        result=U.stage_update(self.release(data),self.install,lambda n,t:seen.append((n,t)),open_url=lambda *a,**kw:io.BytesIO(data))
        self.assertEqual((self.install/'Clip Maker.exe').read_bytes(),b'old')
        self.assertEqual((result.payload_root/'Clip Maker.exe').read_bytes(),b'new')
        self.assertEqual(result.payload_root.parent,self.install.parent)
        self.assertEqual(seen[-1],(len(data),len(data)))
    def test_digest_failure_keeps_partial_and_original(self):
        data=self.archive();bad=data[:-1]+b'x'
        with self.assertRaisesRegex(ValueError,'SHA-256'):
            U.stage_update(self.release(data),self.install,open_url=lambda *a,**kw:io.BytesIO(bad))
        self.assertEqual((self.install/'Clip Maker.exe').read_bytes(),b'old')
        self.assertTrue(list(self.root.rglob('*.part')))
    def test_archive_rejects_traversal_backslashes_and_drive_streams(self):
        for path in ['../escape','/absolute','Clip Maker/../outside','Clip Maker/file:stream']:
            with self.subTest(path=path),zipfile.ZipFile(io.BytesIO(self.archive({path:b'x'}))) as z:
                with self.assertRaises(ValueError):U._safe_archive_members(z,self.root/'extract')
        # Windows ZipInfo normalizes slashes while decoding; check the raw-member guard too.
        info=zipfile.ZipInfo('safe');info.filename='Clip Maker\\bad.exe'
        archive=Mock();archive.infolist.return_value=[info]
        with self.assertRaises(ValueError):U._safe_archive_members(archive,self.root/'extract')
    def test_symlink_is_rejected(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:
            i=zipfile.ZipInfo('Clip Maker/link');i.external_attr=0o120777<<16;z.writestr(i,b'outside')
        with zipfile.ZipFile(io.BytesIO(b.getvalue())) as z:
            with self.assertRaisesRegex(ValueError,'symbolic'):U._safe_archive_members(z,self.root/'extract')
    def test_release_requires_stable_official_digest_asset(self):
        data=self.archive();a=self.release(data).asset
        payload={'tag_name':'v1.0.0','assets':[{'name':a.name,'size':a.size,'digest':'sha256:'+a.sha256,'browser_download_url':a.url}]}
        fetch=lambda:U.fetch_latest_release(lambda *args,**kw:io.BytesIO(json.dumps(payload).encode()))
        self.assertEqual(fetch().version,'1.0.0')
        payload['prerelease']=True
        with self.assertRaisesRegex(ValueError,'non-stable'):fetch()
        payload['prerelease']=False;payload['assets'][0]['digest']=''
        with self.assertRaisesRegex(ValueError,'SHA-256'):fetch()
        payload['assets'][0]['digest']='sha256:'+a.sha256;payload['assets'][0]['browser_download_url']='https://example.com/file'
        with self.assertRaisesRegex(ValueError,'official'):fetch()
    def test_helper_early_exit_does_not_allow_parent_shutdown(self):
        process=Mock();process.poll.return_value=0
        with patch.object(U.subprocess,'Popen',return_value=process):
            with self.assertRaisesRegex(RuntimeError,'before starting'):U._start_update_helper(['powershell'],self.root/'ready',self.root/'helper.log')
    def test_receipt_read_does_not_create_profile(self):
        self.assertIsNone(U.read_update_receipt());self.assertFalse((self.root/'receipts').exists())
    def test_semantic_versions(self):
        self.assertTrue(U.is_newer_version('v1.0.10','1.0.9'))
        self.assertFalse(U.is_newer_version('1.0.0','1.0.0'))
        with self.assertRaises(ValueError):U.version_tuple('v1.0.0-beta')

if __name__=='__main__':unittest.main()
