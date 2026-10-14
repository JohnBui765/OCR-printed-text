@echo off
cd /d "%~dp0"
python img2word.py
if errorlevel 1 pause
