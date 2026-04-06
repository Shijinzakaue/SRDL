@echo off
cd /d "%~dp0"
start powershell -NoExit -Command "python SRDL.py"
