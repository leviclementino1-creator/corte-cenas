; ============================================================
;  Corte Cenas - Inno Setup script
; ============================================================
;  Wraps dist\CorteCenas\ (PyInstaller onedir output) into a
;  proper Windows installer: Start Menu / Desktop shortcuts,
;  Add/Remove Programs entry, upgrade-in-place support.
;
;  Build via build_installer.bat (which runs PyInstaller first,
;  then invokes ISCC.exe on this file).
;
;  Requires Inno Setup 6+  ->  https://jrsoftware.org/isdl.php
; ============================================================

#define AppName        "Corte Cenas"
#define AppVersion     "0.3.6"
#define AppPublisher   "Levi Clementino"
#define AppExeName     "CorteCenas.exe"
#define AppId          "{{7A3F8B21-4C5D-4E6F-9A1B-2C3D4E5F6A7B}"

[Setup]
AppId={#AppId}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
DefaultDirName={autopf}\CorteCenas
DefaultGroupName=Corte Cenas
UninstallDisplayName={#AppName}
UninstallDisplayIcon={app}\{#AppExeName}
DisableProgramGroupPage=yes
OutputDir=releases
OutputBaseFilename=CorteCenas-Setup-{#AppVersion}
SetupIconFile=app\assets\icon.ico
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=admin
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
; Same AppId across versions => "install over" behavior (upgrade in place).
CloseApplications=force
RestartApplications=no

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
; Copy the entire PyInstaller onedir tree into {app}\
Source: "dist\CorteCenas\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Corte Cenas";        Filename: "{app}\{#AppExeName}"
Name: "{group}\Desinstalar Corte Cenas"; Filename: "{uninstallexe}"
Name: "{autodesktop}\Corte Cenas";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Run]
; runasoriginaluser: o instalador roda elevado, mas o app deve abrir com os
; privilégios normais do usuário — elevado, o Windows bloqueia drag-and-drop
; vindo do Explorer (UIPI).
Filename: "{app}\{#AppExeName}"; Description: "Abrir Corte Cenas"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallDelete]
; Nothing beyond what [Files] tracked. The user's cache/output stays in
; %LOCALAPPDATA%\CorteCenas and their Output folder — we don't touch those.
Type: filesandordirs; Name: "{app}\_internal\__pycache__"

; ============================================================
;  FFmpeg is now bundled inside the installer (bin\ffmpeg.exe),
;  so no external check is needed. The app resolves the bundled
;  binary via app/ffmpeg_locate.py.
; ============================================================
