import json
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch
from unittest.mock import Mock,MagicMock
from clipmaker import studio
from clipmaker.settings import Settings


class StudioTests(unittest.TestCase):
    def setUp(self):
        self.folder = Path(__file__).parent/("studio-fixture-"+uuid.uuid4().hex)
        self.folder.mkdir()
        self.settings = Settings(data_dir=str(self.folder),output_dir=str(self.folder))
        self.media = self.folder/"long-recording.mp4"
        self.media.write_bytes(b"fixture")
        self.meta = {"duration_sec":10800,"width":1920,"height":1080,"audio_streams":[{"ordinal":0},{"ordinal":1}],"chapters":[]}
        with patch("clipmaker.studio.probe_video",return_value=self.meta):
            self.asset = studio.import_media(self.settings,self.media)

    def tearDown(self):
        for p in self.folder.iterdir(): p.unlink()
        self.folder.rmdir()

    def test_full_vod_can_open_and_save_without_inference(self):
        with patch("clipmaker.lmstudio.lmstudio_chat",side_effect=AssertionError("AI must not run")):
            p = studio.create_project(self.settings,{"asset_id":self.asset["id"],"name":"Manual VOD"})
            self.assertEqual(p["recipe"]["clips"][0]["end"],10800)
            p["recipe"]["clips"][0]["audio"][1]["enabled"] = True
            saved = studio.save_project(self.settings,p["id"],p)
            self.assertEqual(saved["revision"],2)
            self.assertTrue(saved["recipe"]["clips"][0]["audio"][1]["enabled"])
            self.assertEqual(self.media.read_bytes(),b"fixture")

    def test_stale_autosave_cannot_replace_a_newer_project(self):
        p = studio.create_project(self.settings,{"asset_id":self.asset["id"]})
        newer = studio.save_project(self.settings,p["id"],{**p,"name":"New name"})
        with self.assertRaisesRegex(ValueError,"another window"):
            studio.save_project(self.settings,p["id"],{**p,"name":"Stale name"})
        self.assertEqual(studio.project(self.settings,p["id"])["name"],"New name")

    def test_reorder_speed_and_caption_path_roundtrip(self):
        p = studio.create_project(self.settings,{"asset_id":self.asset["id"]})
        c = p["recipe"]["clips"][0]
        p["recipe"]["clips"] = [{**c,"start":100,"end":110,"speed":2},{**c,"id":"second","start":10,"end":15}]
        p["recipe"]["aspect"] = "9:16"
        p["recipe"]["captions"] = [{"text":"Hello","start":1,"end":2,"motion":{"enabled":True,"effect":"shake","keyframes":[{"time":1,"x":25,"y":40},{"time":2,"x":50,"y":40}]}}]
        result = studio.save_project(self.settings,p["id"],p)
        self.assertEqual(sum(studio.clip_duration(c) for c in result["recipe"]["clips"]),10)
        self.assertEqual(result["frame"],[1080,1920])
        self.assertEqual(result["recipe"]["captions"][0]["motion"]["keyframes"][1]["x"],50)

    def test_unknown_media_and_nonfinite_numbers_are_rejected(self):
        p = studio.create_project(self.settings,{"asset_id":self.asset["id"]})
        p["recipe"]["clips"][0]["asset_id"] = 999
        with self.assertRaisesRegex(ValueError,"unavailable"):
            studio.save_project(self.settings,p["id"],p)
        with self.assertRaisesRegex(ValueError,"finite"):
            studio.number(float("nan"))

    def test_empty_tracks_and_linked_clip_lanes_roundtrip(self):
        p=studio.create_project(self.settings,{'asset_id':self.asset['id']})
        p['recipe']['editing']['lanes']=[{'id':'v2','name':'Camera','kind':'video'},{'id':'a2','name':'Voice','kind':'audio'},{'id':'empty','name':'Titles','kind':'caption'}]
        p['recipe']['overlays']=[{'asset_id':self.asset['id'],'time':2,'duration':3,'lane':'v2','link_id':'pair'}]
        p['recipe']['sounds']=[{'asset_id':self.asset['id'],'time':2,'duration':3,'lane':'a2','link_id':'pair'}]
        saved=studio.save_project(self.settings,p['id'],p)['recipe']
        self.assertEqual(saved['editing']['lanes'],p['recipe']['editing']['lanes'])
        self.assertEqual(saved['overlays'][0]['link_id'],saved['sounds'][0]['link_id'])
        self.assertEqual(saved['overlays'][0]['lane'],'v2')

    def test_export_progress_tolerates_unavailable_timestamp_and_reaps_process(self):
        process=Mock();process.stdout=MagicMock();process.stdout.__iter__.return_value=iter(['out_time_us=N/A\n','out_time_us=1000000\n'])
        process.returncode=0;process.poll.return_value=0
        values=[]
        with patch('clipmaker.studio.subprocess.Popen',return_value=process):
            studio._run(self.settings,'progress-test',[],self.folder/'export.log',2,values.append)
        self.assertEqual(values,['Project export 50%']);process.wait.assert_called_once()
        self.assertNotIn('progress-test',studio.PROCESSES)

if __name__ == "__main__": unittest.main()
