; Inno Setup script for Medleser (per-user installation, no administrator rights needed).
; Build: scripts\build_windows.ps1  (or: ISCC.exe installer\medleser.iss after the PyInstaller build)

#define MyAppName "Medleser"
#define MyAppVersion "1.0.0"
#define MyAppExeName "Medleser.exe"

[Setup]
AppId={{6F1C2B9E-6C8A-4C1E-9B7D-3E2A9F4D1A55}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher=Magnus Nohr
AppPublisherURL=https://github.com/magnus-nohr/medleser
AppSupportURL=https://github.com/magnus-nohr/medleser/issues
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=..\dist
OutputBaseFilename=Medleser-Setup-{#MyAppVersion}
SetupIconFile=..\assets\medleser.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
InfoBeforeFile=installer_info_nb.txt
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0.17763
CloseApplications=yes

[Languages]
Name: "norwegian"; MessagesFile: "compiler:Languages\Norwegian.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Medleser\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\..\LICENSE"; DestDir: "{app}"; DestName: "LICENSE.txt"; Flags: ignoreversion
Source: "..\dist\Brukerveiledning.pdf"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\dist\Installasjonsveiledning.pdf"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Comment: "Lydbøker og synkronisert medlesing"
Name: "{autoprograms}\{#MyAppName} – brukerveiledning"; Filename: "{app}\Brukerveiledning.pdf"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; Dokumenter, lyd og nedlastede stemmer i %LOCALAPPDATA%\Medleser beholdes ved avinstallering
; (se INSTALL_WINDOWS.md for hvordan de slettes).
