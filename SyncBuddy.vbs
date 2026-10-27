' SyncBuddy launcher - no console window at all
' Double-click this instead of the .cmd if you don't want the brief black flash
Option Explicit

Dim fso, sh, base, pyw, script
Set fso = CreateObject("Scripting.FileSystemObject")
Set sh  = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
script = fso.BuildPath(base, "syncbuddy.py")

pyw = sh.ExpandEnvironmentStrings("%USERPROFILE%") & _
      "\.workbuddy\binaries\python\envs\default\Scripts\pythonw.exe"
If Not fso.FileExists(pyw) Then pyw = "pythonw.exe"

sh.CurrentDirectory = base
' 0 = hidden window, False = don't wait
sh.Run """" & pyw & """ """ & script & """", 0, False
