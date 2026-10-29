#define MyAppName "Offline Voice Studio"
#define MyAppVersion "1.0.0"
#define MyAppPublisher "Jerome Ventigan"
#define MyAppExeName "OfflineVoiceStudio.exe"

[Setup]
AppId={{b7e3f1a2-4c5d-4e6f-8a9b-0c1d2e3f4a5b}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\Offline Voice Studio
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\{#MyAppExeName}
PrivilegesRequired=lowest
ArchitecturesAllowed=x64
ArchitecturesInstallIn64BitMode=x64
MinVersion=10.0
InfoAfterFile=..\docs\USER_GUIDE.txt
OutputDir=installer_output
OutputBaseFilename=OfflineVoiceStudio-Setup
Compression=zip
SolidCompression=no
WizardStyle=modern

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional shortcuts:"; Flags: unchecked

[Files]
Source: "app_dist\OfflineVoiceStudio\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\USER_GUIDE.txt"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autoprograms}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autoprograms}\{#MyAppName}\User Guide"; Filename: "{app}\USER_GUIDE.txt"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName}"; Flags: postinstall nowait skipifsilent
