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
AppPublisherURL=https://www.partyops.cn/
AppSupportURL=https://www.partyops.cn/guide
AppUpdatesURL=https://www.partyops.cn/
DefaultDirName={autopf}\PartyOps
DefaultGroupName=党建智办
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
OutputDir={#OutputRoot}
OutputBaseFilename=PartyOps_1.4.3-rc.2_windows_amd64
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern dynamic hidebevels
WizardBackColor=#fff8ee
WizardBackColorDynamicDark=#211816
DisableWelcomePage=no
DisableProgramGroupPage=yes
UsePreviousAppDir=yes
RestartApplications=no
ShowLanguageDialog=no
SetupLogging=yes
; 安装器自身与卸载项使用 PartyOps 品牌图标（来自打包目录 partyops.ico）
SetupIconFile={#BuildRoot}\partyops.ico
UninstallDisplayIcon={app}\partyops.ico
WizardSmallImageFile={#BuildRoot}\partyops-1024.png

[Languages]
; 安装器固定使用简体中文，避免新手在主流程中看到英文 Inno Setup 文案。
Name: "chinesesimp"; MessagesFile: "{#SourcePath}\languages\ChineseSimplified.isl"

[Messages]
BeveledLabel=PartyOps 1.4.3-rc.2 · 未签名候选版
WelcomeLabel1=欢迎使用党建智办 PartyOps 安装向导
WelcomeLabel2=本向导将安装 [name/ver]。%n%n程序安装目录和业务数据目录可以分别选择；升级时会保留原有选择。安装前会自动安全停止旧服务。
SelectDirDesc=选择 PartyOps 程序安装目录
SelectDirLabel3=PartyOps 程序文件将安装到下面的文件夹。此目录可以自由选择，不会锁定在 C 盘。
SelectDirBrowseLabel=单击“下一步”继续；如需更换目录，请单击“浏览”。
ReadyLabel1=PartyOps 已准备好安装。
ReadyLabel2a=单击“安装”开始；如需修改程序或数据目录，请单击“上一步”。
InstallingLabel=正在安装 PartyOps，并配置本机服务，请稍候。
FinishedHeadingLabel=PartyOps 安装完成
FinishedLabel=程序文件和服务已安装。接下来将在配置向导中选择主机或协同机角色。
ErrorCloseApplications=无法自动关闭正在使用 PartyOps 文件的程序。请关闭 PartyOps 后重试。
ErrorReplacingExistingFile=无法更新正在使用的旧文件：
StatusClosingApplications=正在关闭旧版 PartyOps…
StatusExtractFiles=正在校验并释放 PartyOps 文件…
StatusCreateIcons=正在创建 PartyOps 快捷方式…
StatusRunProgram=正在完成服务配置…

[Dirs]
; 主机数据库、备份、证书与信任公钥只能由服务账户和管理员修改。
; 协同机的 LocalAppData 由以原用户身份运行的向导按需创建，避免管理员安装
; 错把目录建进管理员账户，也避免 Inno Setup 的跨用户目录告警。
Name: "{commonappdata}\PartyOps"; Permissions: admins-full system-full

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

function UnquoteEnvironmentValue(Value: String): String;
var
  FirstCode, LastCode: Integer;
begin
  Result := Trim(Value);
  if Length(Result) < 2 then
    exit;
  FirstCode := Ord(Result[1]);
  LastCode := Ord(Result[Length(Result)]);
  if ((FirstCode = 39) and (LastCode = 39)) or
     ((FirstCode = 34) and (LastCode = 34)) then
  begin
    Delete(Result, Length(Result), 1);
    Delete(Result, 1, 1);
    Result := Trim(Result);
  end;
end;

function LoadConfiguredDataDir(var ConfiguredDataDir: String): Boolean;
var
  EnvironmentLines: TArrayOfString;
  I: Integer;
  Line, Prefix: String;
begin
  Result := False;
  Prefix := 'PARTYOPS_DATA_DIR=';
  if not LoadStringsFromFile(
    ExpandConstant('{commonappdata}\PartyOps\partyops.env'),
    EnvironmentLines
  ) then
    exit;
  for I := 0 to GetArrayLength(EnvironmentLines) - 1 do
  begin
    Line := Trim(EnvironmentLines[I]);
    if CompareText(Copy(Line, 1, Length(Prefix)), Prefix) = 0 then
    begin
      ConfiguredDataDir := UnquoteEnvironmentValue(
        Copy(Line, Length(Prefix) + 1, Length(Line))
      );
      Result := ConfiguredDataDir <> '';
      exit;
    end;
  end;
end;

procedure InitializeWizard;
var
  PreviousDataDir: String;
  PreviousDataDirLines: TArrayOfString;
begin
  WizardForm.Caption := '党建智办 PartyOps 1.4.3-rc.2 安装向导';
  DataDirPage := CreateInputDirPage(
    wpSelectDir,
    '选择 PartyOps 业务数据目录',
    '数据库、附件、备份、证书、模型、缓存和日志统一保存在这里',
    '程序目录与业务数据目录彼此独立。建议选择本机固定数据盘中的独立文件夹；' +
    '支持中文、空格和非 C 盘路径。升级安装会自动保留原目录。',
    False,
    ''
  );
  DataDirPage.Add('');
  PreviousDataDir := '';
  // 卸载重装或升级时，实际主机配置比安装阶段预选标记更可信。
  if not LoadConfiguredDataDir(PreviousDataDir) then
  begin
    if LoadStringsFromFile(
      ExpandConstant('{commonappdata}\PartyOps\install-data-dir.txt'),
      PreviousDataDirLines
    ) and (GetArrayLength(PreviousDataDirLines) > 0) then
      PreviousDataDir := Trim(PreviousDataDirLines[0]);
  end;
  if PreviousDataDir = '' then
    PreviousDataDir := ExpandConstant('{commonappdata}\PartyOps');
  DataDirPage.Values[0] := PreviousDataDir;
end;

function ServiceExists(ServiceName: String): Boolean;
var
  ResultCode: Integer;
begin
  Result := Exec(
    ExpandConstant('{sys}\sc.exe'),
    'query ' + ServiceName,
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  ) and (ResultCode = 0);
end;

function StopServiceBeforeUpgrade(
  ServiceName, ServiceExecutable, DisplayName: String
): String;
var
  ResultCode: Integer;
  ExecutablePath: String;
begin
  Result := '';
  if not ServiceExists(ServiceName) then
    exit;

  ExecutablePath := ExpandConstant('{app}\') + ServiceExecutable;
  if not FileExists(ExecutablePath) then
  begin
    Result := '检测到旧版' + DisplayName + '，但缺少服务管理程序。' +
      '请先卸载损坏的旧版本，再重新运行安装器。';
    exit;
  end;

  if (not Exec(
    ExecutablePath,
    '--wait=45 stop',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  )) or (ResultCode <> 0) then
  begin
    Result := '无法安全停止' + DisplayName + '（诊断码：UPGRADE_SERVICE_STOP_FAILED，退出码：' +
      IntToStr(ResultCode) + '）。请关闭 PartyOps 后重试。';
  end;
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
begin
  WizardForm.StatusLabel.Caption := '正在安全停止旧版 PartyOps 服务…';
  Result := StopServiceBeforeUpgrade(
    'PartyOpsHost', 'PartyOpsService.exe', 'PartyOps 主机服务'
  );
  if Result <> '' then
    exit;
  Result := StopServiceBeforeUpgrade(
    'PartyOpsUpdateService', 'PartyOpsUpdaterService.exe', 'PartyOps 更新服务'
  );
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
  SelectedDataDir: TArrayOfString;
begin
  if CurStep <> ssPostInstall then
    exit;
  ForceDirectories(ExpandConstant('{commonappdata}\PartyOps'));
  SetArrayLength(SelectedDataDir, 1);
  SelectedDataDir[0] := DataDirPage.Values[0];
  if not SaveStringsToUTF8File(
    ExpandConstant('{commonappdata}\PartyOps\install-data-dir.txt'),
    SelectedDataDir,
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
