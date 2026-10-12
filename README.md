# Clip Maker

A Windows video editor for clips, VODs and longer videos. Manual editing works without an AI model. Originals stay in their existing folders; projects store edit instructions and exports create new files.

## Install

Download `Clip-Maker-v1.0.0-Windows.zip` and `Install-Clip-Maker.ps1` from [Releases](https://github.com/CaptainKeat/clip-maker/releases). Put them in the same folder, then run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Install-Clip-Maker.ps1
```

The installer adds **Clip Maker** to the desktop and Start menu. It installs for the current user without administrator access. Python and FFmpeg do not need to be installed separately. The ZIP can also be extracted and run directly using `Clip Maker/Clip Maker.exe`.

Settings and projects live under `%LOCALAPPDATA%\ClipMaker`; app files live under `%LOCALAPPDATA%\Programs\Clip Maker`. Uninstalling keeps projects and media, and archives the app directory for recovery.

## Edit a video

1. **Import media** selects videos, images and audio. Choose **Open in editor** for a video, or start a blank project.
2. Add video pieces from the media bin. Drag trim handles or enter source in/out times. Split at the playhead and move pieces earlier or later.
3. Use the inspector for speed, fit/fill, freeze endings and fades. Select and balance individual OBS audio tracks.
4. Type captions, place them over a person, and add position keys or Pop, Shake, Grow or Fall effects. Images and videos can be overlays; audio can be a soundtrack.
5. **Export video** chooses original dimensions, landscape, portrait or square; 720p, 1080p or 2160p; and quality. Follow progress or cancel. Finished and failed exports remain listed.

Projects autosave. Undo/redo is available during the editing session. Concurrent saves from another window are rejected to protect the saved edit. A local draft is retained when a save fails.

Keyboard shortcuts: Space play/pause, Ctrl+S save, Ctrl+Z undo, Ctrl+Shift+Z redo, Ctrl+K split, Delete remove, M bookmark.

Full source durations are retained. There is no short-clip duration cap. The first release has one sequential video lane with additional overlay, caption, sound and bookmark lanes.

## Motion captions

Select a caption and drag it in the preview. **Save position here**, scrub forward and move it again to make a path. Keys interpolate smoothly. Caption times and keys use the edited timeline.

For visual tracking, choose **Select tracking target**, draw a small box over a distinctive detail, place the caption, then **Track caption**. This follows a local image patch without an AI model. It is limited to 30 seconds per cue and stops when confidence drops or a cut is reached. Review and correct paths after camera changes, occlusion or target-size changes.

## Preview and export

The desktop uses cached VP9/Opus preview windows near the playhead because its bundled browser does not decode every recording codec. Each window is at most 90 seconds and reflects the chosen audio mix. Exports read the original files and use H.264/AAC. Long exports can take time and disk space: intermediate pieces and failure logs are retained in the private data directory.

## Optional AI

AI tools start **Off**. Manual editing, typed captions, tracking and export never require model inference. Settings → AI tools enables the existing highlight-finding and automatic-caption workflow and lets you configure an external Whisper executable/model and local model-server URL. Enabling AI does not load a model. The Windows app opens this workflow in the default browser; keep Clip Maker running while using it.

No model weights, recordings, credentials or publishing configuration are shipped. Whisper.cpp and LM Studio are optional external tools. The source-only Parakeet integration needs its separate Python/runtime dependencies; the Windows package uses the external Whisper path.

## Updates

Settings → **Updates** checks stable releases from `CaptainKeat/clip-maker`, shows notes, and offers **Download update** followed by **Install & restart**. Downloads must match GitHub's size and SHA-256 digest. A separate hidden helper waits for the app to exit, preserves the previous installation, swaps files, and waits for the new app's UI readiness signal. Failed starts restore the previous app. Updates preserve user data and original media. Installation waits for exports to finish and saves pending edits first.

Portable copies need a writable parent directory. Update downloads, helper logs and receipts are retained under the private data directory.

## Develop

Use Python 3.14 on Windows and install `requirements.txt`. FFmpeg and FFprobe must be on PATH for source development.

```powershell
python -m pip install -r requirements.txt
python desktop_app.py
python -m unittest discover -s tests
node tests/test_motion_captions.cjs
```

For a browser workspace use `python -m clipmaker serve`. Developer settings are in ignored `clipmaker.json`; installed settings live outside the source tree.

To build, install PyInstaller 6.22.2. Install requirements into `vendor` if isolation is desired. Run `python scripts/fetch_media_tools.py` to fetch the pinned, hash-checked media tools, then `powershell -File build.ps1`. Package the resulting `dist/Clip Maker` directory as the root of the versioned Windows ZIP. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licenses and dependency source links.
