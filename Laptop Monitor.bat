@echo off
:: Run this every day after the first-time setup is done.
:: No console window appears — just the GUI.
start "" pythonw "%~dp0app_launcher.pyw"
