#define MyAppName "党建智办 PartyOps"
#define MyAppVersion "1.4.3-rc.7"
#define MyAppPublisher "PartyOps Local"
#define BuildRoot GetEnv("PARTYOPS_WINDOWS_BUILD_ROOT")
#define OutputRoot GetEnv("PARTYOPS_WINDOWS_OUTPUT_ROOT")
#ifndef PartyOpsOutputBase
  #define PartyOpsOutputBase "PartyOps_1.4.3-rc.7_windows_amd64"
#endif

[Setup]
AppId={{1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
VersionInfoVersion=1.4.3.7
AppPublisher={#MyAppPublisher}
AppPublisherURL=https://www.partyops.cn/
AppSupportURL=https://www.partyops.cn/guide
AppUpdatesURL=https://www.partyops.cn/
DefaultDirName={autopf}\PartyOps
DefaultGroupName=党建智办
#ifdef PartyOpsX86
ArchitecturesAllowed=x86compatible
#else
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
#endif
#ifdef PartyOpsLegacy
MinVersion=6.1sp1
#else
MinVersion=10.0
#endif
PrivilegesRequired=admin
OutputDir={#OutputRoot}
OutputBaseFilename={#PartyOpsOutputBase}
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
BeveledLabel=PartyOps 1.4.3-rc.7 · 未签名候选版
#ifdef PartyOpsLegacy
WinVersionTooLowError=此 Windows 7 专用安装包要求 Windows 7 SP1 或更高版本。请先安装 SP1 后重试。
#else
WinVersionTooLowError=此安装包仅支持 Windows 10/11。Windows 7 SP1 请返回官网下载文件名包含 windows7_amd64 或 windows7_x86 的专用安装包。
#endif
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
Name: "{commonappdata}\PartyOps-System"; Permissions: admins-full system-full

[Files]
Source: "{#BuildRoot}\*"; Excludes: "PartyOpsUpdater.exe,PartyOpsUpdaterService.exe"; DestDir: "{app}"; Flags: recursesubdirs createallsubdirs ignoreversion
; 主机应用内升级期间这两个进程仍承载事务。Windows 不能覆盖正在运行的
; EXE，因此仅它们使用系统重启替换；主程序、前端和其余组件立即生效。
Source: "{#BuildRoot}\PartyOpsUpdater.exe"; DestDir: "{app}"; Flags: ignoreversion restartreplace
Source: "{#BuildRoot}\PartyOpsUpdaterService.exe"; DestDir: "{app}"; Flags: ignoreversion restartreplace
Source: "{#SourcePath}\validate-install-path.ps1"; Flags: dontcopy

[Icons]
Name: "{group}\党建智办"; Filename: "{app}\PartyOpsLauncher.exe"; IconFilename: "{app}\partyops.ico"
Name: "{group}\管理本机共享文件夹"; Filename: "{app}\PartyOpsWizard.exe"; Parameters: "--manage-shared-roots"; IconFilename: "{app}\partyops.ico"
Name: "{commondesktop}\党建智办"; Filename: "{app}\PartyOpsLauncher.exe"; IconFilename: "{app}\partyops.ico"

[Run]
Filename: "{app}\PartyOpsLauncher.exe"; Description: "启动党建智办配置向导"; Flags: nowait postinstall skipifsilent runasoriginaluser
Filename: "{app}\PartyOpsLauncher.exe"; Parameters: "--background"; Flags: nowait runasoriginaluser; Check: WizardSilent

[UninstallRun]
Filename: "{app}\PartyOpsService.exe"; Parameters: "--wait=30 stop"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopHostService"
Filename: "{app}\PartyOpsService.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RemoveHostService"
Filename: "{app}\PartyOpsUpdaterService.exe"; Parameters: "--wait=30 stop"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "StopUpdateService"
Filename: "{app}\PartyOpsUpdaterService.exe"; Parameters: "remove"; Flags: runhidden waituntilterminated skipifdoesntexist; RunOnceId: "RemoveUpdateService"
Filename: "netsh.exe"; Parameters: "advfirewall firewall delete rule name=""党建智办主机"""; Flags: runhidden waituntilterminated; RunOnceId: "RemoveFirewallRule"

[UninstallDelete]
; 程序目录由安装前安全检查主动创建，Inno 不会把它登记为自己创建的目录。
; 仅在全部程序文件已经删除且目录确实为空时移除根目录；任何未知文件都会
; 阻止该动作，绝不递归删除用户内容。
Type: dirifempty; Name: "{app}"

[Code]
type
  TRegistryValueBackup = record
    RootKey: Integer;
    Subkey: String;
    ValueName: String;
    Existed: Boolean;
    ValueData: String;
  end;
  TRegistryKeyBackup = record
    RootKey: Integer;
    Subkey: String;
    Existed: Boolean;
    ExportPath: String;
  end;

var
  ServiceSetupFailed: Boolean;
  InAppServiceUpdate: Boolean;
  DataDirPage: TInputDirWizardPage;
  RegistryBackups: array[0..19] of TRegistryValueBackup;
  RegistryBackupCount: Integer;
  RegistryKeyBackups: array[0..3] of TRegistryKeyBackup;
  RegistryKeyBackupCount: Integer;
  ProtocolRegistryWritten: Boolean;
  HostServiceExistedBeforeInstall: Boolean;
  UpdateServiceExistedBeforeInstall: Boolean;
  HostServiceRunningBeforeInstall: Boolean;
  UpdateServiceRunningBeforeInstall: Boolean;
  HostServiceStartTypeBeforeInstall: Cardinal;
  UpdateServiceStartTypeBeforeInstall: Cardinal;
  HostServiceDelayedBeforeInstall: Cardinal;
  UpdateServiceDelayedBeforeInstall: Cardinal;
  ConfiguredHostModeBeforeInstall: Boolean;
  RestartPreviousServicesOnExit: Boolean;
  InstallCompletedSuccessfully: Boolean;
  InstallerCachePath: String;
  InstallerCachePreviousPath: String;
  InstallerCacheIncomingPath: String;
  InstallerCacheHashPath: String;
  InstallerCacheHashPreviousPath: String;
  InstallerCacheHashIncomingPath: String;
  InstallerCacheHadPrevious: Boolean;
  InstallerCacheHashHadPrevious: Boolean;
  InstallerCacheTransactionActive: Boolean;
  DataMarkerPath: String;
  DataMarkerPreviousPath: String;
  DataMarkerHadPrevious: Boolean;
  DataMarkerTransactionActive: Boolean;
  DeleteAllDataOnUninstall: Boolean;

const
  PartyOpsAppId = '{1C8EFC63-CAFC-46EF-A5E3-D3D119B5BB3A}';
  ClassesPrefix = 'Software\Classes\';

#ifdef PartyOpsLegacy
function GetModuleHandle(ModuleName: String): THandle;
  external 'GetModuleHandleW@kernel32.dll stdcall';
function GetProcAddress(Module: THandle; ProcName: AnsiString): LongWord;
  external 'GetProcAddress@kernel32.dll stdcall';

function HasWindows7LoaderUpdate: Boolean;
var
  Kernel32: THandle;
begin
  { KB2533623 已被后续月度汇总更新取代时，CBS 中未必再保留精确 KB 名称。
    微软要求通过 GetProcAddress 判断 Loader API 是否可用；按真实能力放行，
    既避免误拦截已完整更新的 Win7，也不会在缺少安全加载能力时继续安装。 }
  Kernel32 := GetModuleHandle('kernel32.dll');
  Result :=
    (Kernel32 <> 0) and
    (GetProcAddress(Kernel32, 'AddDllDirectory') <> 0) and
    (GetProcAddress(Kernel32, 'SetDefaultDllDirectories') <> 0);
end;

function ValidateWindows7Prerequisites(var ErrorMessage: String): Boolean;
var
  Version: TWindowsVersion;
  HasLoaderUpdate: Boolean;
begin
  Result := False;
  GetWindowsVersionEx(Version);
  { Legacy 安装器可在更高版本 Windows 上运行；只有 Windows 7 需要旧系统前置项。 }
  if (Version.Major = 6) and (Version.Minor = 1) then
  begin
    if Version.ServicePackMajor < 1 then
    begin
      ErrorMessage := '[WIN7_SP1_REQUIRED] 当前系统不是 Windows 7 SP1。请先安装 SP1 后重新运行 PartyOps 安装器。';
      exit;
    end;
    HasLoaderUpdate := HasWindows7LoaderUpdate;
    if not HasLoaderUpdate then
    begin
      ErrorMessage := '[WIN7_LOADER_API_REQUIRED] 当前 Windows 7 缺少安全 DLL 加载能力。请先完成系统重要更新（至少包含 KB2533623 或其后续汇总更新）并重启，再运行 PartyOps。';
      exit;
    end;
    if not FileExists(ExpandConstant('{sys}\ucrtbase.dll')) then
    begin
      ErrorMessage := '[WIN7_UCRT_REQUIRED] 未检测到 Universal CRT。请安装微软 Universal C Runtime 更新并重启，再运行 PartyOps。';
      exit;
    end;
  end;
  Result := True;
end;
#endif

function RegistryRootName(RootKey: Integer): String;
begin
  if RootKey = HKA then
    Result := 'HKA'
  else if RootKey = HKLM then
    Result := 'HKLM'
  else
    Result := 'HKCU';
end;

function IsPartyOpsProtocol(RootKey: Integer; ProtocolName: String): Boolean;
var
  BaseKey, CommandValue, AppIdValue, ExpectedCommand: String;
begin
  BaseKey := ClassesPrefix + ProtocolName;
  if not RegKeyExists(RootKey, BaseKey) then
  begin
    Result := True;
    exit;
  end;
  CommandValue := '';
  AppIdValue := '';
  RegQueryStringValue(RootKey, BaseKey + '\shell\open\command', '', CommandValue);
  RegQueryStringValue(RootKey, BaseKey, 'PartyOps.AppId', AppIdValue);
  if ProtocolName = 'partyops-file' then
    ExpectedCommand := '"' + ExpandConstant('{app}\PartyOpsFileOpen.exe') + '" "%1"'
  else
    ExpectedCommand := '"' + ExpandConstant('{app}\PartyOpsWizard.exe') +
      '" --manage-shared-roots --action-uri "%1"';
  Result :=
    (CompareText(AppIdValue, PartyOpsAppId) = 0) or
    (CompareText(CommandValue, ExpectedCommand) = 0);
end;

function ValidateProtocolOwnership(var ErrorMessage: String): Boolean;
var
  ProtocolName: String;
  RootIndex, ProtocolIndex, RootKey: Integer;
begin
  Result := False;
  for RootIndex := 0 to 1 do
  begin
    if RootIndex = 0 then RootKey := HKLM else RootKey := HKCU;
    for ProtocolIndex := 0 to 1 do
    begin
      if ProtocolIndex = 0 then ProtocolName := 'partyops-file'
      else ProtocolName := 'partyops-client';
      if not IsPartyOpsProtocol(RootKey, ProtocolName) then
      begin
        ErrorMessage := '[PROTOCOL_REGISTRY_CONFLICT] 检测到外部程序占用了 ' +
          RegistryRootName(RootKey) + '\' + ClassesPrefix + ProtocolName +
          '。安装器没有覆盖该协议，请联系管理员确认归属。';
        exit;
      end;
    end;
  end;
  Result := True;
end;

function ProbeRegistryRoot(RootKey: Integer; var ErrorMessage: String): Boolean;
var
  ProbeKey, ProbeValue: String;
begin
  Result := False;
  ProbeKey := ClassesPrefix + 'PartyOps.RegistryProbe.' + PartyOpsAppId;
  RegDeleteKeyIncludingSubkeys(RootKey, ProbeKey);
  if not RegWriteStringValue(RootKey, ProbeKey, '', 'PartyOps registry preflight') then
  begin
    ErrorMessage := '[PROTOCOL_REGISTRY_DENIED] 无法写入 ' + RegistryRootName(RootKey) +
      '\' + ClassesPrefix + '。请确认安装器已获管理员权限，且安全软件未阻止注册协议。';
    exit;
  end;
  if (not RegQueryStringValue(RootKey, ProbeKey, '', ProbeValue)) or
     (ProbeValue <> 'PartyOps registry preflight') then
  begin
    RegDeleteKeyIncludingSubkeys(RootKey, ProbeKey);
    ErrorMessage := '[PROTOCOL_REGISTRY_VERIFY_FAILED] 注册表写入后回读不一致，安装已安全停止。';
    exit;
  end;
  if not RegDeleteKeyIncludingSubkeys(RootKey, ProbeKey) then
  begin
    ErrorMessage := '[PROTOCOL_REGISTRY_VERIFY_FAILED] 注册表可写性探测项无法清理，安装已安全停止。';
    exit;
  end;
  Result := True;
end;

function PreflightProtocolRegistry(var ErrorMessage: String): Boolean;
begin
  Result := ValidateProtocolOwnership(ErrorMessage);
  if not Result then exit;
  Result := ProbeRegistryRoot(HKA, ErrorMessage);
  if not Result then exit;
  { 只有当前用户已有历史覆盖键时才修复 HKCU；主注册始终写入管理员 HKA
    对应的 HKLM\Software\Classes，不再使用不推荐的 HKCR。 }
  if RegKeyExists(HKCU, ClassesPrefix + 'partyops-file') or
     RegKeyExists(HKCU, ClassesPrefix + 'partyops-client') then
    Result := ProbeRegistryRoot(HKCU, ErrorMessage);
end;

procedure BackupRegistryKey(RootKey: Integer; Subkey: String);
var
  FullKey, ExportPath: String;
  ResultCode: Integer;
begin
  RegistryKeyBackups[RegistryKeyBackupCount].RootKey := RootKey;
  RegistryKeyBackups[RegistryKeyBackupCount].Subkey := Subkey;
  RegistryKeyBackups[RegistryKeyBackupCount].Existed := RegKeyExists(RootKey, Subkey);
  ExportPath := ExpandConstant('{tmp}\partyops-protocol-backup-') +
    IntToStr(RegistryKeyBackupCount) + '.reg';
  DeleteFile(ExportPath);
  RegistryKeyBackups[RegistryKeyBackupCount].ExportPath := ExportPath;
  if RegistryKeyBackups[RegistryKeyBackupCount].Existed then
  begin
    if (RootKey = HKA) or (RootKey = HKLM) then
      FullKey := 'HKEY_LOCAL_MACHINE\' + Subkey
    else
      FullKey := 'HKEY_CURRENT_USER\' + Subkey;
    ResultCode := -1;
    if (not Exec(
      ExpandConstant('{sys}\reg.exe'),
      'export "' + FullKey + '" "' + ExportPath + '" /y',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode
    )) or (ResultCode <> 0) or (not FileExists(ExportPath)) then
      RaiseException(
        '[PROTOCOL_REGISTRY_BACKUP_FAILED] 无法完整备份 ' + FullKey +
        '，安装在写入前已停止。'
      );
  end;
  RegistryKeyBackupCount := RegistryKeyBackupCount + 1;
end;

procedure BackupRegistryValue(RootKey: Integer; Subkey, ValueName: String);
var
  ValueData: String;
begin
  RegistryBackups[RegistryBackupCount].RootKey := RootKey;
  RegistryBackups[RegistryBackupCount].Subkey := Subkey;
  RegistryBackups[RegistryBackupCount].ValueName := ValueName;
  RegistryBackups[RegistryBackupCount].Existed :=
    RegQueryStringValue(RootKey, Subkey, ValueName, ValueData);
  RegistryBackups[RegistryBackupCount].ValueData := ValueData;
  RegistryBackupCount := RegistryBackupCount + 1;
end;

procedure RollbackProtocolRegistry;
var
  I, ResultCode: Integer;
  FullKey, VerifyPath, BackupHash, VerifyHash, FailureDetail: String;
begin
  FailureDetail := '';
  for I := RegistryKeyBackupCount - 1 downto 0 do
  begin
    if RegKeyExists(RegistryKeyBackups[I].RootKey, RegistryKeyBackups[I].Subkey) and
       (not RegDeleteKeyIncludingSubkeys(
        RegistryKeyBackups[I].RootKey,
        RegistryKeyBackups[I].Subkey
      )) then
    begin
      FailureDetail := FailureDetail + '无法删除半写入键 ' +
        RegistryKeyBackups[I].Subkey + '；';
      continue;
    end;
    if RegistryKeyBackups[I].Existed then
    begin
      ResultCode := -1;
      if (not Exec(
        ExpandConstant('{sys}\reg.exe'),
        'import "' + RegistryKeyBackups[I].ExportPath + '"',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode
      )) or (ResultCode <> 0) then
      begin
        FailureDetail := FailureDetail + '无法导入原键 ' +
          RegistryKeyBackups[I].Subkey + '；';
        continue;
      end;
      if (RegistryKeyBackups[I].RootKey = HKA) or
         (RegistryKeyBackups[I].RootKey = HKLM) then
        FullKey := 'HKEY_LOCAL_MACHINE\' + RegistryKeyBackups[I].Subkey
      else
        FullKey := 'HKEY_CURRENT_USER\' + RegistryKeyBackups[I].Subkey;
      VerifyPath := RegistryKeyBackups[I].ExportPath + '.verify';
      DeleteFile(VerifyPath);
      ResultCode := -1;
      if (not Exec(
        ExpandConstant('{sys}\reg.exe'),
        'export "' + FullKey + '" "' + VerifyPath + '" /y',
        '', SW_HIDE, ewWaitUntilTerminated, ResultCode
      )) or (ResultCode <> 0) or (not FileExists(VerifyPath)) then
      begin
        FailureDetail := FailureDetail + '无法回读恢复键 ' +
          RegistryKeyBackups[I].Subkey + '；';
        continue;
      end;
      BackupHash := GetSHA256OfFile(RegistryKeyBackups[I].ExportPath);
      VerifyHash := GetSHA256OfFile(VerifyPath);
      if (BackupHash = '') or (CompareText(BackupHash, VerifyHash) <> 0) then
        FailureDetail := FailureDetail + '恢复键回读不一致 ' +
          RegistryKeyBackups[I].Subkey + '；';
      DeleteFile(VerifyPath);
    end
    else if RegKeyExists(
      RegistryKeyBackups[I].RootKey, RegistryKeyBackups[I].Subkey
    ) then
      FailureDetail := FailureDetail + '新增键仍有残留 ' +
        RegistryKeyBackups[I].Subkey + '；';
  end;
  if FailureDetail <> '' then
    RaiseException(
      '[PROTOCOL_REGISTRY_ROLLBACK_FAILED] 协议注册未能完整恢复：' + FailureDetail +
      '备份保留在安装器临时目录，可供诊断。'
    );
  for I := 0 to RegistryKeyBackupCount - 1 do
    DeleteFile(RegistryKeyBackups[I].ExportPath);
  RegistryBackupCount := 0;
  RegistryKeyBackupCount := 0;
  ProtocolRegistryWritten := False;
end;

procedure WriteProtocolValue(RootKey: Integer; Subkey, ValueName, ValueData: String);
begin
  if not RegWriteStringValue(RootKey, Subkey, ValueName, ValueData) then
  begin
    RollbackProtocolRegistry;
    RaiseException('[PROTOCOL_REGISTRY_DENIED] 无法写入 ' + RegistryRootName(RootKey) +
      '\' + Subkey + '。安装已回滚，没有保留半安装协议。');
  end;
end;

procedure RegisterProtocolAtRoot(
  RootKey: Integer; ProtocolName, DisplayName, CommandValue: String
);
var
  BaseKey, CommandKey, Verified: String;
begin
  BaseKey := ClassesPrefix + ProtocolName;
  CommandKey := BaseKey + '\shell\open\command';
  BackupRegistryKey(RootKey, BaseKey);
  WriteProtocolValue(RootKey, BaseKey, '', DisplayName);
  WriteProtocolValue(RootKey, BaseKey, 'URL Protocol', '');
  WriteProtocolValue(RootKey, BaseKey, 'PartyOps.AppId', PartyOpsAppId);
  WriteProtocolValue(RootKey, BaseKey, 'PartyOps.InstallPath', ExpandConstant('{app}'));
  WriteProtocolValue(RootKey, CommandKey, '', CommandValue);
  if (not RegQueryStringValue(RootKey, CommandKey, '', Verified)) or
     (CompareText(Verified, CommandValue) <> 0) then
  begin
    RollbackProtocolRegistry;
    RaiseException('[PROTOCOL_REGISTRY_VERIFY_FAILED] ' + ProtocolName +
      ' 写入后回读不一致。安装已回滚。');
  end;
end;

procedure RegisterPartyOpsProtocols;
var
  FileCommand, ClientCommand: String;
begin
  RegistryBackupCount := 0;
  RegistryKeyBackupCount := 0;
  FileCommand := '"' + ExpandConstant('{app}\PartyOpsFileOpen.exe') + '" "%1"';
  ClientCommand := '"' + ExpandConstant('{app}\PartyOpsWizard.exe') +
    '" --manage-shared-roots --action-uri "%1"';
  RegisterProtocolAtRoot(HKA, 'partyops-file', 'URL:PartyOps File Protocol', FileCommand);
  RegisterProtocolAtRoot(HKA, 'partyops-client', 'URL:PartyOps Client Protocol', ClientCommand);
  if RegKeyExists(HKCU, ClassesPrefix + 'partyops-file') then
    RegisterProtocolAtRoot(HKCU, 'partyops-file', 'URL:PartyOps File Protocol', FileCommand);
  if RegKeyExists(HKCU, ClassesPrefix + 'partyops-client') then
    RegisterProtocolAtRoot(HKCU, 'partyops-client', 'URL:PartyOps Client Protocol', ClientCommand);
  ProtocolRegistryWritten := True;
end;

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

function IsConfiguredHostMode: Boolean;
var
  EnvironmentLines: TArrayOfString;
  I: Integer;
  Line, Prefix, ModeValue: String;
begin
  Result := False;
  Prefix := 'PARTYOPS_MODE=';
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
      ModeValue := UnquoteEnvironmentValue(
        Copy(Line, Length(Prefix) + 1, Length(Line))
      );
      Result := CompareText(ModeValue, 'host') = 0;
      exit;
    end;
  end;
end;

procedure InitializeWizard;
var
  PreviousDataDir: String;
  PreviousDataDirLines: TArrayOfString;
begin
  InAppServiceUpdate := CompareText(
    ExpandConstant('{param:INAPPUPDATE|0}'), '1'
  ) = 0;
  WizardForm.Caption := '党建智办 PartyOps 1.4.3-rc.7 安装向导';
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
    PreviousDataDir := ExpandConstant('{commonappdata}\PartyOps-Data');
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

function ServiceIsRunning(ServiceName: String): Boolean;
var
  PowerShell, Parameters: String;
  ResultCode: Integer;
begin
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  Parameters := '-NoLogo -NoProfile -NonInteractive -Command ' +
    AddQuotes('$s=Get-Service -Name ' + AddQuotes(ServiceName) +
      ' -ErrorAction SilentlyContinue; if($s -and $s.Status -eq ''Running''){exit 0}else{exit 3}');
  Result := FileExists(PowerShell) and
    Exec(PowerShell, Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode) and
    (ResultCode = 0);
end;

function NormalizeOwnedExecutablePath(Value: String): String;
begin
  Result := Trim(Value);
  while (Length(Result) > 3) and
        ((Result[Length(Result)] = '\') or (Result[Length(Result)] = '/')) do
    Delete(Result, Length(Result), 1);
end;

function ExtractServiceExecutablePath(CommandLine: String): String;
var
  I: Integer;
begin
  Result := '';
  CommandLine := Trim(CommandLine);
  if CommandLine = '' then
    exit;
  if CommandLine[1] = '"' then
  begin
    I := 2;
    while (I <= Length(CommandLine)) and (CommandLine[I] <> '"') do
      I := I + 1;
    if I <= Length(CommandLine) then
      Result := Copy(CommandLine, 2, I - 2);
  end
  else
  begin
    I := Pos(' ', CommandLine);
    if I = 0 then
      Result := CommandLine
    else
      Result := Copy(CommandLine, 1, I - 1);
  end;
  Result := NormalizeOwnedExecutablePath(Result);
end;

function QueryOwnedServiceExecutable(
  ServiceName, ServiceExecutable: String; var ExecutablePath: String
): Boolean;
var
  ServiceKey, ImagePath, OwnerAppId, PreviousInstallLocation: String;
  ExpectedCurrent, ExpectedPrevious: String;
begin
  Result := False;
  ExecutablePath := '';
  ServiceKey := 'SYSTEM\CurrentControlSet\Services\' + ServiceName;
  if not RegQueryStringValue(HKLM, ServiceKey, 'ImagePath', ImagePath) then
    exit;
  ExecutablePath := ExtractServiceExecutablePath(ImagePath);
  if ExecutablePath = '' then
    exit;

  ExpectedCurrent := NormalizeOwnedExecutablePath(
    AddBackslash(ExpandConstant('{app}')) + ServiceExecutable
  );
  if CompareText(ExecutablePath, ExpectedCurrent) = 0 then
  begin
    Result := True;
    exit;
  end;

  { rc.7 起为服务写入不可变产品标识。后续即使旧文件损坏或安装目录变化，
    仍可在精确文件名匹配的前提下安全修复，不会接管同名第三方服务。 }
  OwnerAppId := '';
  RegQueryStringValue(HKLM, ServiceKey, 'PartyOps.AppId', OwnerAppId);
  if (CompareText(OwnerAppId, PartyOpsAppId) = 0) and
     (CompareText(ExtractFileName(ExecutablePath), ServiceExecutable) = 0) then
  begin
    Result := True;
    exit;
  end;

  { 兼容尚未写入服务标识的历史安装器：Inno 的卸载项和 SCM ImagePath
    必须同时指向同一个精确的 PartyOps 服务管理程序，缺一项都拒绝接管。 }
  PreviousInstallLocation := '';
  RegQueryStringValue(
    HKLM,
    'Software\Microsoft\Windows\CurrentVersion\Uninstall\' +
      PartyOpsAppId + '_is1',
    'InstallLocation', PreviousInstallLocation
  );
  if PreviousInstallLocation = '' then
    exit;
  ExpectedPrevious := NormalizeOwnedExecutablePath(
    AddBackslash(PreviousInstallLocation) + ServiceExecutable
  );
  Result := CompareText(ExecutablePath, ExpectedPrevious) = 0;
end;

function StopOwnedServiceThroughScm(ServiceName, DisplayName: String): String;
var
  ResultCode, RemainingChecks: Integer;
begin
  Result := '';
  if not ServiceIsRunning(ServiceName) then
    exit;
  ResultCode := -1;
  Exec(
    ExpandConstant('{sys}\sc.exe'), 'stop ' + ServiceName,
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );
  RemainingChecks := 180;
  while (RemainingChecks > 0) and ServiceIsRunning(ServiceName) do
  begin
    Sleep(250);
    RemainingChecks := RemainingChecks - 1;
  end;
  if ServiceIsRunning(ServiceName) then
    Result := '[LEGACY_SERVICE_STOP_FAILED] 无法安全停止' + DisplayName +
      '（SCM 退出码：' + IntToStr(ResultCode) +
      '）。请关闭正在使用 PartyOps 的窗口后重试。';
end;

procedure SnapshotServiceConfiguration(
  ServiceName: String; var StartType, DelayedAutoStart: Cardinal
);
var
  ServiceKey: String;
begin
  StartType := 3;
  DelayedAutoStart := 0;
  ServiceKey := 'SYSTEM\CurrentControlSet\Services\' + ServiceName;
  RegQueryDWordValue(HKLM, ServiceKey, 'Start', StartType);
  RegQueryDWordValue(HKLM, ServiceKey, 'DelayedAutoStart', DelayedAutoStart);
end;

function ServiceStartupArgument(
  Existed, ConfiguredHostMode: Boolean; StartType, DelayedAutoStart: Cardinal
): String;
begin
  if not Existed then
  begin
    if ConfiguredHostMode then
      Result := '--startup auto '
    else
      Result := '--startup manual ';
  end
  else if StartType = 4 then
    Result := '--startup disabled '
  else if (StartType = 2) and (DelayedAutoStart <> 0) then
    Result := '--startup delayed '
  else if StartType = 2 then
    Result := '--startup auto '
  else if ConfiguredHostMode then
    { rc.2 等旧安装器曾错误地把已配置主机的两项服务保留为手动。
      只修复历史手动状态；管理员明确禁用的状态仍由上方分支保留。 }
    Result := '--startup auto '
  else
    Result := '--startup manual ';
end;

procedure RestoreServiceStartup(
  ServiceName: String; Existed: Boolean; StartType, DelayedAutoStart: Cardinal
);
var
  ResultCode: Integer;
  StartValue: String;
begin
  if not Existed then
    exit;
  if StartType = 4 then StartValue := 'disabled'
  else if StartType = 2 then StartValue := 'auto'
  else StartValue := 'demand';
  Exec(ExpandConstant('{sys}\sc.exe'), 'config ' + ServiceName + ' start= ' + StartValue,
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
  if (StartType = 2) and (DelayedAutoStart <> 0) then
    RegWriteDWordValue(
      HKLM, 'SYSTEM\CurrentControlSet\Services\' + ServiceName,
      'DelayedAutoStart', DelayedAutoStart
    );
end;

function StopServiceBeforeUpgrade(
  ServiceName, ServiceExecutable, DisplayName: String
): String;
var
  ResultCode: Integer;
  ExecutablePath, RegisteredExecutable: String;
begin
  Result := '';
  if not ServiceExists(ServiceName) then
    exit;

  if not QueryOwnedServiceExecutable(
    ServiceName, ServiceExecutable, RegisteredExecutable
  ) then
  begin
    Result := '[LEGACY_SERVICE_CONFLICT] 检测到同名的“' + DisplayName +
      '”，但无法证明它属于 PartyOps。为避免修改其他软件的服务，安装已停止；' +
      '请复制安装日志给技术支持。';
    exit;
  end;

  ExecutablePath := ExpandConstant('{app}\') + ServiceExecutable;
  if not FileExists(ExecutablePath) then
  begin
    { 旧服务管理程序可能被杀毒软件隔离、被手工删除，或旧安装不完整。
      服务本身仍由 SCM 管理；核验归属后直接停止即可。新文件释放后会使用
      update/install 原位修复注册，不要求用户先卸载，也不删除业务数据。 }
    Result := StopOwnedServiceThroughScm(ServiceName, DisplayName);
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

function InstallPathValidationMessage(ResultCode: Integer): String;
begin
  case ResultCode of
    2: Result := '[INSTALL_DIR_INVALID] 程序目录必须是本机固定磁盘上的具体文件夹，不能直接使用磁盘根目录。';
    3: Result := '[INSTALL_DIR_REPARSE_POINT] 程序目录及其父目录、现有内容不能包含符号链接或目录联接。';
    4: Result := '[INSTALL_DIR_NOT_PARTYOPS] 所选程序目录不是空目录，也不是可识别的 PartyOps 旧安装目录。';
    6: Result := '[INSTALL_DIR_ACL_UNSAFE] 所选目录仍允许其他普通用户替换服务文件。管理员自己创建的 D/E 盘、中文和空格目录均受支持；请改选由您本人或管理员控制的目录。';
  else
    Result := '[INSTALL_DIR_CHECK_FAILED] 无法完成程序目录安全检查（退出码：' +
      IntToStr(ResultCode) + '）。安装日志已保留 PowerShell 的具体原因，请重试或复制日志给技术支持。';
  end;
end;

function ReadInstallPathDiagnostic(
  DiagnosticFile: String; ResultCode: Integer
): String;
var
  DiagnosticLines: TArrayOfString;
begin
  Result := '';
  if LoadStringsFromFile(DiagnosticFile, DiagnosticLines) and
     (GetArrayLength(DiagnosticLines) > 0) then
    Result := Trim(DiagnosticLines[0]);
  if (Result = '') or (Pos('[INSTALL_DIR_OK]', Result) = 1) then
    Result := InstallPathValidationMessage(ResultCode);
end;

function RunInstallPathValidator(
  PowerShell, Parameters: String; var ResultCode: Integer
): Boolean;
begin
  ResultCode := 5;
  try
    { 将 PowerShell 的 stdout/stderr 写入安装日志。即使脚本在参数绑定或
      解析阶段失败、来不及创建诊断文件，技术支持仍能看到真实错误。 }
    Result := ExecAndLogOutput(
      PowerShell,
      Parameters,
      '',
      SW_SHOWNORMAL,
      ewWaitUntilTerminated,
      ResultCode,
      nil
    );
  except
    Log('安装目录校验进程启动或日志捕获失败：' + GetExceptionMessage);
    Result := False;
  end;
end;

function ValidateAndSecureInstallDirectory: String;
var
  AppDir, Validator, PowerShell, Parameters, DiagnosticFile: String;
  ResultCode: Integer;
begin
  Result := '';
  AppDir := ExpandConstant('{app}');
  ExtractTemporaryFile('validate-install-path.ps1');
  Validator := ExpandConstant('{tmp}\validate-install-path.ps1');
  PowerShell := ExpandConstant('{sys}\WindowsPowerShell\v1.0\powershell.exe');
  DiagnosticFile := ExpandConstant('{tmp}\partyops-install-path-diagnostic.txt');
  DeleteFile(DiagnosticFile);
  Parameters := '-NoLogo -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ' +
    AddQuotes(Validator) + ' -Path ' + AddQuotes(AppDir) +
    ' -DiagnosticFile ' + AddQuotes(DiagnosticFile);
  ResultCode := 5;
  if not FileExists(PowerShell) then
  begin
    Result := '[INSTALL_DIR_CHECK_FAILED] 系统缺少 Windows PowerShell，无法安全配置自定义程序目录。';
    exit;
  end;
  if (not RunInstallPathValidator(PowerShell, Parameters, ResultCode)) or
     (ResultCode <> 0) then
  begin
    Result := ReadInstallPathDiagnostic(DiagnosticFile, ResultCode);
    exit;
  end;
  if not ForceDirectories(AppDir) then
  begin
    Result := '[INSTALL_DIR_CREATE_FAILED] 无法创建所选程序目录，请检查磁盘和权限。';
    exit;
  end;
  { 新建目录后再次检查，收窄普通用户在首次检查与目录创建之间替换路径的窗口。 }
  if (not RunInstallPathValidator(PowerShell, Parameters, ResultCode)) or
     (ResultCode <> 0) then
  begin
    Result := ReadInstallPathDiagnostic(DiagnosticFile, ResultCode);
    exit;
  end;
  if (not Exec(
    ExpandConstant('{sys}\icacls.exe'),
    AddQuotes(AppDir) + ' /setowner *S-1-5-32-544 /T /C /Q',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  )) or (ResultCode <> 0) then
  begin
    Result := '[INSTALL_DIR_ACL_DENIED] 无法把程序目录所有权交给管理员，安装已停止。';
    exit;
  end;
  if (not Exec(
    ExpandConstant('{sys}\icacls.exe'),
    AddQuotes(AppDir) + ' /inheritance:r /grant:r ' +
      '*S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F ' +
      '*S-1-5-32-545:(OI)(CI)RX',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  )) or (ResultCode <> 0) then
  begin
    Result := '[INSTALL_DIR_ACL_DENIED] 无法保护程序目录写权限，安装已停止。';
    exit;
  end;
  { (OI)(CI) 是目录继承标记，不能用 /T 直接递归写到普通文件；否则文件会
    变成受保护但没有有效访问 ACE，连管理员也无法读取或执行。根目录先固定
    ACL，再把已存在的载荷重置为继承根目录权限。空的新目录无需执行此步骤。 }
  if FileExists(AddBackslash(AppDir) + 'PartyOps.exe') then
  begin
    if (not Exec(
      ExpandConstant('{sys}\icacls.exe'),
      AddQuotes(AddBackslash(AppDir) + '*') + ' /reset /T /C /Q',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode
    )) or (ResultCode <> 0) then
    begin
      Result := '[INSTALL_DIR_TREE_ACL_DENIED] 无法让程序文件安全继承目录权限，安装已停止。';
      exit;
    end;
    if FileExists(AddBackslash(AppDir) + 'PartyOpsService.exe') and
       (GetSHA256OfFile(AddBackslash(AppDir) + 'PartyOpsService.exe') = '') then
    begin
      Result := '[INSTALL_DIR_TREE_ACL_VERIFY_FAILED] 程序文件权限回读失败，安装已停止。';
      exit;
    end;
  end;
  { 自定义磁盘的上级目录可能保留面向日常文件的宽松 DACL。最终程序目录除
    收敛 DACL 外再设置高完整性标签：普通桌面进程仍可读取/执行，但不能通过
    父目录的通用写权限替换由管理员和 SYSTEM 使用的程序文件。/T 同时覆盖
    本次刚释放的全部载荷，避免只保护根目录。 }
  if (not Exec(
    ExpandConstant('{sys}\icacls.exe'),
    AddQuotes(AppDir) + ' /setintegritylevel (OI)(CI)H /T /C /Q',
    '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  )) or (ResultCode <> 0) then
  begin
    Result := '[INSTALL_DIR_INTEGRITY_DENIED] 无法保护自定义程序目录的完整性级别，安装已停止。';
    exit;
  end;
  { ACL 收敛后最后回查；普通用户仍可读取并执行，但不能替换服务二进制。 }
  if (not RunInstallPathValidator(
    PowerShell,
    Parameters + ' -VerifyTargetAcl',
    ResultCode
  )) or
     (ResultCode <> 0) then
    Result := ReadInstallPathDiagnostic(DiagnosticFile, ResultCode);
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  RegistryError: String;
begin
#ifdef PartyOpsLegacy
  if not ValidateWindows7Prerequisites(RegistryError) then
  begin
    Result := RegistryError;
    exit;
  end;
#endif
  WizardForm.StatusLabel.Caption := '正在检查并保护自定义程序目录…';
  Result := ValidateAndSecureInstallDirectory;
  if Result <> '' then
    exit;
  HostServiceExistedBeforeInstall := ServiceExists('PartyOpsHost');
  UpdateServiceExistedBeforeInstall := ServiceExists('PartyOpsUpdateService');
  ConfiguredHostModeBeforeInstall := IsConfiguredHostMode;
  HostServiceRunningBeforeInstall :=
    HostServiceExistedBeforeInstall and ServiceIsRunning('PartyOpsHost');
  UpdateServiceRunningBeforeInstall :=
    UpdateServiceExistedBeforeInstall and ServiceIsRunning('PartyOpsUpdateService');
  SnapshotServiceConfiguration(
    'PartyOpsHost', HostServiceStartTypeBeforeInstall, HostServiceDelayedBeforeInstall
  );
  SnapshotServiceConfiguration(
    'PartyOpsUpdateService', UpdateServiceStartTypeBeforeInstall,
    UpdateServiceDelayedBeforeInstall
  );
  WizardForm.StatusLabel.Caption := '正在安全停止旧版 PartyOps 服务…';
  if not PreflightProtocolRegistry(RegistryError) then
  begin
    Result := RegistryError;
    exit;
  end;
  RestartPreviousServicesOnExit :=
    HostServiceExistedBeforeInstall or UpdateServiceExistedBeforeInstall;
  Result := StopServiceBeforeUpgrade(
    'PartyOpsHost', 'PartyOpsService.exe', 'PartyOps 主机服务'
  );
  if Result <> '' then
    exit;
  if not InAppServiceUpdate then
    Result := StopServiceBeforeUpgrade(
      'PartyOpsUpdateService', 'PartyOpsUpdaterService.exe', 'PartyOps 更新服务'
    );
end;

procedure RemoveOwnedProtocol(RootKey: Integer; ProtocolName: String);
var
  BaseKey, AppIdValue, InstallPathValue: String;
begin
  BaseKey := ClassesPrefix + ProtocolName;
  if not RegKeyExists(RootKey, BaseKey) then
    exit;
  AppIdValue := '';
  InstallPathValue := '';
  RegQueryStringValue(RootKey, BaseKey, 'PartyOps.AppId', AppIdValue);
  RegQueryStringValue(RootKey, BaseKey, 'PartyOps.InstallPath', InstallPathValue);
  { 只清理由当前 AppId 且当前安装路径共同证明归属的键，避免删除外部同名协议。 }
  if (CompareText(AppIdValue, PartyOpsAppId) = 0) and
     (CompareText(InstallPathValue, ExpandConstant('{app}')) = 0) then
    RegDeleteKeyIncludingSubkeys(RootKey, BaseKey);
end;

function RunDataCleanup(Scope: String; CheckOnly: Boolean): Boolean;
var
  Parameters: String;
  ResultCode: Integer;
begin
  Parameters := '--scope ' + Scope;
  if CheckOnly then
    Parameters := Parameters + ' --check';
  ResultCode := -1;
  { ExecAsOriginalUser 官方不支持卸载阶段。清理器以提升后的卸载令牌运行，
    并自行枚举已登记配置目录与已加载用户启动项，同时逐项核验归属。 }
  Result := Exec(
    ExpandConstant('{app}\PartyOpsDataCleanup.exe'),
    Parameters, '', SW_HIDE, ewWaitUntilTerminated, ResultCode
  );
  Result := Result and (ResultCode = 0);
end;

function UninstallDataActionParameter: String;
var
  I: Integer;
  Argument, Prefix: String;
begin
  Result := '';
  Prefix := '/DATAACTION=';
  for I := 1 to ParamCount do
  begin
    Argument := ParamStr(I);
    if CompareText(Copy(Argument, 1, Length(Prefix)), Prefix) = 0 then
    begin
      Result := Lowercase(Trim(Copy(Argument, Length(Prefix) + 1, Length(Argument))));
      exit;
    end;
  end;
end;

function InitializeUninstall(): Boolean;
var
  Choice: Integer;
  DataAction: String;
begin
  DeleteAllDataOnUninstall := False;
  DataAction := UninstallDataActionParameter;
  if (DataAction <> '') and (DataAction <> 'preserve') and
     (DataAction <> 'delete') then
  begin
    MsgBox(
      '[UNINSTALL_DATAACTION_INVALID] /DATAACTION 只能使用 preserve 或 delete。',
      mbError, MB_OK
    );
    Result := False;
    exit;
  end;
  { 静默卸载不能让被抑制的 Yes/No 对话框默认选择不可恢复的数据删除。
    默认只删除程序；自动化只有显式 DATAACTION=delete 才能请求彻底清理。 }
  if UninstallSilent or (DataAction <> '') then
  begin
    if DataAction = 'delete' then
      Choice := IDYES
    else
      Choice := IDNO;
  end
  else
    Choice := MsgBox(
      '请选择卸载方式：' + #13#10 + #13#10 +
      '“是”＝彻底卸载：删除程序、服务、当前账号配置以及经 PartyOps 标记的数据库、附件、备份、证书、模型、缓存和日志。此操作不可恢复，请先确认备份可用。' + #13#10 + #13#10 +
      '“否”＝仅删除程序：保留全部业务数据，方便以后重装继续使用。' + #13#10 + #13#10 +
      '“取消”＝暂不卸载。',
      mbConfirmation, MB_YESNOCANCEL
    );
  if Choice = IDCANCEL then
  begin
    Result := False;
    exit;
  end;
  DeleteAllDataOnUninstall := Choice = IDYES;
  if DeleteAllDataOnUninstall then
  begin
    if not RunDataCleanup('all', True) then
    begin
      MsgBox(
        '[UNINSTALL_DATA_PREFLIGHT_FAILED] 本机 PartyOps 数据目录未通过完整安全检查。为防止误删，卸载尚未开始；可改选“仅删除程序”保留数据。',
        mbError, MB_OK
      );
      Result := False;
      exit;
    end;
  end;
  Result := True;
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep <> usUninstall then
    exit;
  if not RunDataCleanup('runtime', False) then
    RaiseException('[UNINSTALL_RUNTIME_CLEANUP_FAILED] PartyOps 用户进程或自启动项未能安全清理，卸载已停止。');
  if DeleteAllDataOnUninstall then
  begin
    if not RunDataCleanup('all', False) then
      RaiseException('[UNINSTALL_DATA_FAILED] 本机 PartyOps 数据未能按唯一清单安全删除，卸载已停止。');
  end;
  RemoveOwnedProtocol(HKA, 'partyops-file');
  RemoveOwnedProtocol(HKA, 'partyops-client');
  RemoveOwnedProtocol(HKCU, 'partyops-file');
  RemoveOwnedProtocol(HKCU, 'partyops-client');
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

procedure RollbackInstallerCache;
begin
  if not InstallerCacheTransactionActive then
    exit;
  DeleteFile(InstallerCachePath);
  DeleteFile(InstallerCacheHashPath);
  if InstallerCacheHadPrevious and FileExists(InstallerCachePreviousPath) then
    RenameFile(InstallerCachePreviousPath, InstallerCachePath)
  else
    DeleteFile(InstallerCachePreviousPath);
  if InstallerCacheHashHadPrevious and FileExists(InstallerCacheHashPreviousPath) then
    RenameFile(InstallerCacheHashPreviousPath, InstallerCacheHashPath)
  else
    DeleteFile(InstallerCacheHashPreviousPath);
  DeleteFile(InstallerCacheIncomingPath);
  DeleteFile(InstallerCacheHashIncomingPath);
  InstallerCacheTransactionActive := False;
end;

procedure BeginInstallerCacheTransaction;
var
  SystemRoot, CacheDirectory, SourceInstaller, SourceHash, IncomingHash: String;
begin
  { 回滚安装器会被 SYSTEM 执行，必须与普通用户可写的自定义业务数据隔离。 }
  SystemRoot := ExpandConstant('{commonappdata}\PartyOps-System');
  CacheDirectory := AddBackslash(SystemRoot) + 'installer-cache';
  if not ForceDirectories(CacheDirectory) then
    RaiseException('[INSTALLER_CACHE_DENIED] 无法创建升级回滚缓存目录：' + CacheDirectory);
  RunChecked(
    ExpandConstant('{sys}\icacls.exe'),
    '"' + SystemRoot + '" /inheritance:r /grant:r ' +
      '*S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F',
    '保护 PartyOps 系统事务目录权限'
  );
  RunChecked(
    ExpandConstant('{sys}\icacls.exe'),
    '"' + CacheDirectory + '" /inheritance:r /grant:r ' +
      '*S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F',
    '保护安装器回滚缓存权限'
  );
  InstallerCachePath := AddBackslash(CacheDirectory) + 'current.exe';
  InstallerCachePreviousPath := InstallerCachePath + '.previous';
  InstallerCacheIncomingPath := InstallerCachePath + '.incoming';
  InstallerCacheHashPath := InstallerCachePath + '.sha256';
  InstallerCacheHashPreviousPath := InstallerCacheHashPath + '.previous';
  InstallerCacheHashIncomingPath := InstallerCacheHashPath + '.incoming';
  SourceInstaller := ExpandConstant('{srcexe}');
  DeleteFile(InstallerCacheIncomingPath);
  DeleteFile(InstallerCacheHashIncomingPath);
  { 如果上次被外部强制中断，优先保留 current；只有 current 缺失时才
    恢复 previous，避免把一个已经提交的新缓存意外降级。 }
  if (not FileExists(InstallerCachePath)) and FileExists(InstallerCachePreviousPath) then
    RenameFile(InstallerCachePreviousPath, InstallerCachePath)
  else
    DeleteFile(InstallerCachePreviousPath);
  if (not FileExists(InstallerCacheHashPath)) and FileExists(InstallerCacheHashPreviousPath) then
    RenameFile(InstallerCacheHashPreviousPath, InstallerCacheHashPath)
  else
    DeleteFile(InstallerCacheHashPreviousPath);
  if not CopyFile(SourceInstaller, InstallerCacheIncomingPath, False) then
    RaiseException('[INSTALLER_CACHE_COPY_FAILED] 无法保存当前安装器，已拒绝不可回滚安装。');
  SourceHash := GetSHA256OfFile(SourceInstaller);
  IncomingHash := GetSHA256OfFile(InstallerCacheIncomingPath);
  if (SourceHash = '') or (CompareText(SourceHash, IncomingHash) <> 0) then
  begin
    DeleteFile(InstallerCacheIncomingPath);
    RaiseException('[INSTALLER_CACHE_VERIFY_FAILED] 安装器回滚缓存校验失败，安装已停止。');
  end;
  if not SaveStringToFile(InstallerCacheHashIncomingPath, Lowercase(SourceHash), False) then
  begin
    DeleteFile(InstallerCacheIncomingPath);
    RaiseException('[INSTALLER_CACHE_VERIFY_FAILED] 无法保存安装器校验值，安装已停止。');
  end;
  InstallerCacheHadPrevious := FileExists(InstallerCachePath);
  InstallerCacheHashHadPrevious := FileExists(InstallerCacheHashPath);
  if InstallerCacheHadPrevious and
     (not RenameFile(InstallerCachePath, InstallerCachePreviousPath)) then
  begin
    DeleteFile(InstallerCacheIncomingPath);
    DeleteFile(InstallerCacheHashIncomingPath);
    RaiseException('[INSTALLER_CACHE_SWITCH_FAILED] 无法保护旧版安装器缓存，安装已停止。');
  end;
  if InstallerCacheHashHadPrevious and
     (not RenameFile(InstallerCacheHashPath, InstallerCacheHashPreviousPath)) then
  begin
    if InstallerCacheHadPrevious then
      RenameFile(InstallerCachePreviousPath, InstallerCachePath);
    DeleteFile(InstallerCacheIncomingPath);
    DeleteFile(InstallerCacheHashIncomingPath);
    RaiseException('[INSTALLER_CACHE_SWITCH_FAILED] 无法保护旧版安装器校验值，安装已停止。');
  end;
  if not RenameFile(InstallerCacheIncomingPath, InstallerCachePath) then
  begin
    if InstallerCacheHadPrevious then
      RenameFile(InstallerCachePreviousPath, InstallerCachePath);
    if InstallerCacheHashHadPrevious then
      RenameFile(InstallerCacheHashPreviousPath, InstallerCacheHashPath);
    DeleteFile(InstallerCacheIncomingPath);
    DeleteFile(InstallerCacheHashIncomingPath);
    RaiseException('[INSTALLER_CACHE_SWITCH_FAILED] 无法原子切换安装器缓存，安装已停止。');
  end;
  if not RenameFile(InstallerCacheHashIncomingPath, InstallerCacheHashPath) then
  begin
    DeleteFile(InstallerCachePath);
    if InstallerCacheHadPrevious then
      RenameFile(InstallerCachePreviousPath, InstallerCachePath);
    if InstallerCacheHashHadPrevious then
      RenameFile(InstallerCacheHashPreviousPath, InstallerCacheHashPath);
    DeleteFile(InstallerCacheHashIncomingPath);
    RaiseException('[INSTALLER_CACHE_SWITCH_FAILED] 无法原子切换安装器校验值，安装已停止。');
  end;
  InstallerCacheTransactionActive := True;
end;

procedure RollbackDataMarker;
begin
  if not DataMarkerTransactionActive then
    exit;
  DeleteFile(DataMarkerPath);
  if DataMarkerHadPrevious and FileExists(DataMarkerPreviousPath) then
    RenameFile(DataMarkerPreviousPath, DataMarkerPath)
  else
    DeleteFile(DataMarkerPreviousPath);
  DataMarkerTransactionActive := False;
end;

procedure BeginDataMarkerTransaction;
var
  SelectedDataDir: TArrayOfString;
begin
  if not ForceDirectories(ExpandConstant('{commonappdata}\PartyOps')) then
    RaiseException('[DATA_MARKER_DENIED] 无法创建 PartyOps 系统引导目录。');
  DataMarkerPath := ExpandConstant('{commonappdata}\PartyOps\install-data-dir.txt');
  DataMarkerPreviousPath := DataMarkerPath + '.previous';
  DeleteFile(DataMarkerPreviousPath);
  DataMarkerHadPrevious := FileExists(DataMarkerPath);
  if DataMarkerHadPrevious and
     (not RenameFile(DataMarkerPath, DataMarkerPreviousPath)) then
    RaiseException('[DATA_MARKER_BACKUP_FAILED] 无法保护原数据目录配置，安装已停止。');
  SetArrayLength(SelectedDataDir, 1);
  SelectedDataDir[0] := DataDirPage.Values[0];
  if not SaveStringsToUTF8File(DataMarkerPath, SelectedDataDir, False) then
  begin
    if DataMarkerHadPrevious then
      RenameFile(DataMarkerPreviousPath, DataMarkerPath);
    RaiseException('[DATA_MARKER_WRITE_FAILED] 保存主机数据目录预选项失败。');
  end;
  DataMarkerTransactionActive := True;
end;

procedure CommitPostInstallTransactions;
begin
  DeleteFile(InstallerCachePreviousPath);
  DeleteFile(InstallerCacheIncomingPath);
  DeleteFile(InstallerCacheHashPreviousPath);
  DeleteFile(InstallerCacheHashIncomingPath);
  DeleteFile(DataMarkerPreviousPath);
  InstallerCacheTransactionActive := False;
  DataMarkerTransactionActive := False;
end;

procedure RollbackPostInstall;
var
  ResultCode: Integer;
  HostExecutable, UpdateExecutable: String;
begin
  RollbackDataMarker;
  RollbackInstallerCache;
  if ProtocolRegistryWritten then
    RollbackProtocolRegistry;
  HostExecutable := ExpandConstant('{app}\PartyOpsService.exe');
  UpdateExecutable := ExpandConstant('{app}\PartyOpsUpdaterService.exe');
  if FileExists(UpdateExecutable) then
  begin
    if not UpdateServiceExistedBeforeInstall then
    begin
      Exec(UpdateExecutable, '--wait=15 stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Exec(UpdateExecutable, 'remove', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
  if FileExists(HostExecutable) then
  begin
    if not HostServiceExistedBeforeInstall then
    begin
      Exec(HostExecutable, '--wait=15 stop', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
      Exec(HostExecutable, 'remove', '', SW_HIDE, ewWaitUntilTerminated, ResultCode);
    end;
  end;
  RestoreServiceStartup(
    'PartyOpsHost', HostServiceExistedBeforeInstall,
    HostServiceStartTypeBeforeInstall, HostServiceDelayedBeforeInstall
  );
  RestoreServiceStartup(
    'PartyOpsUpdateService', UpdateServiceExistedBeforeInstall,
    UpdateServiceStartTypeBeforeInstall, UpdateServiceDelayedBeforeInstall
  );
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

procedure ProtectSystemControlDirectories;
var
  ControlRoot, TransactionRoot: String;
begin
  ControlRoot := ExpandConstant('{commonappdata}\PartyOps');
  TransactionRoot := ExpandConstant('{commonappdata}\PartyOps-System');
  if not ForceDirectories(ControlRoot) then
    RaiseException('[CONTROL_DIR_CREATE_FAILED] 无法创建 PartyOps 系统控制目录');
  if not ForceDirectories(TransactionRoot) then
    RaiseException('[TRANSACTION_DIR_CREATE_FAILED] 无法创建 PartyOps 系统事务目录');
  RunChecked(
    ExpandConstant('{sys}\icacls.exe'),
    '"' + ControlRoot + '" /setowner *S-1-5-32-544 /T /C /Q',
    '保护 PartyOps 系统控制目录所有权'
  );
  RunChecked(
    ExpandConstant('{sys}\icacls.exe'),
    '"' + ControlRoot + '" /inheritance:r /grant:r ' +
      '*S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F ' +
      '*S-1-5-32-545:(OI)(CI)RX',
    '保护 PartyOps 系统控制目录写权限'
  );
  RunChecked(
    ExpandConstant('{sys}\icacls.exe'),
    AddQuotes(AddBackslash(ControlRoot) + '*') + ' /reset /T /C /Q',
    '规范 PartyOps 系统控制目录子项权限'
  );
  RunChecked(
    ExpandConstant('{sys}\icacls.exe'),
    '"' + TransactionRoot + '" /setowner *S-1-5-32-544 /T /C /Q',
    '保护 PartyOps 系统事务目录所有权'
  );
  RunChecked(
    ExpandConstant('{sys}\icacls.exe'),
    '"' + TransactionRoot + '" /inheritance:r /grant:r ' +
      '*S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F',
    '保护 PartyOps 系统事务目录写权限'
  );
  RunChecked(
    ExpandConstant('{sys}\icacls.exe'),
    AddQuotes(AddBackslash(TransactionRoot) + '*') + ' /reset /T /C /Q',
    '规范 PartyOps 系统事务目录子项权限'
  );
end;

procedure MarkServiceOwnership(ServiceName, ServiceExecutable: String);
var
  ServiceKey, Verified: String;
begin
  ServiceKey := 'SYSTEM\CurrentControlSet\Services\' + ServiceName;
  if (not RegWriteStringValue(HKLM, ServiceKey, 'PartyOps.AppId', PartyOpsAppId)) or
     (not RegWriteStringValue(
       HKLM, ServiceKey, 'PartyOps.Executable', ServiceExecutable
     )) then
    RaiseException('[SERVICE_OWNERSHIP_MARK_FAILED] 无法写入 PartyOps 服务归属标识。');
  Verified := '';
  if (not RegQueryStringValue(HKLM, ServiceKey, 'PartyOps.AppId', Verified)) or
     (CompareText(Verified, PartyOpsAppId) <> 0) then
    RaiseException('[SERVICE_OWNERSHIP_VERIFY_FAILED] PartyOps 服务归属标识回读失败。');
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  ResultCode: Integer;
  ErrorMessage, HostServiceStartup, UpdateServiceStartup: String;
begin
  if CurStep = ssDone then
  begin
    CommitPostInstallTransactions;
    InstallCompletedSuccessfully := True;
    RestartPreviousServicesOnExit := False;
    exit;
  end;
  if CurStep <> ssPostInstall then
    exit;
  try
    { 文件释放后再次检查重解析点与最终 ACL，再注册 LocalSystem 服务。 }
    WizardForm.StatusLabel.Caption := '正在复核自定义程序目录与安装文件…';
    ErrorMessage := ValidateAndSecureInstallDirectory;
    if ErrorMessage <> '' then
      RaiseException(ErrorMessage);
    ProtectSystemControlDirectories;
    RegisterPartyOpsProtocols;
    HostServiceStartup := ServiceStartupArgument(
      HostServiceExistedBeforeInstall,
      ConfiguredHostModeBeforeInstall,
      HostServiceStartTypeBeforeInstall,
      HostServiceDelayedBeforeInstall
    );
    UpdateServiceStartup := ServiceStartupArgument(
      UpdateServiceExistedBeforeInstall,
      ConfiguredHostModeBeforeInstall,
      UpdateServiceStartTypeBeforeInstall,
      UpdateServiceDelayedBeforeInstall
    );
    RunChecked(
      ExpandConstant('{app}\PartyOpsService.exe'),
      HostServiceStartup + ServiceInstallAction('PartyOpsHost'),
      '安装 PartyOps 主机服务'
    );
    MarkServiceOwnership('PartyOpsHost', 'PartyOpsService.exe');
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
      UpdateServiceStartup + ServiceInstallAction('PartyOpsUpdateService'),
      '安装 PartyOps 更新服务'
    );
    MarkServiceOwnership('PartyOpsUpdateService', 'PartyOpsUpdaterService.exe');
    RunChecked(
      ExpandConstant('{sys}\sc.exe'),
      'failure PartyOpsUpdateService reset= 86400 actions= restart/5000/restart/15000/',
      '配置 PartyOps 更新服务恢复策略'
    );
    { 当前安装器和数据目录标记都采用同目录临时文件 + 旧值保留。
      只有 Inno 进入 ssDone 才提交，后续任一步失败都会恢复原值。 }
    BeginInstallerCacheTransaction;
    BeginDataMarkerTransaction;
    Exec(
      ExpandConstant('{sys}\netsh.exe'),
      'advfirewall firewall delete rule name="党建智办主机"',
      '', SW_HIDE, ewWaitUntilTerminated, ResultCode
    );
    if HostServiceRunningBeforeInstall or
       (ConfiguredHostModeBeforeInstall and not HostServiceExistedBeforeInstall) then
      RunChecked(
        ExpandConstant('{sys}\sc.exe'), 'start PartyOpsHost',
        '恢复升级前运行的 PartyOps 主机服务'
      );
    if (UpdateServiceRunningBeforeInstall or
        (ConfiguredHostModeBeforeInstall and not UpdateServiceExistedBeforeInstall)) and
       not InAppServiceUpdate then
      RunChecked(
        ExpandConstant('{sys}\sc.exe'), 'start PartyOpsUpdateService',
        '恢复升级前运行的 PartyOps 更新服务'
      );
  except
    ErrorMessage := GetExceptionMessage;
    RollbackPostInstall;
    RaiseException(ErrorMessage);
  end;
end;

procedure DeinitializeSetup;
var
  ResultCode: Integer;
begin
  { 只有在 Inno 完成自身文件回滚后才恢复升级前已有服务，避免服务过早
    启动并锁住正在还原的旧二进制。新安装创建的服务已在异常处理中删除。 }
  if RestartPreviousServicesOnExit and not InstallCompletedSuccessfully then
  begin
    RollbackDataMarker;
    RollbackInstallerCache;
    RestoreServiceStartup(
      'PartyOpsHost', HostServiceExistedBeforeInstall,
      HostServiceStartTypeBeforeInstall, HostServiceDelayedBeforeInstall
    );
    RestoreServiceStartup(
      'PartyOpsUpdateService', UpdateServiceExistedBeforeInstall,
      UpdateServiceStartTypeBeforeInstall, UpdateServiceDelayedBeforeInstall
    );
    if HostServiceRunningBeforeInstall then
      Exec(ExpandConstant('{sys}\sc.exe'), 'start PartyOpsHost', '', SW_HIDE,
        ewWaitUntilTerminated, ResultCode);
    if UpdateServiceRunningBeforeInstall then
      Exec(ExpandConstant('{sys}\sc.exe'), 'start PartyOpsUpdateService', '', SW_HIDE,
        ewWaitUntilTerminated, ResultCode);
  end;
end;
