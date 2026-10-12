' LocalAI Studio app launcher (used by the desktop / Start-menu icon).
' Opens a "starting" page immediately and starts all services in the background without any console window.
' If LocalAI is already running, it simply opens the app.
Option Explicit
Dim sh, fso, root, http, running
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
root = fso.GetParentFolderName(fso.GetParentFolderName(WScript.ScriptFullName))

running = False
On Error Resume Next
Set http = CreateObject("MSXML2.ServerXMLHTTP.6.0")
http.setTimeouts 1000, 1000, 1000, 1000
http.open "GET", "http://127.0.0.1:8000/api/health", False
http.send
If Err.Number = 0 Then
  If http.status = 200 Then running = True
End If
On Error GoTo 0

If running Then
  sh.Run "http://localhost:3000/", 1, False
Else
  sh.Run """" & root & "\config\starting.html""", 1, False
  ' window style 0 = hidden; -Silent skips opening the browser a second time
  sh.Run "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File """ & root & "\scripts\start.ps1"" -Silent", 0, False
End If
