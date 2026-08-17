param(
  [Parameter(Mandatory = $true)]
  [string]$Path,
  [switch]$VerifyTargetAcl,
  [string]$DiagnosticFile = ""
)

$ErrorActionPreference = "Stop"

function Stop-InstallPathValidation {
  param([string]$Code, [string]$Message, [int]$ExitCode)
  $diagnostic = "[$Code] $Message"
  if (-not [string]::IsNullOrWhiteSpace($DiagnosticFile)) {
    try {
      [IO.File]::WriteAllText(
        $DiagnosticFile,
        $diagnostic,
        (New-Object Text.UTF8Encoding($false))
      )
    } catch {
      # 诊断文件只是给 Inno 展示详情；写入失败不能覆盖真正的退出码。
    }
  }
  [Console]::Error.WriteLine($diagnostic)
  exit $ExitCode
}

function Assert-SecureDirectoryAcl {
  param(
    [string]$DirectoryPath,
    [switch]$ForProgramDirectory
  )

  $acl = [IO.Directory]::GetAccessControl(
    $DirectoryPath,
    [Security.AccessControl.AccessControlSections]::Access -bor
      [Security.AccessControl.AccessControlSections]::Owner
  )
  $trustedSids = @(
    'S-1-5-18',       # LocalSystem
    'S-1-5-32-544',   # BUILTIN\Administrators
    'S-1-3-4',        # OWNER RIGHTS；目录所有者已在下方单独校验
    'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464' # TrustedInstaller
  )
  # 安装器已经由 UAC 提升。当前管理员的显式 SID 与 BUILTIN\Administrators
  # 具有相同信任级别；旧实现把该 SID 当作“普通用户”，导致管理员自己创建的
  # D/E 盘目录被稳定误判为 PARENT_UNSAFE。
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  if ($principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    $trustedSids += $identity.User.Value
  }
  $owner = $acl.GetOwner([Security.Principal.SecurityIdentifier]).Value
  if ($trustedSids -notcontains $owner) {
    Stop-InstallPathValidation "INSTALL_DIR_EXISTING_ACL_UNSAFE" "PartyOps 程序目录所有者不是当前管理员、SYSTEM、管理员组或 TrustedInstaller：$DirectoryPath" 6
  }
  $deleteChild = [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles
  $deleteObject = [Security.AccessControl.FileSystemRights]::Delete
  $writeContent =
    [Security.AccessControl.FileSystemRights]::WriteData -bor
    [Security.AccessControl.FileSystemRights]::AppendData -bor
    [Security.AccessControl.FileSystemRights]::WriteExtendedAttributes -bor
    [Security.AccessControl.FileSystemRights]::WriteAttributes -bor
    [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
    [Security.AccessControl.FileSystemRights]::TakeOwnership
  $rules = $acl.GetAccessRules(
    $true,
    $true,
    [Security.Principal.SecurityIdentifier]
  )
  foreach ($rule in $rules) {
    if ($rule.AccessControlType -ne [Security.AccessControl.AccessControlType]::Allow) {
      continue
    }
    $sid = $rule.IdentityReference.Value
    if ($trustedSids -contains $sid) { continue }
    $canDeleteChild = ($rule.FileSystemRights -band $deleteChild) -ne 0
    $canDeleteObject = ($rule.FileSystemRights -band $deleteObject) -ne 0
    $canChangeProgramContent =
      $ForProgramDirectory -and
      (($rule.FileSystemRights -band $writeContent) -ne 0)
    $isVolumeRoot = $DirectoryPath.TrimEnd('\') -eq
      [IO.Path]::GetPathRoot($DirectoryPath).TrimEnd('\')
    if ($canDeleteChild -or
        (-not $isVolumeRoot -and $canDeleteObject) -or
        $canChangeProgramContent) {
      if ($ForProgramDirectory) {
        Stop-InstallPathValidation "INSTALL_DIR_EXISTING_ACL_UNSAFE" "现有 PartyOps 程序目录允许其他普通用户修改内容：$DirectoryPath（主体 $sid）。请选择空目录，或先恢复该目录的管理员权限。" 6
      }
      Stop-InstallPathValidation "INSTALL_DIR_PARENT_ACL_UNSAFE" "父目录允许其他普通用户替换服务目录：$DirectoryPath（主体 $sid）。当前管理员自己的自定义目录不会被此规则拦截；请改选由您本人或管理员控制的目录。" 6
    }
  }
}

try {
  $fullPath = [IO.Path]::GetFullPath($Path)
  $root = [IO.Path]::GetPathRoot($fullPath)
  if ([string]::IsNullOrEmpty($root)) {
    Stop-InstallPathValidation "INSTALL_DIR_INVALID" "程序目录必须使用绝对路径。" 2
  }
  if ($fullPath.TrimEnd('\') -eq $root.TrimEnd('\')) {
    Stop-InstallPathValidation "INSTALL_DIR_ROOT_DENIED" "程序目录不能直接使用磁盘根目录。" 2
  }
  $drive = New-Object IO.DriveInfo($root)
  if ($drive.DriveType -ne [IO.DriveType]::Fixed) {
    Stop-InstallPathValidation "INSTALL_DIR_NOT_FIXED" "程序目录必须位于本机固定磁盘。" 2
  }

  # 从目标向上检查全部已存在父目录，避免解析路径后把目录联接隐藏掉。
  $cursor = $fullPath
  while (-not [string]::IsNullOrEmpty($cursor)) {
    if ([IO.Directory]::Exists($cursor) -or [IO.File]::Exists($cursor)) {
      $attributes = [IO.File]::GetAttributes($cursor)
      if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
        Stop-InstallPathValidation "INSTALL_DIR_REPARSE_POINT" "程序目录及其父目录不能是符号链接或目录联接：$cursor" 3
      }
    }
    $parent = [IO.Directory]::GetParent($cursor)
    if ($null -eq $parent -or $parent.FullName -eq $cursor) { break }
    $cursor = $parent.FullName
  }

  # 当前管理员 SID 属于受信主体，因此管理员自己在 D/E 盘创建的中文、空格目录
  # 可以正常通过。仍须逐级阻断真正向 Everyone/Users 开放“删除子项”的父目录：
  # LocalSystem 服务若从此路径启动，攻击者可在重启前把整个目录替换为恶意程序。
  $aclCursor = if ($VerifyTargetAcl -and [IO.Directory]::Exists($fullPath)) {
    $fullPath
  } else {
    $parent = [IO.Directory]::GetParent($fullPath)
    if ($null -eq $parent) { $root } else { $parent.FullName }
  }
  while (-not [string]::IsNullOrEmpty($aclCursor)) {
    if ([IO.Directory]::Exists($aclCursor)) {
      if ($VerifyTargetAcl -and
          $aclCursor.TrimEnd('\') -eq $fullPath.TrimEnd('\')) {
        Assert-SecureDirectoryAcl $aclCursor -ForProgramDirectory
      } else {
        Assert-SecureDirectoryAcl $aclCursor
      }
    }
    $aclParent = [IO.Directory]::GetParent($aclCursor)
    if ($null -eq $aclParent -or $aclParent.FullName -eq $aclCursor) { break }
    $aclCursor = $aclParent.FullName
  }

  if ([IO.Directory]::Exists($fullPath)) {
    $topEntries = [IO.Directory]::GetFileSystemEntries($fullPath)
    if ($topEntries.Length -gt 0 -and
        -not [IO.File]::Exists((Join-Path $fullPath "PartyOps.exe"))) {
      Stop-InstallPathValidation "INSTALL_DIR_NOT_PARTYOPS" "所选程序目录不是空目录，也不是可识别的 PartyOps 旧安装目录。" 4
    }
    # 旧安装目录中的未知文件不会被 Inno 自动清理。若该目录此前允许普通用户
    # 写入，攻击者可提前放置 DLL 并在升级后随 LocalSystem 服务加载；因此必须
    # 在修改 ACL 或覆盖任何文件之前拒绝这种升级。空的新目录随后由安装器收敛 ACL。
    if ($topEntries.Length -gt 0) {
      Assert-SecureDirectoryAcl $fullPath -ForProgramDirectory
    }
    $pending = New-Object 'Collections.Generic.Stack[string]'
    $pending.Push($fullPath)
    while ($pending.Count -gt 0) {
      $current = $pending.Pop()
      foreach ($entry in [IO.Directory]::GetFileSystemEntries($current)) {
        $attributes = [IO.File]::GetAttributes($entry)
        if (($attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
          Stop-InstallPathValidation "INSTALL_DIR_REPARSE_POINT" "程序目录包含符号链接或目录联接：$entry" 3
        }
        if (($attributes -band [IO.FileAttributes]::Directory) -ne 0) {
          $pending.Push($entry)
        }
      }
    }
  }
}
catch {
  Stop-InstallPathValidation "INSTALL_DIR_CHECK_FAILED" "程序目录安全检查失败：$($_.Exception.Message)" 5
}

if (-not [string]::IsNullOrWhiteSpace($DiagnosticFile)) {
  try {
    [IO.File]::WriteAllText(
      $DiagnosticFile,
      "[INSTALL_DIR_OK] 程序目录检查通过。",
      (New-Object Text.UTF8Encoding($false))
    )
  } catch {
    # 成功状态已经由退出码 0 表达，诊断文件失败不能把安装误报为失败。
  }
}
exit 0
