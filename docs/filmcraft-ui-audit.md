# FilmCraft interface audit and Clip Maker implementation

Reference: FilmCraft public source and its Effects screenshot at commit `71438c627b5cbcfb550526c3971f4d0037733c5c` (verified current on October 12, 2026). Sources: [dock layouts](https://github.com/storytold/filmcraft/blob/71438c627b5cbcfb550526c3971f4d0037733c5c/crates/ui-egui/src/dock.rs), [theme](https://github.com/storytold/filmcraft/blob/71438c627b5cbcfb550526c3971f4d0037733c5c/crates/ui-egui/src/theme.rs), [UI parity notes](https://github.com/storytold/filmcraft/blob/71438c627b5cbcfb550526c3971f4d0037733c5c/docs/ui-parity.md). This is a source/screenshot audit, not a claim of running every FilmCraft feature or matching its rendering engine.

| Observed reference | Clip Maker change | Observable acceptance |
| --- | --- | --- |
| Compact neutral panels, underlined tabs, blue active values, Inter and JetBrains Mono | Editor theme with licensed fonts, consistent fields and focus styling | Fonts load locally; keyboard focus remains visible; original waveforms stay readable |
| Workspace navigation across the top; traditional command menus | Workspace shortcuts and File/Edit/Clip/Sequence/Window menus tied to supported commands | Save, Undo, Group and panel commands act on the existing editor; shortcuts avoid typing fields |
| Source and Effect Controls share the upper-left stack, Program alongside, Effects at the right | New FilmCraft workspace; Project/Bookmarks below; timeline with Tools and Audio meters | Panels still dock/float; old saved layouts remain available; new layout survives restart |
| Dense project browser with search | Searchable media bin with list/thumbnail modes | Filtering retains source-preview and media-insertion actions |
| Tools and output meters beside the timeline | Selection, scrub, split, caption and marker controls; live stereo preview meters | Controls change real editor state; generated speech produces meter levels while playing |
| Small, contextual controls instead of repeated large action bars | Compact desktop controls and menus | Desktop timeline has room for tracks; 800x480 handheld retains touch targets and drawers |

Intentional differences: only Clip Maker's implemented tools appear. No inactive Lumetri, Graphics, slip/slide, trim monitor or mixer replicas are presented. Caption styles and speaker analysis remain in Effect Controls; floating panels remain inside the app window. V1 is still a continuous main edit. Handheld mode keeps larger controls rather than scaling down the desktop UI. Existing projects, selection, Undo/Redo, track locks and saved layouts remain the shared foundation.

The UI implementation is independent. Inter and JetBrains Mono are reused under OFL-1.1, with licenses and pinned checksums included. No Adobe resources, FilmCraft media or native rendering code ship in Clip Maker.
