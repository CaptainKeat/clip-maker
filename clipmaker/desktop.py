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
    from PySide6.QtWidgets import QApplication, QMainWindow, QFileDialog, QMessageBox, QInputDialog
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
    if sys.platform=='win32':
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID('CaptainKeat.ClipMaker')
    app = QApplication(sys.argv)
    app.setApplicationName("Clip Maker");app.setOrganizationName("ClipMaker")
    app_icon=QIcon(str(bundle/'assets'/'clipmaker.ico'))
    app.setWindowIcon(app_icon)
    if sys.platform=='win32' and getattr(sys,'frozen',False):
        from .desktop_icons import repair
        try:repair(sys.executable,data_root)
        except OSError:logging.exception('Could not refresh application shortcuts')
    lock = QLockFile(str(data_root/"desktop.lock"));lock.setStaleLockTime(0)
    if not lock.tryLock(100):
        QMessageBox.information(None,"Clip Maker","Clip Maker is already running.")
        return 0

    class Bridge(QObject):
        filesSelected = Signal(str)
        whisperSelected = Signal(str,str)
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
        @Slot(str)
        def pickWhisper(self,kind):
            if kind not in {"cli","model"}: return
            path,_=QFileDialog.getOpenFileName(window,"Choose Whisper "+kind,str(Path(settings.whisper_model).parent),
                "Whisper model (*.bin)" if kind=="model" else "Whisper executable (*.exe)")
            if path: self.whisperSelected.emit(kind,path)
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
            with JOBS_LOCK: running=any(j.get("status")=="running" and not j.get("id","").startswith("studio-waveform-") for j in JOBS.values())
            if running and not self.updating:
                QMessageBox.information(self,"Job in progress","Finish or cancel the current media task before closing Clip Maker.")
                event.ignore();return
            if not self.updating and not getattr(self,"saveComplete",False):
                if getattr(self,"closing",False):event.ignore();return
                self.closing=True;event.ignore()
                def flush(script):
                    page.runJavaScript("Promise.resolve("+script+").then(()=>window.desktopBridge.saveFinished(true,''),e=>window.desktopBridge.saveFinished(false,String(e)))")
                    def deadline():
                        if self.closing and not getattr(self,"saveComplete",False):
                            self.closing=False;page.runJavaScript("window.studioCancelClose && window.studioCancelClose()")
                            QMessageBox.warning(self,"Save needs attention","Clip Maker stayed open because saving did not finish. Save your project or choose Exit without saving.")
                    QTimer.singleShot(10000,deadline)
                def inspected(value):
                    try:state=json.loads(value) if value else {}
                    except (ValueError,TypeError):state={"dirty":True}
                    if not state.get('dirty') and not state.get('unnamed'):return flush("window.studioFlush ? window.studioFlush() : undefined")
                    box=QMessageBox(self);box.setWindowTitle("Close Clip Maker?")
                    box.setText("Save your project before closing?")
                    box.setInformativeText("Previously autosaved work remains available. Exit without saving skips pending changes.")
                    save_button=box.addButton("Save project",QMessageBox.ButtonRole.AcceptRole)
                    exit_button=box.addButton("Exit without saving",QMessageBox.ButtonRole.DestructiveRole)
                    cancel_button=box.addButton("Cancel",QMessageBox.ButtonRole.RejectRole)
                    box.setDefaultButton(save_button);box.setEscapeButton(cancel_button);box.exec()
                    if box.clickedButton()==exit_button:return flush("window.studioExit ? window.studioExit() : undefined")
                    if box.clickedButton()==save_button:
                        name=state.get('name','')
                        if state.get('unnamed'):
                            name,ok=QInputDialog.getText(self,"Save project","Project name:",text=name)
                            if not ok or not name.strip():
                                self.closing=False;page.runJavaScript("window.studioCancelClose()")
                                return
                        return flush("window.studioSaveNamed("+json.dumps(name.strip())+")")
                    self.closing=False;page.runJavaScript("window.studioCancelClose && window.studioCancelClose()")
                page.runJavaScript("window.studioBeginClose ? window.studioBeginClose() : '{}'",inspected)
                return
            server.shutdown();server.server_close();lock.unlock()
            event.accept()

    window = Window();window.setWindowTitle("Clip Maker");window.resize(1440,920);window.setMinimumSize(800,480)
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
            page.runJavaScript("window.studioCancelClose && window.studioCancelClose()")
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
