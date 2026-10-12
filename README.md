# Clip Maker

A Windows video editor for clips, VODs and longer videos. Manual editing works without an AI model. Originals stay in their existing folders; projects store edit instructions and exports create new files.

## Install

Download `Clip-Maker-v1.2.0-Windows.zip` and `Install-Clip-Maker.ps1` from [Releases](https://github.com/CaptainKeat/clip-maker/releases). Put them in the same folder, then run:

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

Drag files from Windows Explorer onto **Projects** to start an edit, an existing project card to append media, or the **media bin** to import without changing the edit. Drop on the editor to append video clips and place images or audio at the playhead; the overlay and audio lanes accept their respective media. Media already in the bin can also be dragged onto the editor. Multiple files are supported with import progress and cancellation.

The installed app links original files in place. Browser drops and the browser file picker stream copies into private local storage on this PC. Interrupted copies and completed imports are retained when an import is cancelled. Move or rename linked originals only after exporting or restoring their saved paths.

Projects autosave. Undo/redo is available during the editing session. Concurrent saves from another window are rejected to protect the saved edit. A local draft is retained when a save fails.

Keyboard shortcuts: Space play/pause, Ctrl+S save, Ctrl+Z undo, Ctrl+Shift+Z redo, Ctrl+K split, Ctrl+C/X/V copy/cut/paste, I/O mark range, Delete remove, M bookmark.

Full source durations are retained. There is no short-clip duration cap. The first release has one sequential video lane with additional overlay, caption, sound and bookmark lanes.

## Manual editing tools

Select a video piece and choose its audio track, then **Build waveform**. Peaks are cached locally and can be cancelled; use the whole-edit audio overview to seek. Compatible preview windows are cached in memory (up to eight) and on disk; still-frame previews respond while a new desktop window is prepared. Only visible timeline pieces are drawn at high zoom for long VODs. Drag the red playhead line or triangle, click and drag the ruler, empty lanes or video pieces, or drag the audio overview to scrub. Playback pauses while scrubbing. Hold near a timeline edge to scroll further. Focus the playhead and use Left/Right for frame steps, Shift+Left/Right for one-second steps, or Home/End for the beginning/end.

**Ripple layers** makes clip trims and removals move captions, overlays, soundtracks and bookmarks with the footage. Mark **I** and **O**, then **Ripple cut range** to remove a section. Copy a clip with its attached layers and paste it at the playhead; paste splits the destination clip. Copy/paste also works on individual captions and layers. Undo restores each edit. **Snap** aligns timeline drags with nearby edges, bookmarks and the playhead; hold Shift to bypass snapping. The video lane remains continuous when Ripple layers is off.

In the caption inspector, choose a font, colour, animation and speaker, then **Save speaker preset**. Apply a saved style to one caption or all captions with the same speaker. Applying styles preserves tracked positions and keyframes. The selected preset also styles new typed and Whisper captions. Presets live in the private project database.

For audio, explicitly mark microphone/speech source tracks as **Dialogue**, or give a soundtrack the Dialogue role. Enable **Duck under dialogue** on music/SFX and set its compression ratio. Clip audio and soundtracks have fade-in/out controls. **Crossfade next soundtrack** overlaps the next sound layer with matching fades; it does not overlap video pieces. Preview ducking follows live audio levels; exports use FFmpeg sidechain compression, so their envelopes can differ slightly. Source track and source-in controls select the sound layer's original audio.

## Models and speech

Settings > AI tools stores named Local, Remote, and API connection profiles. Providers include LM Studio, Ollama, OpenAI, OpenRouter, Anthropic, Google Gemini and OpenAI-compatible servers. Save a profile, choose **Use this connection**, and enable LLM tools before generation. Server/model discovery is an explicit metadata request; a model listing does not prove it is loaded or can generate. **Test a short response** sends a small synthetic prompt. Local models are loaded in their provider application; Clip Maker does not automatically change loaded models.

Remote connections can use HTTPS or explicitly allowed HTTP. Hosted API connections require HTTPS. API keys are encrypted for the current Windows account with DPAPI, live separately from settings, and are never returned to the browser. Changing a profile endpoint clears its previous key. Local keeps transcript text on the PC; remote/API generation sends transcript text to that server and hosted APIs may charge. Video files are not uploaded by this connector. Existing LM Studio/controller settings continue to work until a profile is explicitly activated.

Settings > Speech checks an installed whisper.cpp executable and downloaded model, finds nearby model files, and selects language (including auto), automatic GPU/CPU or CPU-only, threads, quality, vocabulary hints and English translation. Automatic device selection depends on the installed Whisper build. English-only `.en.bin` models require English; use multilingual models for other languages. The app does not download models automatically.

In the editor, select a video piece and **Transcribe clip**, select an audio track, and start local Whisper. The original trim range is extracted to a private local WAV; cues account for playback speed. Correct the draft before **Add captions to edit**. Existing captions are preserved, and changed source trims or speeds require a fresh transcription. No LLM is needed. Transcription can be cancelled; files, diagnostics and previous completed transcripts are retained. **Review transcript** reopens the last completed draft. Finish or cancel a running transcription before closing the installed app.

Provider protocols follow the official [Ollama compatibility API](https://docs.ollama.com/api/openai-compatibility), [OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat), [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages/create) and [Gemini compatibility API](https://ai.google.dev/gemini-api/docs/openai). Whisper options follow the [whisper.cpp CLI](https://github.com/ggml-org/whisper.cpp/blob/master/examples/cli/README.md).

## Motion captions

Select a caption and drag it in the preview. **Save position here**, scrub forward and move it again to make a path. Keys interpolate smoothly. Caption times and keys use the edited timeline.

For visual tracking, choose **Choose / adjust target**. The editor prepares the caption's first frame before selection. Draw a box over a distinctive detail, then drag the box to move it, drag its handles to resize, or use Target X/Y/Width/Height for precise adjustments. Arrow keys nudge the focused target; Shift makes larger steps. **Redraw** replaces the box; **Clear target** removes it. Targets stay available when switching captions during the current editing session, and are shown at each caption's first frame. Place the caption, then **Track caption**. Larger target boxes are sampled across their entire area instead of being silently cropped. This follows a local image patch without an AI model. It is limited to 30 seconds per cue and stops when confidence drops or a cut is reached. Review and correct paths after camera changes, occlusion or target-size changes.

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


### Waveforms, saving and speaker tracks

Clips automatically build a local source waveform in an aligned row below their video. Additional audio and speaker tracks get waveforms too. The selected source-track menu controls the clip waveform. Cached peaks are reused across edits.

The desktop close prompt offers Save project, Exit without saving and Cancel for pending changes or unnamed projects. Save project is also available in the editor toolbar. Earlier autosaves remain available when exiting without saving.

Split speakers scans the selected video piece's chosen audio stream offline, using bundled WeSpeaker ResNet34 and pyannote segmentation models. No LLM or download is needed after installation. Scans run in a separate process with two inference threads and five-minute analysis chunks. Review and rename the aligned FLAC tracks before adding them. Applying mutes only the chosen source audio stream on that video piece; Undo restores it. Background, uncertain speech and overlapping voices are retained in separate tracks. Speaker names are estimates, consistent across the scanned piece; overlapping voices are retained together, not isolated. Preview the first 90 seconds in the review and use the timeline for the full tracks. Scan another source piece separately when needed.

Preview layouts and overview waveforms are cached. The paused editor redraws on changes instead of continuously painting; recovery snapshots are debounced during dragging. Preview exports still use original footage.
