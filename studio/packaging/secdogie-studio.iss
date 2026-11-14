; Inno Setup script: secdogie Studio for Windows, as a double-click setup.exe.
;
;   iscc /DAppVersion=0.10.0 /DSourceDir=..\dist\secdogie-studio /DOutputDir=..\dist packaging\secdogie-studio.iss
;
; Installs for the current user only (no administrator rights, nothing
; system-wide), adds a Start-menu entry and an uninstaller, and offers a
; desktop shortcut that is off by default. It never starts Studio at login,
; installs no service and leaves nothing running: Studio, and the node it
; starts, stop when the window closes. Uninstalling removes the program; your
; keys and journal stay in your profile (%APPDATA%\secdogie-studio) unless you
; delete them yourself.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef SourceDir
  #define SourceDir "..\dist\secdogie-studio"
#endif
#ifndef OutputDir
  #define OutputDir "..\dist"
#endif

[Setup]
AppId={{6F1E3C2A-8B54-4C1D-9E7A-5D2B9C4A1F30}
AppName=secdogie Studio
AppVersion={#AppVersion}
AppPublisher=secdogie
AppPublisherURL=https://github.com/Larrydev-cpp/secdogie
DefaultDirName={localappdata}\Programs\secdogie Studio
DefaultGroupName=secdogie Studio
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=secdogie-studio-setup
SetupIconFile=icons\secdogie-studio.ico
UninstallDisplayIcon={app}\secdogie-studio.exe
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\secdogie Studio"; Filename: "{app}\secdogie-studio.exe"
Name: "{group}\Uninstall secdogie Studio"; Filename: "{uninstallexe}"
Name: "{autodesktop}\secdogie Studio"; Filename: "{app}\secdogie-studio.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\secdogie-studio.exe"; Description: "{cm:LaunchProgram,secdogie Studio}"; Flags: nowait postinstall skipifsilent
