"""Route Explorer file drops from the WebEngine render child into the studio."""
import json
from PySide6.QtCore import QObject,QEvent,Qt
from PySide6.QtWidgets import QWidget


def local_paths(mime):
    if mime.hasFormat('application/x-clipmaker-asset') or not mime.hasUrls(): return []
    paths=[];seen=set()
    for url in mime.urls():
        if not url.isLocalFile(): continue
        path=url.toLocalFile()
        if path and path.casefold() not in seen:
            paths.append(path);seen.add(path.casefold())
    return paths


class DesktopDropFilter(QObject):
    def __init__(self,view,notify,parent=None):
        super().__init__(parent);self.view=view;self.notify=notify;self.active=False

    def eventFilter(self,watched,event):
        kind=event.type()
        if kind not in {QEvent.DragEnter,QEvent.DragMove,QEvent.DragLeave,QEvent.Drop}: return False
        if not isinstance(watched,QWidget) or not (watched is self.view or self.view.isAncestorOf(watched)): return False
        if kind==QEvent.DragLeave:
            if not self.active:return False
            self.active=False;self.notify('leave',{});event.accept();return True
        paths=local_paths(event.mimeData())
        if not paths:return False
        point=watched.mapTo(self.view,event.position().toPoint())
        # CopyAction tells Explorer to keep the originals, even when it offers Move.
        event.setDropAction(Qt.CopyAction);event.accept()
        self.active=kind!=QEvent.Drop
        self.notify('drop' if kind==QEvent.Drop else 'drag',{'paths':paths,'x':point.x(),'y':point.y()})
        return True
