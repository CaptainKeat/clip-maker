"""Refresh this application's existing Windows shortcuts after an update."""
import subprocess
from pathlib import Path
from .media import CREATE_NO_WINDOW

SCRIPT=r'''
param([string]$Executable,[string]$Icon)
$ErrorActionPreference='Stop'
$shell=New-Object -ComObject WScript.Shell
foreach($folder in @([Environment]::GetFolderPath('Desktop'),[Environment]::GetFolderPath('Programs'))){
    $path=Join-Path $folder 'Clip Maker.lnk'
    if(Test-Path -LiteralPath $path){
        $shortcut=$shell.CreateShortcut($path)
        if([IO.Path]::GetFullPath($shortcut.TargetPath).Equals([IO.Path]::GetFullPath($Executable),[StringComparison]::OrdinalIgnoreCase)){
            $shortcut.IconLocation=$Icon+',0';$shortcut.Save()
        }
    }
}
$key='HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\ClipMaker'
if(Test-Path -LiteralPath $key){
    $entry=Get-ItemProperty -LiteralPath $key
    if($entry.InstallLocation -eq (Split-Path -Parent $Executable)){Set-ItemProperty -LiteralPath $key -Name DisplayIcon -Value $Icon}
}
Add-Type -Namespace ClipMaker -Name IconRefresh -MemberDefinition '[System.Runtime.InteropServices.DllImport("shell32.dll")] public static extern void SHChangeNotify(uint e, uint f, System.IntPtr a, System.IntPtr b);'
[ClipMaker.IconRefresh]::SHChangeNotify(0x08000000,0,[IntPtr]::Zero,[IntPtr]::Zero)
'''

def repair(executable,data_root):
    executable=Path(executable);icon=executable.parent/'clipmaker.ico'
    if not icon.is_file():return None
    script=Path(data_root)/'icon-repair.ps1';script.write_text(SCRIPT,encoding='utf-8-sig')
    with (Path(data_root)/'icon-repair.log').open('ab') as log:
        return subprocess.Popen(['powershell.exe','-NoProfile','-ExecutionPolicy','Bypass','-File',str(script),'-Executable',str(executable),'-Icon',str(icon)],stdout=log,stderr=log,creationflags=CREATE_NO_WINDOW)
