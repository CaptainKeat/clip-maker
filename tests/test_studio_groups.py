import unittest
from clipmaker.studio_groups import normalize_groups
from clipmaker.studio_tracks import export_view

class MediaGroups(unittest.TestCase):
    def recipe(self):
        return {'clips':[{'id':'video','audio':[{'ordinal':0,'enabled':True}]}],'captions':[{'id':'caption'}],'overlays':[],'sounds':[{'id':'sound','lane':'Voice'}],'editing':{'tracks':{},'lanes':[]}}
    def test_declared_refs_and_inherited_fragment_membership(self):
        r=self.recipe();r['sounds'].append({'id':'fragment','group_id':'g'})
        groups=normalize_groups([{'id':'g','name':'Intro','members':[{'type':'clips','id':'video'},{'type':'sounds','id':'sound'},{'type':'sounds','id':'missing'}]}],r)
        self.assertEqual(groups[0]['name'],'Intro');self.assertEqual(len(groups[0]['members']),3);self.assertEqual(r['sounds'][0]['group_id'],'g')
    def test_singletons_and_malformed_groups_are_removed(self):
        r=self.recipe();r['clips'][0]['group_id']='orphan';self.assertEqual(normalize_groups([None,{'id':'x','members':'bad'}],r),[]);self.assertNotIn('group_id',r['clips'][0])
    def test_disabled_media_preserves_main_duration_and_original_mix(self):
        r=self.recipe()
        for kind in ('clips','captions','sounds'):r[kind][0]['disabled']=True
        out=export_view(r)
        self.assertEqual(len(out['clips']),1);self.assertFalse(out['clips'][0]['audio'][0]['enabled']);self.assertEqual(out['sounds'],[]);self.assertEqual(out['captions'],[]);self.assertTrue(r['clips'][0]['audio'][0]['enabled'])

if __name__=='__main__':unittest.main()
