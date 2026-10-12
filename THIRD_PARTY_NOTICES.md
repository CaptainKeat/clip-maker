# Third-party notices

## FreeCut

Clip Maker's caption phrase grouping, short-form style presets, motion treatment, and retry-recipe design were adapted from and informed by [walterlow/freecut](https://github.com/walterlow/freecut), inspected at commit `8a06b05103a3d5b00cea12743141774573dd06df`.

MIT License

Copyright (c) 2025 FreeCut

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE.

## Optional local transcription components

The optional Parakeet path uses `onnx-asr`, ONNX Runtime, and NVIDIA's Parakeet TDT 0.6B v3 model. Their own licenses and model-card terms apply. These components and downloaded model files live outside Clip Maker's source distribution.

## Windows runtime

- **PySide6 / Shiboken / Qt 6.11.2**: The Qt Company and contributors. Used under LGPL v3; licenses are in `third_party/qt`. The app dynamically loads the shipped DLLs, which can be replaced with compatible builds. Qt and binding sources: https://code.qt.io/cgit/qt/ and https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.11.2 . Reverse engineering for debugging modifications to these libraries is permitted by their license. No Qt source modifications were made.
- **Qt WebEngine / Chromium**: Qt's attribution and license pages for Qt 6.11 are included under `third_party/qt/chromium`, with source URLs in `notice-sources.json`. Index: https://doc.qt.io/qt-6.11/qtwebengine-licensing.html . Source: https://code.qt.io/cgit/qt/qtwebengine.git/tag/?h=v6.11.2 and its Chromium submodule. No engine modifications were made.
- **FFmpeg / FFprobe 8.1.2 essentials (Gyan)**: separate subprocess tools under GPL v3 or later. `third_party/ffmpeg/LICENSE`, `README.txt` and `manifest.json` identify the exact build, upstream commit, external libraries, download URL and executable hashes. Source and build information: https://www.gyan.dev/ffmpeg/builds/ and https://github.com/GyanD/codexffmpeg . These binaries are unmodified.
- **Python 3.14**: Python Software Foundation license and bundled notices in `third_party/python/LICENSE.txt`. Source: https://www.python.org/downloads/source/ .
- **NumPy**: BSD license and bundled-library notices are collected with NumPy in the Windows package. Source: https://github.com/numpy/numpy .
- **PyInstaller 6.22.2**: GPL with the bootloader exception permitting distribution of this app. License: https://pyinstaller.org/en/stable/license.html . Source: https://github.com/pyinstaller/pyinstaller/tree/v6.22.2 .

Clip Maker's MIT license covers its own source. Separately distributed dependencies retain their own licenses.

## Offline speaker scanning

WeSpeaker VoxCeleb ResNet34 (CC BY 4.0, WeSpeaker/VoxCeleb attribution), pyannote segmentation 3.0 (MIT, CNRS 2022), sherpa-onnx 1.13.8 (Apache 2.0) and its ONNX Runtime (MIT). Bundled model origins, checksums and licenses are in `third_party/speaker`.

## Offline automatic captions

The Windows package includes whisper.cpp v1.9.2 and its ggml CPU backends (MIT), and OpenAI Whisper base converted/quantized as ggml-base-q5_1 (MIT, OpenAI 2022). Pinned archive/model origins and SHA-256 checksums are in `third_party/whisper/manifest.json`; licenses are `LICENSE-whisper.cpp` and `LICENSE-model`. The default model is multilingual and runs locally on CPU. The package reuses the Microsoft runtime already shipped with Qt/Python for the child executable.

## Interface fonts and reference

Inter (Rasmus Andersson) and JetBrains Mono (JetBrains) are reused unmodified under OFL-1.1. License texts are in `assets/fonts/OFL-Inter.txt` and `assets/fonts/OFL-JetBrainsMono.txt`; pinned origins and checksums are in `third_party/fonts/manifest.json`. These font files came from FilmCraft's public repository at commit 71438c627b5cbcfb550526c3971f4d0037733c5c. The Clip Maker UI is independently implemented from public panel/layout/theme observations. See `docs/filmcraft-ui-audit.md`; no reference footage or native rendering code is distributed.
