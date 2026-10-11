' Optional Windows launcher: starts RadarScope without a console window.
' Put this file (or a shortcut to it) in your Startup folder to run at login:
'   Win+R  ->  shell:startup  ->  copy radarscope.vbs there.
' Edit VenvDir below to point at the folder containing your .venv.
Option Explicit
Dim shell, fso, VenvDir, pythonw
Set shell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

VenvDir = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\radarscope\.venv"
pythonw = VenvDir & "\Scripts\pythonw.exe"

If Not fso.FileExists(pythonw) Then
    MsgBox "RadarScope: couldn't find " & pythonw & vbCrLf & _
           "Edit VenvDir in radarscope.vbs.", vbExclamation, "RadarScope"
    WScript.Quit 1
End If

' 0 = hidden window, False = don't wait
shell.Run """" & pythonw & """ -m radarscope", 0, False
