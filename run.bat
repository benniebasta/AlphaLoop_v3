@echo off
title AlphaLoop v3 - WebUI
cd /d "%~dp0"
set PYTHONPATH=src
python -c "from alphaloop.webui.app import run_server; run_server()"
pause
