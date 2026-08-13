#define MyAppName "党建智办 PartyOps"
#define MyAppVersion "1.4.3-rc.2"
#define MyAppPublisher "PartyOps Local"
#define BuildRoot GetEnv("PARTYOPS_WINDOWS_BUILD_ROOT")
#define OutputRoot GetEnv("PARTYOPS_WINDOWS_OUTPUT_ROOT")

[Setup]
AppId={{1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion=1.4.3.2
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\PartyOps
DefaultGroupName=党建智办
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir={#OutputRoot}
OutputBaseFilename=PartyOps_1.4.3-rc.2_windows_amd64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
SetupLogging=yes
; 安装器自身与卸载项使用 PartyOps 品牌图标（来自打包目录 partyops.ico）
SetupIconFile={#BuildRoot}\partyops.ico
UninstallDisplayIcon={app}\partyops.ico

[Dirs]
; 主机数据库、备份、证书与信任公钥只能由服务账户和管理员修改。
; 协同机日常用户只写自己的 LocalAppData，不能借同机低权限账号篡改主机数据。
Name: "{commonappdata}\PartyOps"; Permissions: admins-full system-full
Name: "{localappdata}\PartyOps"; Permissions: users-modify

[Files]
Source: "{#BuildRoot}\*"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion

[Icons]
Name: "{group}\党建智办"; Filename: "{app}\PartyOpsLauncher.exe"; IconFilename: "{app}\partyops.ico"
Name: "{group}\管理本机共享文件夹"; Filename: "{app}\PartyOpsWizard.exe"; Parameters: "--manage-shared-roots"; IconFilename: "{app}\partyops.ico"
Name: "{commondesktop}\党建智办"; Filename: "{app}\PartyOpsLauncher.exe"; IconFilename: "{app}\partyops.ico"

[Registry]
Root: HKCR; Subkey: "partyops-file"; ValueType: string; ValueName: ""; ValueData: "URL:PartyOps File Protocol"; Flags: uninsdeletekey
Root: HKCR; Subkey: "partyops-file"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCR; Subkey: "partyops-file\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\PartyOpsFileOpen.exe"" ""%1"""
Root: HKCR; Subkey: "partyops-client"; ValueType: string; ValueName: ""; ValueData: "URL:PartyOps Client Protocol"; Flags: uninsdeletekey
Root: HKCR; Subkey: "partyops-client"; ValueType: string; ValueName: "URL Protocol"; ValueData: ""
Root: HKCR; Subkey: "partyops-client\shell\open\command"; ValueType: string; ValueName: ""; ValueData: """{app}\PartyOpsWizard.exe"" --manage-shared-roots --action-uri ""%1"""

[Run]
Filename: "{app}\PartyOpsLauncher.exe"; Description: "启动党建智办配置向导"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{app}\PartyOpsService.exe"; Parameters: "--wait=30 stop"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopHostService"
Filename: "{app}\PartyOpsService.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RemoveHostService"
Filename: "{app}\PartyOpsUpdaterService.exe"; Parameters: "--wait=30 stop"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopUpdateService"
Filename: "{app}\PartyOpsUpdaterService.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RemoveUpdateService"
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""党建智办主机"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveFirewallRule"

[Code]
var
  ServiceSetupFailed: Boolean;
  DataDirPage: TInputDirWizardPage;

procedure InitializeWizard;
begin
  DataDirPage := CreateInputDirPage(
    wpSelectDir,
    '预选主机数据目录',
    '选择业务数据、附件、备份与日志的保存位置',
    '如果这台电脑将作为主机，建议选择本机数据盘中的独立空文件夹。' +
    '稍后的首次配置仍可修改；如果只作为协同机，可保持默认值。',
    False,
    ''
  );
  DataDirPage.Add('');
  DataDirPage.Values[0] := ExpandConstant('{commonappdata}\PartyOps');
end;

procedure RunChecked(FileName, Parameters, Description: String);
var
  ResultCode: Integer;
begin
  if (not Exec(FileName, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode)) or
     (ResultCode <> 0) then
  begin
    ServiceSetupFailed := True;
    RaiseException(Description + '失败，退出码：' + IntToStr(ResultCode));
  end;
end;

function GetCustomSetupExitCode: Integer;
begin
  if ServiceSetupFailed then
    Result := 1
  else
    Result := 0;
end;

function ServiceInstallAction(ServiceName: String): String;
var
  ResultCode: Integer;
begin
  if Exec(ExpandConstant('{sys}\sc.exe'), 'query ' + ServiceName, '', SW_HIDE,
      ewWaitUntilTerminated, ResultCode) and (ResultCode = 0) then
    Result := 'update'
  else
    Result := 'install';
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
begin
  if CurStep <> ssPostInstall then
    exit;
  ForceDirectories(ExpandConstant('{commonappdata}\PartyOps'));
  if not SaveStringToFile(
    ExpandConstant('{commonappdata}\PartyOps\install-data-dir.txt'),
    DataDirPage.Values[0],
    False
  ) then
    RaiseException('保存主机数据目录预选项失败');
  RunChecked(
    ExpandConstant('{app}\PartyOpsService.exe'),
    '--startup manual ' + ServiceInstallAction('PartyOpsHost'),
    '安装 PartyOps 主机服务'
  );
  RunChecked(
    ExpandConstant('{sys}\sc.exe'),
    'failure PartyOpsHost reset= 86400 actions= restart/5000/restart/15000/',
    '配置 PartyOps 主机服务恢复策略'
  );
  RunChecked(
    ExpandConstant('{sys}\sc.exe'),
    'sdset PartyOpsHost "D:(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;SY)(A;;CCDCLCSWRPWPDTLOCRSDRCWDWO;;;BA)(A;;CCLCSWRPLOCRRC;;;IU)(A;;CCLCSWLOCRRC;;;SU)"',
    '配置 PartyOps 主机服务启动权限'
  );
  RunChecked(
    ExpandConstant('{app}\PartyOpsUpdaterService.exe'),
    '--startup manual ' + ServiceInstallAction('PartyOpsUpdateService'),
    '安装 PartyOps 更新服务'
  );
  RunChecked(
    ExpandConstant('{sys}\sc.exe'),
    'failure PartyOpsUpdateService reset= 86400 actions= restart/5000/restart/15000/',
    '配置 PartyOps 更新服务恢复策略'
  );
  Exec(
    ExpandConstant('{sys}\netsh.exe'),
    'advfirewall firewall delete rule name="党建智办主机"',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );
end;
