"""Packaged desktop shell; user data lives outside the application directory."""
import json
import logging
import os
import sys
import threading
from pathlib import Path


def main():
    data_root = Path(os.environ.get("CLIPMAKER_DATA", str(Path(os.environ.get("LOCALAPPDATA",str(Path.home()))) / "ClipMaker")))
    data_root.mkdir(parents=True,exist_ok=True)
    os.environ["CLIPMAKER_CONFIG"] = str(data_root/"settings.json")
    os.environ["CLIPMAKER_UPDATE_DATA"] = str(data_root/"updates")
    logging.basicConfig(filename=str(data_root/"desktop.log"),level=logging.INFO)
    # PyInstaller windowed apps have no console streams; backend logs need one.
    if sys.stdout is None: sys.stdout = (data_root/"server.log").open("a",encoding="utf-8",buffering=1)
    if sys.stderr is None: sys.stderr = (data_root/"errors.log").open("a",encoding="utf-8",buffering=1)
    from PySide6.QtCore import QUrl, QObject, Signal, Slot, QTimer, QLockFile, QFile, QIODevice
    from PySide6.QtGui import QIcon, QDesktopServices
    from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog, QMessageBox
    from PySide6.QtWebEngineWidgets import QWebEngineView
    from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineSettings, QWebEngineScript
    from PySide6.QtWebChannel import QWebChannel
    from http.server import ThreadingHTTPServer
    from .settings import Settings, load_settings, save_settings
    from .server import ClipMakerHandler, JOBS, JOBS_LOCK
    from . import __version__
    from .desktop_drop import DesktopDropFilter

    settings_path = data_root/"settings.json"
    if not settings_path.exists():
        defaults = Settings(data_dir=str(data_root/"data"),output_dir=str(Path.home()/"Videos"/"Clip Maker Exports"))
        save_settings(defaults,settings_path)
    settings = load_settings(settings_path)
    bundle = Path(getattr(sys,"_MEIPASS",Path(__file__).resolve().parent.parent))
    for name in ("ffmpeg","ffprobe"):
        executable = bundle/"media-tools"/(name+".exe")
        if executable.exists(): setattr(settings,name,str(executable))
    settings.host="127.0.0.1";settings.tailscale_host=""
    app = QApplication(sys.argv)
    app.setApplicationName("Clip Maker");app.setOrganizationName("ClipMaker")
    lock = QLockFile(str(data_root/"desktop.lock"));lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None,"Clip Maker","Clip Maker is already running.")
        return 0

    class Bridge(QObject):
        filesSelected = Signal(str)
        shutdownRequested = Signal()
        saveAcknowledged = Signal(bool,str)
        @Slot(bool,str)
        def saveFinished(self,ok,error):
            self.saveAcknowledged.emit(ok,error)
        @Slot()
        def pickMedia(self):
            paths,_ = QFileDialog.getOpenFileNames(window,"Import media",str(Path(settings.source_root)),
                "Media (*.mp4 *.mkv *.mov *.avi *.webm *.m4v *.ts *.flv *.wav *.mp3 *.m4a *.ogg *.flac *.aac *.png *.jpg *.jpeg *.webp *.bmp)")
            if paths: self.filesSelected.emit(json.dumps(paths))
        @Slot()
        def openAiWorkspace(self):
            QDesktopServices.openUrl(QUrl(url+"/ai"))

    bridge = Bridge()
    handler = type("DesktopClipMakerHandler",(ClipMakerHandler,),{"settings":settings,"update_ready":lambda self:bridge.shutdownRequested.emit()})
    server = ThreadingHTTPServer(("127.0.0.1",0),handler)
    url = "http://127.0.0.1:"+str(server.server_port)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    (data_root/"desktop-ready.json").write_text(json.dumps({"pid":os.getpid(),"url":url,"version":__version__}),encoding="utf-8")

    class Page(QWebEnginePage):
        def acceptNavigationRequest(self,url_,kind,main_frame):
            if main_frame and not url_.toString().startswith(url+"/"):
                if url_.scheme() in {"http","https"}: QDesktopServices.openUrl(url_)
                return False
            return True
        def javaScriptConsoleMessage(self,level,message,line,source):
            logging.info("JS %s:%s %s",source,line,message)

    class Window(QMainWindow):
        updating=False
        def closeEvent(self,event):
            with JOBS_LOCK: running=any(j.get("status")=="running" for j in JOBS.values())
            if running and not self.updating:
                QMessageBox.information(self,"Job in progress","Finish or cancel the current export before closing Clip Maker.")
                event.ignore();return
            # Frontend autosaves; ask its pending debounce to flush before exit.
            if not self.updating and not getattr(self,"saveComplete",False):
                if getattr(self,"closing",False):
                    event.ignore();return
                self.closing=True;event.ignore()
                view.page().runJavaScript("Promise.resolve(window.studioFlush ? window.studioFlush() : undefined).then(()=>window.desktopBridge.saveFinished(true,''),e=>window.desktopBridge.saveFinished(false,String(e)))")
                def deadline():
                    if self.closing and not getattr(self,"saveComplete",False):
                        self.closing=False
                        QMessageBox.warning(self,"Save needs attention","Clip Maker stayed open because saving did not finish. Save your project before closing.")
                QTimer.singleShot(10000,deadline);return
            server.shutdown();server.server_close();lock.unlock()
            event.accept()

    window = Window();window.setWindowTitle("Clip Maker");window.resize(1440,920);window.setMinimumSize(920,650)
    icon = bundle/"assets"/"clipmaker.ico"
    if icon.exists(): window.setWindowIcon(QIcon(str(icon)))
    view = QWebEngineView(window)
    page = Page(view);view.setPage(page)
    view.setAcceptDrops(True)
    def notify_drop(kind,payload):
        function={'drag':'studioNativeDrag','drop':'studioNativeDrop','leave':'studioNativeLeave'}[kind]
        page.runJavaScript('window.'+function+' && window.'+function+'('+json.dumps(payload)+')')
    drop_filter=DesktopDropFilter(view,notify_drop,window)
    app.installEventFilter(drop_filter)
    page.settings().setAttribute(QWebEngineSettings.WebAttribute.PlaybackRequiresUserGesture,False)
    channel = QWebChannel(page);channel.registerObject("desktop",bridge);page.setWebChannel(channel)
    channel_source = QFile(":/qtwebchannel/qwebchannel.js")
    if channel_source.open(QIODevice.OpenModeFlag.ReadOnly):
        script = QWebEngineScript();script.setName("ClipMakerDesktopBridge")
        script.setInjectionPoint(QWebEngineScript.InjectionPoint.DocumentCreation)
        script.setWorldId(QWebEngineScript.ScriptWorldId.MainWorld)
        script.setSourceCode(bytes(channel_source.readAll()).decode("utf-8"));page.scripts().insert(script)
    window.setCentralWidget(view)
    def update_close():
        window.updating=True;window.close()
    bridge.shutdownRequested.connect(update_close)
    def save_acknowledged(ok,error):
        if not getattr(window,"closing",False): return
        window.closing=False
        if ok:
            window.saveComplete=True;window.close()
        else:
            QMessageBox.warning(window,"Save needs attention",error+"\nYour edit stays open.")
    bridge.saveAcknowledged.connect(save_acknowledged)
    open_project = next((a.partition("=")[2] for a in sys.argv if a.startswith("--open-project=")),"")
    def loaded(ok):
        if not ok: return
        def probe(attempt=0):
            def checked(ready):
                if ready:
                    (data_root/"desktop-ready.json").write_text(json.dumps({"pid":os.getpid(),"url":url,"version":__version__,"ui_ready":True}),encoding="utf-8")
                elif attempt<150: QTimer.singleShot(200,lambda:probe(attempt+1))
            page.runJavaScript("!!window.studioReady && !!window.desktopBridge",checked)
        probe()
    view.loadFinished.connect(loaded)
    view.load(QUrl(url+"/"+("?project="+open_project if open_project else "")));window.show()
    if "--smoke" in sys.argv or os.environ.get("CLIPMAKER_SMOKE")=="1":
        def smoke():
            output = data_root/"desktop-smoke.png"
            window.grab().save(str(output))
            view.page().runJavaScript("JSON.stringify({title:document.title,ready:!!document.querySelector('#studio-root'),bridge:!!window.desktopBridge,videoReady:document.querySelector('#studio-video').readyState,videoError:document.querySelector('#studio-video').error?.message,error:document.querySelector('#studio-message')?.textContent})",
                lambda value:(data_root/"desktop-smoke.json").write_text(json.dumps(value),encoding="utf-8"))
            QTimer.singleShot(1000,app.quit)
        QTimer.singleShot(6000,smoke)
    result = app.exec()
    server.shutdown();server.server_close();lock.unlock()
    return result
