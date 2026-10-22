#!/bin/sh

cd -- "$(dirname -- "$0")" || exit 1
game_path='/mnt/ext4/apps2/SteamLibrary/steamapps/common/SpaceEngineers2/Game2'

venv/bin/python patcher.py "$game_path" || exit 1

if [ ! -f "$game_path/NvApiWrapper.dll.fix" ]; then
    printf "\nInfo: NvApiWrapper.dll.fix was not found, if you don't use a modded one, ignore it.\n"
    exit 1
fi
cp -f "$game_path/NvApiWrapper.dll.fix" "$game_path/NvApiWrapper.dll" || exit 1
printf "\nInfo: NvApiWrapper.dll was succesfully overwritten with the fixed one.\n"
