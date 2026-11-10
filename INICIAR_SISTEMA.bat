@echo off
chcp 65001 > nul
title Controle de Saldos de Cartoes - Estacionamento
cd /d "%~dp0"
echo =========================================================
echo   CONTROLE DE SALDOS REMANESCENTES - ESTACIONAMENTO
echo =========================================================
echo.
echo Iniciando o servidor local...
echo O navegador abrira automaticamente em http://localhost:5000
echo (Mantenha esta janela aberta enquanto estiver usando o sistema)
echo.
start "" "http://localhost:5000"
python app.py
pause

