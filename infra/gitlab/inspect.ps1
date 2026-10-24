# GreenGate: read-only inspection of the GitLab side. Changes nothing.
# Usage:  & .\infra\gitlab\inspect.ps1 [-ProjectPath group/sub/project]
# Writes a summary to .gg-out\inspect.txt (gitignored) and to the console.
param([string]$ProjectPath = "")

$ErrorActionPreference = "Continue"
$out = Join-Path $PSScriptRoot "..\..\.gg-out"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out "inspect.txt"
"" | Out-File -Encoding utf8 $log

function Say([string]$s) { Write-Output $s; $s | Out-File -Append -Encoding utf8 $log }
function Api([string]$path) {
  $raw = glab api $path 2>&1 | Out-String
  try { return $raw | ConvertFrom-Json } catch { Say "  API $path failed: $($raw.Trim())"; return $null }
}
function Enc([string]$s) { [Uri]::EscapeDataString($s) }

$me = Api "user"
Say "user: $($me.username) (id $($me.id))"

Say "`n== Projects I am a member of =="
$projects = Api "projects?membership=true&per_page=100&order_by=created_at"
foreach ($p in $projects) {
  $lvl = $p.permissions.project_access.access_level; $glvl = $p.permissions.group_access.access_level
  Say ("  {0}  {1}  created {2}  {3}  access p={4} g={5}" -f $p.id, $p.path_with_namespace, $p.created_at.Substring(0,10), $p.visibility, $lvl, $glvl)
}

Say "`n== Groups =="
$groups = Api "groups?per_page=100&min_access_level=10"
foreach ($g in $groups) { Say ("  {0}  {1}" -f $g.id, $g.full_path) }

if (-not $ProjectPath) {
  $hack = $projects | Where-Object { $_.path_with_namespace -like "gitlab-ai-hackathon/*" } | Select-Object -First 1
  if ($hack) { $ProjectPath = $hack.path_with_namespace }
}
if (-not $ProjectPath) { Say "`nNo hackathon project found. Pass -ProjectPath."; return }

$id = Enc $ProjectPath
$proj = Api "projects/$id?license=true&statistics=false"
Say "`n== Project $ProjectPath =="
Say ("  id {0}, created {1}, visibility {2}, default branch {3}" -f $proj.id, $proj.created_at, $proj.visibility, $proj.default_branch)
Say ("  web: {0}" -f $proj.web_url)
Say ("  license: {0}" -f $proj.license.name)
Say ("  only_allow_merge_if_pipeline_succeeds={0} remove_source_branch_after_merge={1} merge_method={2}" -f $proj.only_allow_merge_if_pipeline_succeeds, $proj.remove_source_branch_after_merge, $proj.merge_method)
Say ("  shared_runners_enabled={0} builds_access_level={1} issues_enabled={2}" -f $proj.shared_runners_enabled, $proj.builds_access_level, $proj.issues_enabled)
Say ("  my access: project={0} group={1}" -f $proj.permissions.project_access.access_level, $proj.permissions.group_access.access_level)

Say "`n== Repository tree =="
$tree = Api "projects/$id/repository/tree?recursive=true&per_page=100"
foreach ($t in $tree) { Say ("  {0}  {1}" -f $t.type, $t.path) }

Say "`n== Files (content) =="
foreach ($t in ($tree | Where-Object { $_.type -eq "blob" })) {
  if ($t.path -match '\.(md|ya?ml|json|toml|txt)$' -or $t.path -match '(^|/)(LICENSE|Dockerfile)$') {
    $f = Api ("projects/$id/repository/files/{0}?ref={1}" -f (Enc $t.path), $proj.default_branch)
    if ($f) {
      $text = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($f.content))
      Say "----- $($t.path) -----"
      Say $text
    }
  }
}

Say "`n== Issues =="
$issues = Api "projects/$id/issues?per_page=20&state=all"
foreach ($i in $issues) { Say ("  #{0} [{1}] {2}  labels={3}" -f $i.iid, $i.state, $i.title, ($i.labels -join ",")) }
$first = $issues | Sort-Object iid | Select-Object -First 1
if ($first) { Say "----- issue #$($first.iid) body -----"; Say $first.description }

Say "`n== Labels =="
foreach ($l in (Api "projects/$id/labels?per_page=100")) { Say "  $($l.name)" }
Say "`n== Protected branches =="
foreach ($b in (Api "projects/$id/protected_branches")) { Say ("  {0} push={1} merge={2}" -f $b.name, ($b.push_access_levels.access_level -join ","), ($b.merge_access_levels.access_level -join ",")) }
Say "`n== CI variables (names only) =="
foreach ($v in (Api "projects/$id/variables?per_page=100")) { Say ("  {0} masked={1} protected={2}" -f $v.key, $v.masked, $v.protected) }
Say "`n== Recent pipelines =="
foreach ($pl in (Api "projects/$id/pipelines?per_page=10")) { Say ("  {0} {1} {2} {3}" -f $pl.id, $pl.status, $pl.ref, $pl.created_at) }
Say "`nDone. Summary saved to .gg-out\inspect.txt"
