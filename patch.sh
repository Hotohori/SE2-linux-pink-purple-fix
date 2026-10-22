#!/bin/sh

cd -- "$(dirname -- "$0")" || exit 1
game_path='/mnt/ext4/apps2/SteamLibrary/steamapps/common/SpaceEngineers2/Game2'

venv/bin/python patcher.py "$game_path" || exit 1
cp -f "$game_path/NvApiWrapper.dll.sik" "$game_path/NvApiWrapper.dll"
