import os,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
try:
    from PySide6.QtCore import QMimeData,QUrl,QPoint,QPointF,Qt,QEvent
    from PySide6.QtGui import QDragEnterEvent,QDragMoveEvent,QDropEvent,QDragLeaveEvent
    from PySide6.QtWidgets import QApplication,QWidget
    from clipmaker.desktop_drop import DesktopDropFilter,local_paths
    QT=True
except ImportError:QT=False


@unittest.skipUnless(QT,'Desktop Qt runtime not installed')
class DesktopDropTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QApplication.instance() or QApplication([])
    def setUp(self):
        self.view=QWidget();self.view.setAcceptDrops(True)
        self.child=QWidget(self.view);self.child.move(20,30);self.child.setAcceptDrops(True)
        self.other=QWidget();self.calls=[]
        self.router=DesktopDropFilter(self.view,lambda kind,data:self.calls.append((kind,data)))
        self.app.installEventFilter(self.router)
        self.mime=QMimeData();self.mime.setUrls([QUrl.fromLocalFile('/video with spaces.mp4'),QUrl.fromLocalFile('/audio.wav')])
    def tearDown(self):
        self.app.removeEventFilter(self.router);self.view.deleteLater();self.other.deleteLater();self.app.processEvents()
    def test_explorer_sequence_on_render_child_keeps_originals_and_maps_coordinates(self):
        drag=QDragEnterEvent(QPoint(10,15),Qt.CopyAction|Qt.MoveAction,self.mime,Qt.LeftButton,Qt.NoModifier)
        move=QDragMoveEvent(QPoint(10,15),Qt.CopyAction|Qt.MoveAction,self.mime,Qt.LeftButton,Qt.NoModifier)
        drop=QDropEvent(QPointF(10,15),Qt.CopyAction|Qt.MoveAction,self.mime,Qt.LeftButton,Qt.NoModifier)
        for event in [drag,move,drop]:
            self.app.sendEvent(self.child,event)
            self.assertTrue(event.isAccepted());self.assertEqual(event.dropAction(),Qt.CopyAction)
        kind,data=self.calls[-1]
        self.assertEqual(kind,'drop');self.assertEqual((data['x'],data['y']),(30,45))
        self.assertEqual(len(data['paths']),2)
    def test_other_windows_and_remote_urls_are_not_intercepted(self):
        event=QDragEnterEvent(QPoint(0,0),Qt.CopyAction,self.mime,Qt.LeftButton,Qt.NoModifier)
        self.assertFalse(self.router.eventFilter(self.other,event));self.assertFalse(self.calls)
        remote=QMimeData();remote.setUrls([QUrl('https://example.com/file.mp4')])
        self.assertEqual(local_paths(remote),[])
    def test_native_drop_preserves_unicode_paths_and_deduplicates(self):
        mime=QMimeData();mime.setUrls([QUrl.fromLocalFile('/日本語 video.mp4'),QUrl.fromLocalFile('/日本語 video.mp4')])
        paths=local_paths(mime);self.assertEqual(len(paths),1);self.assertIn('日本語',paths[0])
        mime.setData('application/x-clipmaker-asset',b'42')
        self.assertEqual(local_paths(mime),[])

if __name__=='__main__':unittest.main()
