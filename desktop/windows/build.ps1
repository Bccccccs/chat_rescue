param(
    [Parameter(Mandatory=$true)][string]$Python,
    [Parameter(Mandatory=$true)][string]$SqlCipher,
    [string]$Weixin = '',
    [string]$Workspace = '',
    [string]$Destination = ''
)
$ErrorActionPreference='Stop'
$repository = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..\..')).Path
$pythonPath = (Resolve-Path -LiteralPath $Python).Path
$cipherPath = (Resolve-Path -LiteralPath $SqlCipher).Path
$compiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
$framework = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\WPF'
if (-not (Test-Path -LiteralPath $compiler)) { throw '需要 Windows .NET Framework 4.x 的 C# 编译器' }
if (-not $Destination) { $Destination = Join-Path $repository 'dist\windows' }
$destination = [IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destination | Out-Null
$references = @('PresentationFramework.dll','PresentationCore.dll','WindowsBase.dll') | ForEach-Object { '/reference:' + (Join-Path $framework $_) }
$output = Join-Path $destination 'ChatRescue.exe'
& $compiler /nologo /target:winexe /platform:x64 /optimize+ "/out:$output" /reference:System.Xaml.dll /reference:System.Web.Extensions.dll /reference:System.Windows.Forms.dll @references (Join-Path $PSScriptRoot 'ChatRescue.cs') (Join-Path $PSScriptRoot 'BackendTransport.cs') (Join-Path $PSScriptRoot 'SourceImportFlow.cs')
if ($LASTEXITCODE -ne 0) { throw 'Windows 桌面应用编译失败' }
$configuration = @{python=$pythonPath;sqlcipher=$cipherPath;repository=$repository;executable=$Weixin}
if ($Workspace) {
    if (-not [IO.Path]::IsPathRooted($Workspace)) { throw '工作目录必须是绝对路径' }
    $configuration.workspace = [IO.Path]::GetFullPath($Workspace)
}
[IO.File]::WriteAllText((Join-Path $destination 'backend-config.json'),($configuration|ConvertTo-Json),[Text.UTF8Encoding]::new($false))
Write-Output "桌面程序已生成：$output"
Write-Output '此为本机开发构建，保留配置指向的 Python、SQLCipher 和仓库；尚未制作独立安装包。'
