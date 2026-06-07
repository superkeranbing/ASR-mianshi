@echo off
cd /d "%~dp0"

REM -- Start Docker services if not running
docker ps | findstr "asr-postgres" >nul 2>&1
if errorlevel 1 (
    echo Starting Docker services...
    docker compose -f docker\docker-compose.dev.yml up -d
    echo Waiting for PostgreSQL...
    timeout /t 5 /nobreak >nul
)

cd backend

REM -- Find the right Python
set PYTHON_EXE=
if exist "C:\Users\wsx\anaconda3\python.exe" (
    set PYTHON_EXE=C:\Users\wsx\anaconda3\python.exe
) else (
    for /f "tokens=*" %%i in ('where python 2^>nul') do ( set PYTHON_EXE=%%i & goto :found )
    :found
)

if "%PYTHON_EXE%"=="" (
    echo ERROR: Python not found.
    pause & exit /b 1
)

echo Using Python: %PYTHON_EXE%
echo Starting ASR-Mianshi on http://localhost:8000
echo Frontend: http://localhost:8000
echo API Docs: http://localhost:8000/api/docs
echo.
%PYTHON_EXE% -c "import uvicorn" 2>nul || %PYTHON_EXE% -m pip install fastapi uvicorn sqlalchemy aiosqlite pydantic pydantic-settings python-jose passlib python-multipart aiofiles websockets python-docx psycopg2-binary -i https://pypi.tuna.tsinghua.edu.cn/simple
%PYTHON_EXE% -m uvicorn app.main:app --host 0.0.0.0 --port 8000 --log-level info
pause
