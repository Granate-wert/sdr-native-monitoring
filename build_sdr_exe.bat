@echo off
py -m PyInstaller --noconfirm --name SDR_Native_Monitoring --exclude-module esw_dfl --exclude-module olefile main_sdr.py
