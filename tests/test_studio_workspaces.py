import json,sqlite3,tempfile,unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from clipmaker import studio_workspaces as W

class Workspaces(unittest.TestCase):
    def state(self):
        layout={'tree':{'kind':'tabs','id':'root','panels':['program','timeline'],'active':'program'},'floats':[]}
        return {'selected':'Desk','saved':{'Desk':layout},'working':{'Desk':layout}}
    def test_shared_profile_port_restart_and_atomic_previous_copy(self):
        with tempfile.TemporaryDirectory() as directory:
            first=SimpleNamespace(data_dir=directory,port=1);second=SimpleNamespace(data_dir=directory,port=2)
            self.assertIsNone(W.read(first)['state']);W.save(first,self.state())
            self.assertEqual(W.read(second)['state']['selected'],'Desk')
            state=self.state();state['working']['Desk']['tree']['active']='timeline';W.save(second,state)
            with closing(sqlite3.connect(Path(directory)/'studio-workspaces.db')) as db:previous=db.execute('select previous_json from workspace_preferences').fetchone()[0]
            self.assertEqual(json.loads(previous)['working']['Desk']['tree']['active'],'program')
    def test_invalid_save_retains_previous_valid_file(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=SimpleNamespace(data_dir=directory);W.save(settings,self.state());before=W.read(settings)
            state=self.state();state['working']['Desk']['tree']['panels']=['program','program']
            with self.assertRaises(ValueError):W.save(settings,state)
            self.assertEqual(W.read(settings),before)
    def test_stale_window_cannot_overwrite_a_saved_workspace(self):
        with tempfile.TemporaryDirectory() as directory:
            settings=SimpleNamespace(data_dir=directory);W.save(settings,self.state(),0);before=W.read(settings)
            with self.assertRaisesRegex(ValueError,'another window'):W.save(settings,self.state(),0)
            self.assertEqual(W.read(settings),before)
    def test_unknown_panels_unsafe_names_and_missing_selection_rejected(self):
        for change in ('panel','name','selected'):
            state=self.state()
            if change=='panel':state['saved']['Desk']['tree']['panels']=['filesystem']
            elif change=='name':state['saved']['__proto__']=state['saved']['Desk']
            else:state['selected']='Missing'
            with self.assertRaises(ValueError):W.validate(state)

if __name__=='__main__':unittest.main()
