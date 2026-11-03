; Build with package.py. Payload paths are relative and contain no account data.
[Setup]
AppId=ChatRescue.Windows.Experimental
AppName=Chat Rescue
AppVersion={#AppVersion}
AppPublisher=Chat Rescue contributors
AppPublisherURL=https://github.com/Bccccccs/chat_rescue
DefaultDirName={localappdata}\Programs\ChatRescue
DefaultGroupName=Chat Rescue
PrivilegesRequired=lowest
ArchitecturesAllowed=x64os
ArchitecturesInstallIn64BitMode=x64os
MinVersion=10.0.19041
OutputDir={#ReleaseDir}
OutputBaseFilename=Chat-Rescue-{#AppVersion}-Windows-x64-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
LicenseFile={#PayloadDir}\LICENSE
InfoBeforeFile={#PayloadDir}\FIRST-RUN.txt
UninstallDisplayIcon={app}\ChatRescue.exe
CloseApplications=no
RestartApplications=no
SetupLogging=yes

[Languages]
Name: "chinese"; MessagesFile: "ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[CustomMessages]
chinese.DesktopIcon=创建桌面快捷方式
english.DesktopIcon=Create a desktop shortcut
chinese.OpenApp=打开 Chat Rescue
english.OpenApp=Open Chat Rescue
chinese.Licenses=开源许可
english.Licenses=Open source licenses

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopIcon}"; Flags: unchecked

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\Chat Rescue\Chat Rescue"; Filename: "{app}\ChatRescue.exe"
Name: "{userdesktop}\Chat Rescue"; Filename: "{app}\ChatRescue.exe"; Tasks: desktopicon
Name: "{userprograms}\Chat Rescue\{cm:Licenses}"; Filename: "{app}\licenses"

[Run]
Filename: "{app}\ChatRescue.exe"; Description: "{cm:OpenApp}"; Flags: nowait postinstall skipifsilent unchecked

[Code]
function InitializeSetup(): Boolean;
begin
  Result := IsDotNetInstalled(net48, 0);
  if not Result then
    MsgBox('Chat Rescue requires Microsoft .NET Framework 4.8 or later. Install it using Windows Update or the official Microsoft installer, then run Setup again.', mbError, MB_OK);
end;

// Account settings, keys, jobs and backups live outside {app}. Never delete them.
