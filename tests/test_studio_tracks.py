import unittest
from clipmaker.studio_tracks import export_view, audible


class TrackPlaybackTests(unittest.TestCase):
    def recipe(self):
        return {'clips':[{'audio':[{'enabled':True}]}], 'captions':[{'text':'Hello'}],
                'overlays':[{'asset_id':2}], 'sounds':[{'lane':'Voice'}, {'lane':'Music'}],
                'editing':{'tracks':{}}}

    def test_solo_and_mute_apply_to_export_without_changing_saved_mix(self):
        recipe=self.recipe()
        recipe['editing']['tracks']={'sound:Voice':{'solo':True}, 'sound:Music':{'muted':True}}
        view=export_view(recipe)
        self.assertFalse(view['clips'][0]['audio'][0]['enabled'])
        self.assertEqual(view['sounds'],[{'lane':'Voice'}])
        self.assertTrue(recipe['clips'][0]['audio'][0]['enabled'])

    def test_visibility_is_output_state_and_lock_is_edit_only(self):
        recipe=self.recipe()
        recipe['editing']['tracks']={'captions':{'hidden':True},'overlays':{'hidden':True},'audio':{'locked':True}}
        view=export_view(recipe)
        self.assertEqual(view['captions'],[])
        self.assertEqual(view['overlays'],[])
        self.assertTrue(view['clips'][0]['audio'][0]['enabled'])

    def test_removed_solo_lane_cannot_silence_remaining_tracks(self):
        recipe=self.recipe();recipe['editing']['tracks']={'sound:Deleted':{'solo':True}}
        self.assertTrue(audible(recipe,'audio'))
        self.assertEqual(len(export_view(recipe)['sounds']),2)


if __name__ == '__main__':unittest.main()
