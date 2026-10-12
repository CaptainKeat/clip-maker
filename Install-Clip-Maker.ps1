param([string]$Package=(Join-Path $PSScriptRoot 'Clip-Maker-v1.0.0-Windows.zip'),[switch]$NoLaunch)
$ErrorActionPreference='Stop'
$programs=[IO.Path]::GetFullPath((Join-Path $env:LOCALAPPDATA 'Programs'))
$target=[IO.Path]::GetFullPath((Join-Path $programs 'Clip Maker'))
if(-not (Split-Path -Parent $target).Equals($programs,[StringComparison]::OrdinalIgnoreCase)){throw 'Invalid install path'}
if(Get-Process | Where-Object {try {$_.Path -eq (Join-Path $target 'Clip Maker.exe')}catch{$false}}){throw 'Close Clip Maker before installing.'}
$attempt=Join-Path $env:TEMP ('ClipMaker-install-'+[guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $attempt,$programs -Force | Out-Null
Expand-Archive -LiteralPath $Package -DestinationPath $attempt
$payload=Join-Path $attempt 'Clip Maker'
if(-not (Test-Path -LiteralPath (Join-Path $payload 'Clip Maker.exe')) -or -not (Test-Path -LiteralPath (Join-Path $payload '_internal'))){throw 'Incomplete Windows package'}
$backup=$target+'.previous-install-'+(Get-Date -Format 'yyyyMMddHHmmss')
if(Test-Path -LiteralPath $target){Move-Item -LiteralPath $target -Destination $backup}
try{Copy-Item -LiteralPath $payload -Destination $target -Recurse}
catch{if(-not (Test-Path -LiteralPath $target) -and (Test-Path -LiteralPath $backup)){Move-Item -LiteralPath $backup -Destination $target};throw}
$shell=New-Object -ComObject WScript.Shell
foreach($folder in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){
    $shortcut=$shell.CreateShortcut((Join-Path $folder 'Clip Maker.lnk'))
    $shortcut.TargetPath=Join-Path $target 'Clip Maker.exe';$shortcut.WorkingDirectory=$target
    $shortcut.IconLocation=(Join-Path $target 'Clip Maker.exe')+',0';$shortcut.Save()
}
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipMaker'
New-Item -Path $key -Force | Out-Null
$uninstaller=Join-Path $target 'Uninstall-Clip-Maker.ps1'
$values=@{DisplayName='Clip Maker';DisplayVersion='1.0.0';Publisher='CaptainKeat';InstallLocation=$target;DisplayIcon=(Join-Path $target 'Clip Maker.exe');UninstallString='powershell.exe -NoProfile -ExecutionPolicy Bypass -File "'+$uninstaller+'"';NoModify=1;NoRepair=1}
foreach($name in $values.Keys){Set-ItemProperty -Path $key -Name $name -Value $values[$name]}
Write-Output "Installed Clip Maker: $target"
Write-Output "Installation files are preserved at $attempt"
if(-not $NoLaunch){Start-Process -FilePath (Join-Path $target 'Clip Maker.exe') -WindowStyle Normal}
