```powershell
# Detect Python with graphify — uv/pipx-aware (fixes #831)
New-Item -ItemType Directory -Force -Path graphify-out | Out-Null
$GRAPHIFY_PYTHON = $null

function Find-GraphifyPython {
    # 1. uv tool install — 'uv tool dir' is authoritative, respects UV_TOOL_DIR automatically
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        $uvDir = (uv tool dir 2>$null).Trim()
        if ($uvDir) {
            $py = Join-Path $uvDir "graphifyy\Scripts\python.exe"
            if (Test-Path $py) {
                & $py -c "import graphify" 2>$null
                if ($LASTEXITCODE -eq 0) { return $py }
            }
        }
    }
    # 2. pipx install — 'pipx environment' respects PIPX_HOME automatically
    if (Get-Command pipx -ErrorAction SilentlyContinue) {
        $venvs = (pipx environment --value PIPX_LOCAL_VENVS 2>$null).Trim()
        if ($venvs) {
            $py = Join-Path $venvs "graphifyy\Scripts\python.exe"
            if (Test-Path $py) {
                & $py -c "import graphify" 2>$null
                if ($LASTEXITCODE -eq 0) { return $py }
            }
        }
    }
    # 3. Active venv / conda / pip-into-current-env
    $pyCmd = Get-Command python -ErrorAction SilentlyContinue
    if ($pyCmd) {
        & $pyCmd.Source -c "import graphify" 2>$null
        if ($LASTEXITCODE -eq 0) {
            return (& $pyCmd.Source -c "import sys; print(sys.executable)").Trim()
        }
    }
    return $null
}

# Try to find the right Python (uv → pipx → active env)
$GRAPHIFY_PYTHON = Find-GraphifyPython

# Not found — install then re-detect
if (-not $GRAPHIFY_PYTHON) {
    if (Get-Command uv -ErrorAction SilentlyContinue) {
        uv tool install --upgrade graphifyy -q 2>&1 | Select-Object -Last 3
    } else {
        pip install graphifyy -q 2>&1 | Select-Object -Last 3
    }
    $GRAPHIFY_PYTHON = Find-GraphifyPython
}

# Save interpreter path — all subsequent steps read this
$GRAPHIFY_PYTHON | Out-File -FilePath graphify-out\.graphify_python -Encoding utf8 -NoNewline
# Save scan root so `graphify update` (no args) knows where to look next time
(Resolve-Path INPUT_PATH).Path | Out-File -FilePath graphify-out\.graphify_root -Encoding utf8 -NoNewline
```

If the import succeeds, print nothing and move straight to Step 2.

**In every subsequent block, run Python through the checked interpreter so every step uses the interpreter that actually has graphify. The bash blocks below start with the interpreter check and call `"${GRAPHIFY_PY:?}"`; keep those lines when you run them. When you run a step in PowerShell instead, start it with the check below and call `& $GRAPHIFY_PY` in place of a bare `python3`. Never run the contents of `graphify-out\.graphify_python` unchecked: a cloned repository can ship that file, and the check refuses relative paths and anything inside this project or its git work tree.**

```powershell
$GRAPHIFY_PY = "$(Get-Content -Raw -LiteralPath graphify-out\.graphify_python -ErrorAction SilentlyContinue)".Trim()
try {
    $_gok = ($GRAPHIFY_PY -match '^([A-Za-z]:[\\/]|/(?![\\/]))') -and ($GRAPHIFY_PY -notmatch '~|^/(proc|dev)/|(^|[\\/])\.\.?([\\/]|$)') -and (Test-Path -LiteralPath $GRAPHIFY_PY -PathType Leaf)
    $_gi = Get-Item -LiteralPath $GRAPHIFY_PY -Force; $_gpaths = @($_gi.FullName)
    if ($_gi.LinkType) { $_gpaths += $(if ($_gi.PSObject.Methods['ResolveLinkTarget']) { $_gi.ResolveLinkTarget($true).FullName } else { [IO.Path]::GetFullPath([IO.Path]::Combine($_gi.DirectoryName, "$($_gi.Target)")) }) }
    $_groots = @((Get-Location).ProviderPath) + @(if (Get-Command git -ErrorAction SilentlyContinue) { git rev-parse --show-toplevel 2>$null }) | Where-Object { $_ } | ForEach-Object { ([IO.Path]::GetFullPath($_) -replace '\\', '/').TrimEnd('/') + '/' }
    foreach ($_gp in $_gpaths) {
        for ($_ga = $(if ($_gp -eq $_gpaths[0]) { Split-Path -Parent $_gp } else { $_gp }); $_ga; $_ga = Split-Path -Parent $_ga) { if ((Get-Item -LiteralPath $_ga -Force).LinkType) { $_gok = $false } }
        foreach ($_gr in $_groots) { if (($_gp -replace '\\', '/').StartsWith($_gr, [StringComparison]::OrdinalIgnoreCase)) { $_gok = $false } }
    }
} catch { $_gok = $false }
if (-not $_gok) { $GRAPHIFY_PY = $null; Write-Error "graphify: refusing graphify-out\.graphify_python - it must name an absolute Python outside this project; rerun the interpreter guard" }
```
