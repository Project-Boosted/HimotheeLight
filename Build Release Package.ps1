$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$chunkDir = Join-Path $repoRoot 'releases\v0.8.0'
$outFile = Join-Path $repoRoot 'releases\HimotheeLight-v0.8.0.zip'
$expectedSha256 = '4946e20d1e8620a3b7f42d72f395528e77ca79e60995e1a9dc36e7bc76cc8da1'

$chunks = Get-ChildItem -Path $chunkDir -Filter 'part-*.b64' | Sort-Object Name
if (-not $chunks) {
    throw "No release chunks found in $chunkDir"
}

$builder = [System.Text.StringBuilder]::new()
foreach ($chunk in $chunks) {
    [void]$builder.Append((Get-Content -Raw -Path $chunk.FullName).Trim())
}

$bytes = [Convert]::FromBase64String($builder.ToString())
[IO.File]::WriteAllBytes($outFile, $bytes)

$actualSha256 = (Get-FileHash -Algorithm SHA256 -Path $outFile).Hash.ToLowerInvariant()
if ($actualSha256 -ne $expectedSha256) {
    Remove-Item -Force $outFile -ErrorAction SilentlyContinue
    throw "Release reconstruction failed SHA-256 verification. Expected $expectedSha256 but got $actualSha256"
}

Write-Host "HimotheeLight v0.8.0 release rebuilt successfully."
Write-Host "File: $outFile"
Write-Host "SHA-256: $actualSha256"
