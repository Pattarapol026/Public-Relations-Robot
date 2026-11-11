@echo off
rem Puts the wired port on the robot's network (192.168.10.x). Asks for administrator rights. See robot_network.ps1.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0robot_network.ps1" %*
