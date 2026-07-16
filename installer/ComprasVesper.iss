#define MyAppName "ComprasVesper"
#ifndef MyAppVersion
  #define MyAppVersion "4.8.0"
#endif
#define MyAppPublisher "ComprasVesper"
#define MyAppExeName "ComprasVesper.exe"

[Setup]
AppId={{6B7C8B79-84E9-4D80-B09D-39D848A5A6DF}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\ComprasVesper
DefaultGroupName=ComprasVesper
OutputDir=..\dist_installer
OutputBaseFilename=Setup_ComprasVesper_v{#MyAppVersion}
SetupIconFile=..\app\assets\icons\comprasvesper.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\comprasvesper.ico

[Languages]
Name: "brazilianportuguese"; MessagesFile: "compiler:Languages\BrazilianPortuguese.isl"

[Tasks]
Name: "desktopicon"; Description: "Criar atalho na area de trabalho"; GroupDescription: "Atalhos:"; Flags: unchecked

[Files]
Source: "..\dist\ComprasVesper\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\app\assets\icons\comprasvesper.ico"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\version.txt"; DestDir: "{app}"; Flags: ignoreversion

[InstallDelete]
Type: files; Name: "{app}\updates\apply_update.cmd"
Type: filesandordirs; Name: "{app}\_internal\numpy"
Type: filesandordirs; Name: "{app}\_internal\numpy.libs"
Type: filesandordirs; Name: "{app}\_internal\numpy-*.dist-info"

[Icons]
Name: "{autoprograms}\ComprasVesper"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\comprasvesper.ico"
Name: "{autodesktop}\ComprasVesper"; Filename: "{app}\{#MyAppExeName}"; IconFilename: "{app}\comprasvesper.ico"; Tasks: desktopicon

[Run]
Filename: "{cmd}"; Parameters: "/C """"{app}\{#MyAppExeName}"" --prewarm --force-refresh >nul 2>&1 || exit /b 0"""; StatusMsg: "Preparando cache inicial..."; Flags: runhidden waituntilterminated
Filename: "{app}\{#MyAppExeName}"; Description: "Abrir ComprasVesper"; Flags: nowait postinstall skipifsilent unchecked
