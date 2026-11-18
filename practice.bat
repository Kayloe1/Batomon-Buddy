@echo off
rem Plays the practice test in practice_plan.json: offline games in the simulator, no game window needed.
rem Runs at low priority and leaves 4 processor cores free, so the live bot and the PC stay quick.
rem Close its window to stop; double-click again to carry on where it stopped (finished games are kept).
rem Uses PyPy (about 3x faster) when it is unpacked into a "pypy" folder here; otherwise the normal Python.
cd /d "%~dp0"
set "PY=.venv\Scripts\python.exe"
if exist "pypy\pypy.exe" set "PY=pypy\pypy.exe"
if exist "pypy\pypy3.exe" set "PY=pypy\pypy3.exe"
start "Practice games (close to stop)" /low /min "%PY%" -m engine.abtest --plan practice_plan.json %*
