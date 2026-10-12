$ErrorActionPreference='Stop'
$programs=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$target=[IO.Path]::GetFullPath((Join-Path $programs 'Clip Maker'))
if(-not $target.Equals([IO.Path]::GetFullPath($PSScriptRoot),[StringComparison]::OrdinalIgnoreCase)){throw 'Run the uninstaller from the installed Clip Maker directory.'}
if(-not (Split-Path -Parent $target).Equals($programs,[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid install path'}
if(Get-Process | Where-Object {try {$_.Path -eq (Join-Path $target 'Clip Maker.exe')}catch{$false}}){throw 'Close Clip Maker before uninstalling.'}
foreach($folder in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){
    $shortcut=Join-Path $folder 'Clip Maker.lnk';if(Test-Path -LiteralPath $shortcut){Remove-Item -LiteralPath $shortcut}
}
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipMaker'
if(Test-Path -LiteralPath $key){Remove-Item -LiteralPath $key}
# Rename first so the script currently in use and all files remain recoverable.
$archived=$target+'.uninstalled-'+(Get-Date -Format 'yyyyMMddHHmmss')
if(-not (Split-Path -Parent $archived).Equals($programs,[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid archive path'}
Move-Item -LiteralPath $target -Destination $archived
Write-Output "Uninstalled. Projects, settings and original media were preserved. App files: $archived"
