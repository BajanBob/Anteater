; Inno Setup 6 script for the Windows installer.
; Build after PyInstaller:  ISCC.exe /DAppVersion=0.1.0 build\windows\installer.iss
; Output: dist\AnteaterSetup-<version>.exe

#ifndef AppVersion
  #define AppVersion "0.1.0"
#endif

[Setup]
AppId={{6B1E3A52-4C1F-4E0B-9C1D-A47E0E5D2B11}
AppName=Anteater
AppVersion={#AppVersion}
AppPublisher=Anteater contributors
AppPublisherURL=https://github.com/USER/anteater
DefaultDirName={autopf}\Anteater
DefaultGroupName=Anteater
DisableProgramGroupPage=yes
LicenseFile=..\..\LICENSE
OutputDir=..\..\dist
OutputBaseFilename=AnteaterSetup-{#AppVersion}
SetupIconFile=..\icons\anteater.ico
UninstallDisplayIcon={app}\anteater.exe
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequiredOverridesAllowed=dialog
ChangesEnvironment=yes

[Tasks]
Name: "addtopath"; Description: "Add anteater to PATH (use it from any terminal)"; Flags: unchecked

[Files]
Source: "..\..\dist\anteater.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\..\config\anteater.example.toml"; DestDir: "{app}\config"; Flags: ignoreversion
Source: "..\..\config\allowlist.example.yaml"; DestDir: "{app}\config"; Flags: ignoreversion
Source: "..\..\README.md"; DestDir: "{app}"; Flags: ignoreversion isreadme
Source: "..\..\LICENSE"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\Anteater dashboard"; Filename: "{app}\anteater.exe"; Parameters: "ui"; WorkingDir: "{userdocs}\Anteater"
Name: "{group}\Anteater self-test"; Filename: "{cmd}"; Parameters: "/k ""{app}\anteater.exe"" selftest --no-radio"; WorkingDir: "{userdocs}\Anteater"
Name: "{group}\Uninstall Anteater"; Filename: "{uninstallexe}"

[Dirs]
Name: "{userdocs}\Anteater"

[Registry]
Root: HKA; Subkey: "{code:EnvKey}"; ValueType: expandsz; ValueName: "Path"; \
  ValueData: "{olddata};{app}"; Tasks: addtopath; Check: NeedsAddPath(ExpandConstant('{app}'))

[Code]
function EnvKey(Param: string): string;
begin
  if IsAdminInstallMode then
    Result := 'SYSTEM\CurrentControlSet\Control\Session Manager\Environment'
  else
    Result := 'Environment';
end;

function NeedsAddPath(Dir: string): Boolean;
var
  Paths: string;
  RootKey: Integer;
begin
  if IsAdminInstallMode then RootKey := HKEY_LOCAL_MACHINE else RootKey := HKEY_CURRENT_USER;
  if not RegQueryStringValue(RootKey, EnvKey(''), 'Path', Paths) then
  begin
    Result := True;
    exit;
  end;
  Result := Pos(';' + Uppercase(Dir) + ';', ';' + Uppercase(Paths) + ';') = 0;
end;
