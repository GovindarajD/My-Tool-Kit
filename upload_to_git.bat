@echo off
setlocal enabledelayedexpansion

:: ==========================================
:: AUTOMATIC DEFAULTS
:: ==========================================
set "DEFAULT_FOLDER=%CD%"
set "BRANCH=main"
set "COMMIT_MSG=Added files via batch script"
set "CACHE_FILE=%DEFAULT_FOLDER%\.git_remote_url.txt"

echo ===================================================
echo             DIRECT GITHUB UPLOAD TOOL
echo ===================================================

:: Step 1: Initialize Git if it hasn't been done yet
if not exist ".git" (
    echo [1/4] Initializing local Git repository...
    git init -b %BRANCH%
) else (
    echo [1/4] Local Git repository already initialized.
)

:: Step 2: Handle Remote Repository Configuration
if exist "%CACHE_FILE%" (
    set /p REPO_URL=<"%CACHE_FILE%"
    echo [Saved Remote Found] !REPO_URL!
    
    set /p "RESET_CHOICE=Do you want to change the remote URL/Token? (Y/N, default=N): "
    if /i "!RESET_CHOICE!"=="Y" (
        del "%CACHE_FILE%"
        git remote remove origin >nul 2>&1
        goto :prompt_url
    )
    goto :start_upload
)

:prompt_url
echo ---------------------------------------------------
echo Format: https://github.com
echo Example: https://github.com
echo ---------------------------------------------------
set /p "USER_URL=Enter your FULL Authenticated URL: "

if "%USER_URL%"=="" (
    echo Error: URL cannot be empty.
    goto :prompt_url
)

:: Save it directly to file without parsing it or breaking variables
echo %USER_URL%>"%CACHE_FILE%"
set "REPO_URL=%USER_URL%"

:start_upload
:: Configure or update the remote URL safely in Git
git remote remove origin >nul 2>&1
git remote add origin %REPO_URL%

:: Step 3: Stage and Commit
echo [2/4] Staging files...
git add .

echo [3/4] Committing files...
git commit -m "%COMMIT_MSG%"

:: Step 4: Push directly to Remote Branch
echo [4/4] Pushing changes directly to GitHub...
git push -u origin %BRANCH% --force

if %ERRORLEVEL% EQU 0 (
    echo ===================================================
    echo SUCCESS: Workspace contents uploaded to %BRANCH% branch!
    echo ===================================================
) else (
    echo ===================================================
    echo ERROR: Push failed. Check your token permissions or URL string.
    echo ===================================================
)

pause
