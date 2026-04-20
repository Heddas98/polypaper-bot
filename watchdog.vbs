' ========================================
' PolyPaper Bot Watchdog VBScript Wrapper
' Phase 57: Single-instance guard + runs watchdog.bat invisibly
' ========================================

Set fso = CreateObject("Scripting.FileSystemObject")
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' Single-instance: check if watchdog.bat is already running
Set objWMI = GetObject("winmgmts:\\.\root\cimv2")
Set colProcs = objWMI.ExecQuery("SELECT ProcessId FROM Win32_Process WHERE CommandLine LIKE '%watchdog.bat%' AND Name = 'cmd.exe'")

If colProcs.Count > 0 Then
    ' Already running — exit silently
    WScript.Quit 0
End If

Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = scriptDir
WshShell.Run chr(34) & scriptDir & "\watchdog.bat" & chr(34), 0
Set WshShell = Nothing
Set fso = Nothing
