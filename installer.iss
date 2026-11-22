; WebP·PNG 변환기 - Windows 설치 프로그램 (Inno Setup 6)
; 만드는 법: 먼저 PyInstaller 폴더형 빌드(dist\WebPConverter)를 만든 뒤
;   ISCC.exe /DAppVersion=1.0.0 installer.iss     → dist\WebPConverter-Windows-Setup.exe
; (build_windows.bat 이 자동으로 해줍니다)
#ifndef AppVersion
  #define AppVersion "1.0.0"
#endif

[Setup]
; AppId 는 프로그램 고유 번호입니다. 바꾸지 마세요 (바꾸면 업데이트 설치가 아니라 별개 프로그램으로 설치됨)
AppId={{B2F6C8A1-5E3D-4A77-9C21-7D4E8F0A1B35}
AppName=WebP·PNG 변환기
AppVersion={#AppVersion}
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\WebPConverter
DefaultGroupName=WebP·PNG 변환기
DisableProgramGroupPage=yes
UninstallDisplayIcon={app}\WebPConverter.exe
UninstallDisplayName=WebP·PNG 변환기
OutputDir=dist
OutputBaseFilename=WebPConverter-Windows-Setup
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
; 관리자 권한 없이 내 계정에만 설치 (C:\Users\이름\AppData\Local\Programs\WebPConverter).
; 이 위치는 쓰기가 가능해서 설정 파일이 프로그램 옆에 그대로 저장됩니다.
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
CloseApplications=yes

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕 화면에 바로가기 만들기"; GroupDescription: "추가 작업:"; Flags: unchecked

[Files]
Source: "dist\WebPConverter\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\WebP·PNG 변환기"; Filename: "{app}\WebPConverter.exe"
Name: "{group}\제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\WebP·PNG 변환기"; Filename: "{app}\WebPConverter.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\WebPConverter.exe"; Description: "지금 실행"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; 프로그램이 만든 설정 파일도 함께 지움
Type: files; Name: "{app}\WebPConverter.settings.json"
