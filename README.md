# Clip Maker

A Windows video editor for clips, VODs and longer videos. Manual editing works without an AI model. Originals stay in their existing folders; projects store edit instructions and exports create new files.

## Install

Download `Clip-Maker-v1.8.0-Windows.zip` and `Install-Clip-Maker.ps1` from [Releases](https://github.com/CaptainKeat/clip-maker/releases). Put them in the same folder, then run:

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

Select an audio piece to inspect its waveform. Waveforms build automatically; **Retry waveform** retries a failed build. Peaks are cached locally and can be cancelled; use the linked source-audio clips to scrub. Compatible preview windows are cached in memory (up to twelve) and on disk; still-frame previews respond while a new desktop window is prepared. Only visible timeline pieces are drawn at high zoom for long VODs. Drag the red playhead line or triangle, click and drag the ruler, empty lanes or video pieces to scrub. Playback pauses while scrubbing. Hold near a timeline edge to scroll further. Focus the playhead and use Left/Right for frame steps, Shift+Left/Right for one-second steps, or Home/End for the beginning/end.

**Ripple layers** makes clip trims and removals move captions, overlays, soundtracks and bookmarks with the footage. Mark **I** and **O**, then **Ripple cut range** to remove a section. Copy a clip with its attached layers and paste it at the playhead; paste splits the destination clip. Copy/paste also works on individual captions and layers. Undo restores each edit. **Snap** aligns timeline drags with nearby edges, bookmarks and the playhead; hold Shift to bypass snapping. The video lane remains continuous when Ripple layers is off.

In the caption inspector, choose a font, colour, animation and speaker, then **Save speaker preset**. Apply a saved style to one caption or all captions with the same speaker. Applying styles preserves tracked positions and keyframes. The selected preset also styles new typed and Whisper captions. Presets live in the private project database.

For audio, explicitly mark microphone/speech source tracks as **Dialogue**, or give a soundtrack the Dialogue role. Enable **Duck under dialogue** on music/SFX and set its compression ratio. Clip audio and soundtracks have fade-in/out controls. **Crossfade next soundtrack** overlaps the next sound layer with matching fades; it does not overlap video pieces. Preview ducking follows live audio levels; exports use FFmpeg sidechain compression, so their envelopes can differ slightly. Source track and source-in controls select the sound layer's original audio.

## Models and speech

Settings > AI tools stores named Local, Remote, and API connection profiles. Providers include LM Studio, Ollama, OpenAI, OpenRouter, Anthropic, Google Gemini and OpenAI-compatible servers. Save a profile, choose **Use this connection**, and enable LLM tools before generation. Server/model discovery is an explicit metadata request; a model listing does not prove it is loaded or can generate. **Test a short response** sends a small synthetic prompt. Local models are loaded in their provider application; Clip Maker does not automatically change loaded models.

Remote connections can use HTTPS or explicitly allowed HTTP. Hosted API connections require HTTPS. API keys are encrypted for the current Windows account with DPAPI, live separately from settings, and are never returned to the browser. Changing a profile endpoint clears its previous key. Local keeps transcript text on the PC; remote/API generation sends transcript text to that server and hosted APIs may charge. Video files are not uploaded by this connector. Existing LM Studio/controller settings continue to work until a profile is explicitly activated.

Settings > Speech checks an installed whisper.cpp executable and downloaded model, finds nearby model files, and selects language (including auto), automatic GPU/CPU or CPU-only, threads, quality, vocabulary hints and English translation. Automatic device selection depends on the installed Whisper build. English-only `.en.bin` models require English; use multilingual models for other languages. The Windows package includes a pinned whisper.cpp CPU runtime and a 57 MiB multilingual base-q5_1 model. On a new installation, or when copied desktop paths are unavailable, automatic captions use the bundled pair without setup or an internet connection. Valid custom engine/model paths remain preferred in Automatic mode; choose Built-in to explicitly use the included model. Advanced paths stay available for larger models. The bundled engine uses CPU, including on AMD handhelds; custom GPU setups remain supported. The smaller included model can be less accurate on noisy or overlapping speech, so review the draft. No runtime model download occurs.

In the editor, select a video piece and **Auto captions**, select an audio track, and start local Whisper. The original trim range is extracted to a private local WAV; cues account for playback speed. Correct the draft before **Add captions to edit**. Existing captions are preserved, and changed source trims or speeds require a fresh transcription. No LLM is needed. Transcription can be cancelled; files, diagnostics and previous completed transcripts are retained. **Review transcript** reopens the last completed draft. Finish or cancel a running transcription before closing the installed app.

Provider protocols follow the official [Ollama compatibility API](https://docs.ollama.com/api/openai-compatibility), [OpenAI Chat API](https://developers.openai.com/api/reference/resources/chat), [Anthropic Messages API](https://platform.claude.com/docs/en/api/messages/create) and [Gemini compatibility API](https://ai.google.dev/gemini-api/docs/openai). Whisper options follow the [whisper.cpp CLI](https://github.com/ggml-org/whisper.cpp/blob/master/examples/cli/README.md).

## Motion captions

Select a caption and drag it in the preview. **Save position here**, scrub forward and move it again to make a path. Keys interpolate smoothly. Caption times and keys use the edited timeline.

For visual tracking, choose **Choose / adjust target**. The editor prepares the caption's first frame before selection. Draw a box over a distinctive detail, then drag the box to move it, drag its handles to resize, or use Target X/Y/Width/Height for precise adjustments. Arrow keys nudge the focused target; Shift makes larger steps. **Redraw** replaces the box; **Clear target** removes it. Targets stay available when switching captions during the current editing session, and are shown at each caption's first frame. Place the caption, then **Track caption**. Larger target boxes are sampled across their entire area instead of being silently cropped. This follows a local image patch without an AI model. It is limited to 30 seconds per cue and stops when confidence drops or a cut is reached. Review and correct paths after camera changes, occlusion or target-size changes.

## Preview and export

Timeline playback uses cached VP9/Opus windows of up to 20 seconds, with five seconds of overlap and one-second keyframes. Playback quality can be Fast (up to 360p), Balanced (540p), or Clear (720p); previews never upscale small sources. The next window is prepared during playback. While paused, a separate still frame shows up to 1080p. These preview settings do not reduce export quality. Exports read the original files and use H.264/AAC. Long exports can take time and disk space: intermediate pieces and failure logs are retained in the private data directory.

## Optional AI

AI tools start **Off**. Manual editing, typed captions, tracking and export never require model inference. Settings → AI tools enables the existing highlight-finding and automatic-caption workflow and lets you configure an external Whisper executable/model and local model-server URL. Enabling AI does not load a model. The Windows app opens this workflow in the default browser; keep Clip Maker running while using it.

The bundled Whisper model and speaker models are shipped; no LLM weights, recordings, credentials or publishing configuration are shipped. The offline speaker models described below are bundled. LM Studio and custom Whisper installations are optional external tools. The source-only Parakeet integration needs its separate Python/runtime dependencies; the Windows package uses the external Whisper path.

## Updates

Settings → **Updates** checks stable releases from `CaptainKeat/clip-maker`, shows notes, and offers **Download update** followed by **Install & restart**. Downloads must match GitHub's size and SHA-256 digest. A separate hidden helper waits for the app to exit, preserves the previous installation, swaps files, and waits for the new app's UI readiness signal. Failed starts restore the previous app. Updates preserve user data and original media. Installation waits for exports to finish and saves pending edits first.

Portable copies need a writable parent directory. Update downloads, helper logs and receipts are retained under the private data directory.

Windows packages use compact attribution filenames and are checked against a legacy updater path budget. Updates also use extended Windows paths during extraction and staging. If an older version fails to download a previous release due to a long filename, check for updates again and download v1.4.1 or newer; no Windows registry change is required. Failed update attempts are retained.

## Develop

Use Python 3.14 on Windows and install `requirements.txt`. FFmpeg and FFprobe must be on PATH for source development.

```powershell
python -m pip install -r requirements.txt
python desktop_app.py
python -m unittest discover -s tests
node tests/test_motion_captions.cjs
```

For a browser workspace use `python -m clipmaker serve`. Developer settings are in ignored `clipmaker.json`; installed settings live outside the source tree.

To build, install PyInstaller 6.22.2. Install requirements into `vendor` if isolation is desired. Run `python scripts/fetch_media_tools.py` and `python scripts/fetch_whisper.py` to fetch the pinned, hash-checked media and caption tools, then `powershell -File build.ps1`. Package the resulting `dist/Clip Maker` directory as the root of the versioned Windows ZIP. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) for licenses and dependency source links.


### Waveforms, saving and speaker tracks

Clips automatically build a local source waveform in an aligned row below their video. Additional audio and speaker tracks get waveforms too. The audio Inspector shows a **Waveform stream** menu only when the source has multiple embedded audio streams. This display choice is per clip and does not change playback. Cached peaks are reused across edits.

The desktop close prompt offers Save project, Exit without saving and Cancel for pending changes or unnamed projects. Save project is also available in the editor toolbar. Earlier autosaves remain available when exiting without saving.

Split speakers scans the selected video piece's chosen audio stream offline, using bundled WeSpeaker ResNet34 and pyannote segmentation models. No LLM or download is needed after installation. Scans run in a separate process with two inference threads and five-minute analysis chunks. Review and rename the aligned FLAC tracks before adding them. Applying mutes only the chosen source audio stream on that video piece; Undo restores it. Background, uncertain speech and overlapping voices are retained in separate tracks. Speaker names are estimates, consistent across the scanned piece; overlapping voices are retained together, not isolated. Preview the first 90 seconds in the review and use the timeline for the full tracks. Scan another source piece separately when needed.

Preview layouts and audio peaks are cached. The paused editor redraws on changes instead of continuously painting; recovery snapshots are debounced during dragging. Preview exports still use original footage.


### Grouped timeline

The fixed timeline sidebar groups Captions, Effects, Video sources, Audio sources and Bookmarks in that order. Collapse any group to focus the timeline; the choice is saved with the project. Video and image overlays stay in the video group. Linked source waveforms, extra soundtracks and speaker tracks stay together in the audio group. Muted source waveforms are dimmed.

Effects shows cues for existing fades, freeze endings and caption animation/motion. A cue selects its owning clip or caption for editing; its timing follows that item. It is not an independent filter layer. Bookmark selection now exposes its name and time in the inspector.


### Resizable workspace, tracks and source monitor

Drag the horizontal divider above the timeline upward to make the timeline taller. Drag the dividers beside the Sources panel and Inspector to resize their widths. Focus a divider and use arrow keys for keyboard resizing. Panel sizes stay local to this app/browser; Reset layout restores the defaults. The installed Windows window can also be resized and maximized.

Track headers offer Mute (M) and Solo (S) for linked source audio and each sound/speaker lane, output visibility (V) for video, overlays and captions, Lock (L), and a height menu. Visibility and audio controls affect preview and exports. Hidden main video produces black while keeping its audio; hiding overlays/captions removes only those visuals. Lock protects content from edits, including ripple changes; linked source audio cannot be moved by a trim while locked. Collapsing a group remains a viewing preference. Track controls and heights save with the project and support Undo/Redo. Audio clips now show filled amplitude bars mirrored around their centerline, matching a conventional editor waveform. Both linked source audio and sound/speaker lanes use this view. The separate overview strip is removed; waveform controls live in the selected audio clip Inspector; the stream selector appears only for sources with multiple embedded audio streams.

Choose Preview on a video or audio in Sources to open the separate source monitor. Choose its audio stream, scrub or play it, set source In/Out numerically or with I/O buttons, and Insert range at the timeline playhead. Inserting video splits the destination clip and follows Ripple layers; audio ranges become sound layers. Source playback pauses timeline playback. Source points are independent of the timeline range marks.

The Bookmarks tab searches names and timestamps, jumps to a result, and offers previous/next navigation. Select a bookmark to rename it or adjust its timeline time. Bookmarks keep their timing through ripple edits and source-range insertion.

### Effects and handheld editing

Open **Effects** beside Sources and Bookmarks. Search for **Split speakers**, drag it onto linked source audio or an added audio clip, or select the clip and press **Apply to selected audio**. Choose its stream and speaker count, scan locally, review names, then add **Spliced speaker turns** or **Aligned full-length tracks**. Spliced turns become individual timed clips in speaker lanes. Background and overlaps remain available. This is diarization and routing of the original mix; it does not isolate simultaneous voices from each other. Scan cancellation leaves the edit unchanged. Applying replaces only the selected audio piece or mutes the chosen linked stream, and Undo restores it. Locked tracks and changed-source scans are protected. An edit supports up to 100 sound pieces; choose aligned tracks for scans with too many turns.

Settings > General > **Handheld & touch** selects Automatic, Handheld, or Desktop layout. Automatic uses the compact workspace on small landscape windows and coarse-pointer screens. Sources, Effects and Inspector open as side panels; **Audio focus** shows audio tracks while retaining the complete edit. Touch controls provide 5-second seek and timeline zoom, and track headers have larger controls. Layout choice and optional controller navigation stay local to each device. Windows can resize the installed app to 800 × 480; 960 × 540 and 1280 × 720 are representative views for scaled handheld displays.

Optional **Controller navigation** supports a standard browser gamepad while the app is focused: D-pad/left stick moves focus, A presses a focused button or checkbox, X toggles playback, LB/RB seeks five seconds, and B closes a side panel. Text and dropdown choices remain available through touch. Physical Ally controller mapping and performance require device testing; these controls do not operate other Windows applications. Close review dialogs through their controls; a running scan must be cancelled first.

The Windows package includes a standalone multiresolution icon. The installer uses it for desktop/Start shortcuts and Installed Apps, and the desktop sets its application icon and Windows taskbar identity. On launch, a packaged app refreshes only existing Clip Maker shortcuts that point to that exact executable, so updates can repair old icons without recreating unrelated shortcuts.

The Effects workflow is informed by [Adobe's documented clip-effect workflow](https://helpx.adobe.com/uk/premiere/desktop/add-video-effects/apply-video-effects/apply-effects.html) and inspected workspace declarations. Clip Maker uses its own source and rendering pipeline. Premiere's native Mercury implementation has not been recovered; REA native analysis requires a configured deep-analysis provider.


### Additional tracks and linked clips

Use **+ Video track**, **+ Audio track**, or **+ Caption track** to create a named, saved lane, including an empty one. Click its header to choose the destination for new media; rename it or remove an empty track in the Inspector. Drop source media or Windows Explorer files onto a track to place them at the drop time. Choose **Place on track** in a clip Inspector to move it to another lane of the same kind. Track controls apply to each lane.

The main V1 edit stays continuous and determines sequence duration. Additional video and image tracks are timed layers within that duration; higher video tracks composite above lower tracks. Audio and caption tracks use the same duration. Add footage to V1 to extend the sequence before placing more layers. An edit supports 32 additional named tracks and up to 100 video/image layers and 100 sound pieces.

Adding a video to an additional video track places its audio on a separate audio track and links the two pieces. Clicking either highlights both. Timing moves, handle trims, splits, copy/paste and removal affect the pair together; each split or pasted pair has its own link. **Unlink audio and video** makes the pieces independent. Standalone audio, images and silent videos select individually. The main V1/A1 source pieces remain linked to the same underlying edit. Undo restores track and clip changes. Copying the main edit with attached layers preserves the copied layers' links without linking them back to the originals.


### Effects workspaces

Choose **FilmCraft**, **Editing**, **Effects**, **Audio** or **Captions** beside the project name. The FilmCraft preset uses Source/Effect Controls on the left, Program beside them, a full-height Effects library on the right, and Project/Bookmarks, Tools, Timeline and live stereo Program meters below. The editor uses compact command menus and locally bundled Inter/JetBrains Mono fonts; the homepage retains Clip Maker navigation. Media supports search and list/thumbnail view. File/Edit/Clip/Sequence/Window menus invoke existing editor commands, rather than duplicate edit logic. V/S switches selection/scrub; T adds a caption; Shift+1/2/3/4/5/7 opens Project/Source/Timeline/Program/Effect Controls/Effects. See the [UI audit](docs/filmcraft-ui-audit.md) for provenance and intentional differences. Drag a panel tab to another panel edge to split the workspace, or onto its tab bar or centre to group panels as tabs. Ctrl-drag floats a panel inside the app window. Drag splitters to resize; focused splitters also accept arrow keys. The panel menu offers docking, floating, closing and maximize/restore; double-click a tab or press backtick in its panel to maximize. **Panels** reopens closed panels. **Save layout** creates or updates a named workspace; **Reset layout** restores that workspace's saved snapshot. Workspaces and current arrangements are stored in private app data, survive random desktop ports, and remain separate from project/export content. Saved layouts are shared by windows using the same app-data profile. Floating panels remain within the app window; native detached windows and second-monitor floating are not supported. Audio and Captions start with their effects category selected.

Effects & tools contains supported actions: local Split speakers and transcription, audio volume/fades, ducking/crossfade controls, video framing/speed/freeze controls, caption tracking/styles and caption animation. A **Controls** entry opens an existing property; **Apply** changes a caption animation; a **Tool** starts a reviewed analysis. Drag an entry onto a compatible clip, or select a clip and click/tap it. Incompatible targets give an explanation.

Split speakers opens inline in Effect Controls. Pick the source stream and speaker count, scan locally, cancel if needed, listen and rename results, then choose spliced turns or aligned tracks. Apply creates one undoable edit. Review remains attached to its original project and clip; changed source clips invalidate the result. Hide/reopen the section to retain a running scan or review. Use selected audio starts a fresh context. WeSpeaker labels speech turns and retains the original mix within them; it does not isolate simultaneous voices from the mix.

Handheld mode uses Sources / Effects / Controls drawers and keeps the lower timeline available. Tap works without dragging. Panel preferences are local UI settings, separate from exported project content. Controls and timeline shortcuts retain the existing editing, linked selection, track lock and Undo semantics.

Reference provenance: independently implemented UI patterns from [Adobe's Effects documentation](https://helpx.adobe.com/premiere/desktop/add-video-effects/apply-video-effects/find-and-group-effects.html) and [FilmCraft's dock source](https://github.com/storytold/filmcraft/blob/71438c627b5cbcfb550526c3971f4d0037733c5c/crates/ui-egui/src/dock.rs), pinned to 71438c627b5cbcfb550526c3971f4d0037733c5c. REA 6.4.0 verified a bounded capture of installed Premiere workspace resource declarations. Adobe resource copies stay private and are excluded from releases. No Adobe/FilmCraft implementation code or native rendering engine was copied. Docking/grouping behavior also follows Adobe's public [panel documentation](https://helpx.adobe.com/premiere/desktop/get-started/tour-the-workspace/dock-group-undock-panels.html) and [clip-group documentation](https://helpx.adobe.com/in/premiere/desktop/edit-projects/change-clip-sequence/group-clips.html). Split-tree layouts and media groups are independent implementations.

### Timeline media groups

Shift-click media to add/remove pieces from the selection, or drag a marquee in empty timeline space with **Select tool** enabled. **Group** (Ctrl+G) creates a named group; **Ungroup** (Ctrl+Shift+G) removes membership while preserving audio/video links. Click a group member to select the whole group; Alt-click selects one piece for individual properties and trims. **Linked selection** controls partner selection; grouping always includes linked partners. Ctrl+A selects all media; Escape clears selection. Bookmarks are independent timeline markers.

Drag selected media together, or use the selection's Move time and earlier/later controls in Effect Controls. Groups support copy/paste, duplicate, split, remove and enable/disable through the toolbar or right-click menu. Relative offsets and caption motion keys follow group moves; new copies get independent group/link identities. Group membership and enabled state save with the project; commands support Undo/Redo and respect track locks. Disabled main video retains its duration as black and silent; disabled layers are skipped in previews and exports. Split speakers retains group membership when deriving speaker tracks.

V1 remains a continuous main edit: adjacent main-video selections reorder at clip boundaries, and ripple moves attached layers. Nonadjacent main selections must be regrouped or edited separately for move/copy. Additional-track media moves freely within the main edit's duration. Individual trims use Alt selection; shared head/tail group trimming is not implemented. Link/Unlink applies to video/audio pairs on additional tracks; the main edit's source audio remains intrinsic to its video clip. This is a focused editing model rather than full Premiere timeline parity.
