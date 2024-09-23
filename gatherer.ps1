$outputFileDjango = "output_django.txt"
$outputFileSetup = "output_setup.txt"
$setupDirectoryDjango = ".\django"
$setupDirectorySetup = ".\setup"

# Function to get relative path
function Get-RelativePath {
    param (
        [string]$Path,
        [string]$BasePath
    )
    $Path = $Path.Replace('\', '/')
    $BasePath = $BasePath.Replace('\', '/')
    
    $PathParts = $Path.Split('/')
    $BasePathParts = $BasePath.Split('/')
    
    $CommonParts = 0
    $MinLength = if ($PathParts.Length -lt $BasePathParts.Length) { $PathParts.Length } else { $BasePathParts.Length }
    
    for ($i = 0; $i -lt $MinLength; $i++) {
        if ($PathParts[$i] -eq $BasePathParts[$i]) {
            $CommonParts++
        }
        else {
            break
        }
    }
    
    $RelativePath = "../" * ($BasePathParts.Length - $CommonParts)
    $RelativePath += $PathParts[$CommonParts..($PathParts.Length - 1)] -join '/'
    
    return $RelativePath.Replace('/', '\')
}

# Clear the output files if they already exist
if (Test-Path $outputFileDjango) { Clear-Content $outputFileDjango }
if (Test-Path $outputFileSetup) { Clear-Content $outputFileSetup }

# Process Django files
$djangoFiles = Get-ChildItem -Path $setupDirectoryDjango -Filter "*.py" -Recurse -File

if ($djangoFiles.Count -eq 0) {
    Add-Content $outputFileDjango "=== No .py files found under $setupDirectoryDjango ===`n`n"
}
else {
    foreach ($file in $djangoFiles) {
        $relativePath = Get-RelativePath -Path $file.FullName -BasePath $PSScriptRoot
        Add-Content $outputFileDjango "=== Contents of $relativePath ==="
        Get-Content $file.FullName | Add-Content $outputFileDjango
        Add-Content $outputFileDjango "`n`n"
    }
}

Write-Host "Django file contents have been consolidated into $outputFileDjango"

# Process Setup files
$setupFiles = Get-ChildItem -Path $setupDirectorySetup -Include @("*.yaml", "*.sh", "*.tf") -Recurse -File

if ($setupFiles.Count -eq 0) {
    Add-Content $outputFileSetup "=== No .yaml, .sh, or .tf files found under $setupDirectorySetup ===`n`n"
}
else {
    foreach ($file in $setupFiles) {
        $relativePath = Get-RelativePath -Path $file.FullName -BasePath $PSScriptRoot
        Add-Content $outputFileSetup "=== Contents of $relativePath ==="
        Get-Content $file.FullName | Add-Content $outputFileSetup
        Add-Content $outputFileSetup "`n`n"
    }
}

Write-Host "Setup file contents have been consolidated into $outputFileSetup"
