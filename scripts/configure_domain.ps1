param(
  [Parameter(Mandatory = $true)]
  [string]$Domain,
  [string]$SiteName = "日本房地產海外查詢站",
  [string]$BrandName = ""
)

$ErrorActionPreference = "Stop"

$ProjectRoot = Resolve-Path (Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Path) "..")
$EnvPath = Join-Path $ProjectRoot ".env"

$normalizedDomain = $Domain.Trim().ToLower()
if ($normalizedDomain -match "^https?://") {
  throw "Domain must not include http:// or https://"
}
if ($normalizedDomain.Contains("/")) {
  throw "Domain must not include path."
}
# Caddyfile 使用 DOMAIN=根網域（manuvip.com）；SITE_URL 預設為 www canonical
if ($normalizedDomain.StartsWith("www.")) {
  $normalizedDomain = $normalizedDomain.Substring(4)
}

$siteUrl = "https://www.$normalizedDomain"

$content = @(
  "DOMAIN=$normalizedDomain"
  "SITE_URL=$siteUrl"
  "SITE_NAME=$SiteName"
  "BRAND_NAME=$BrandName"
)

Set-Content -Path $EnvPath -Value $content -Encoding UTF8

Write-Host "Updated .env"
Write-Host "DOMAIN=$normalizedDomain"
Write-Host "SITE_URL=$siteUrl"
Write-Host ""
Write-Host "Next step:"
Write-Host "docker compose -f docker-compose.prod.yml --env-file .env up -d --build"
