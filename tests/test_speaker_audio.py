import unittest
from clipmaker.speaker_audio import partition


class SpeakerRoutingTests(unittest.TestCase):
    def test_overlap_and_gaps_are_exclusive_and_cover_the_source(self):
        regions=partition([{'start':1,'end':4,'speaker':'Speaker 1'},
                           {'start':3,'end':5,'speaker':'Speaker 2'}],6)
        self.assertEqual([(r['start'],r['end'],r['speaker']) for r in regions],
            [(0,1,'Background / unassigned'),(1,3,'Speaker 1'),(3,4,'Overlapping voices'),
             (4,5,'Speaker 2'),(5,6,'Background / unassigned')])

    def test_repeated_same_speaker_is_not_a_false_overlap(self):
        regions=partition([{'start':0,'end':3,'speaker':'Speaker 1'},
                           {'start':2,'end':5,'speaker':'Speaker 1'}],5)
        self.assertEqual(regions,[{'start':0,'end':5.0,'speaker':'Speaker 1'}])


if __name__=='__main__':unittest.main()
