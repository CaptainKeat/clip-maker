param([string]$Python="python",[switch]$Incremental)
$ErrorActionPreference="Stop"
$project = $PSScriptRoot
Set-Location -LiteralPath $project
$env:PYTHONPATH=Join-Path $project 'vendor'
$pythonExe=(Get-Command $Python).Source
$env:PATH=(Split-Path -Parent $pythonExe)+';'+(Join-Path $env:SystemRoot 'System32')+';'+$env:SystemRoot
$arguments=@('-m','PyInstaller','--noconfirm','--onedir','--windowed','--name','Clip Maker',
    '--paths',(Join-Path $project 'vendor'),'--icon',(Join-Path $project 'assets/clipmaker.ico'),
    '--add-data','clipmaker/static;clipmaker/static','--add-data','assets;assets',
    '--add-data','third_party/qt;third_party/qt','--add-data','third_party/python;third_party/python',
    '--add-data','third_party/ffmpeg/LICENSE;third_party/ffmpeg','--add-data','third_party/ffmpeg/README.txt;third_party/ffmpeg',
    '--add-data','third_party/ffmpeg/manifest.json;third_party/ffmpeg','--add-binary','third_party/ffmpeg/bin/ffmpeg.exe;media-tools',
    '--add-binary','third_party/ffmpeg/bin/ffprobe.exe;media-tools',
    '--add-data','THIRD_PARTY_NOTICES.md;.','--copy-metadata','numpy')
foreach($module in @('torch','transformers','onnx_asr','onnxruntime','scipy','matplotlib','pandas','IPython','tkinter','pytest','librosa','sklearn','soundfile','numba','llvmlite','jax','jaxlib')) {
    $arguments += @('--exclude-module',$module)
}
$arguments += 'desktop_app.py'
if(-not $Incremental){$arguments += '--clean'}
& $pythonExe @arguments
if($LASTEXITCODE -ne 0){throw 'Desktop build failed'}
Copy-Item -LiteralPath (Join-Path $project 'README.md') -Destination (Join-Path $project 'dist/Clip Maker/README.md')
Copy-Item -LiteralPath (Join-Path $project 'LICENSE') -Destination (Join-Path $project 'dist/Clip Maker/LICENSE')
Copy-Item -LiteralPath (Join-Path $project 'Uninstall-Clip-Maker.ps1') -Destination (Join-Path $project 'dist/Clip Maker/Uninstall-Clip-Maker.ps1')
