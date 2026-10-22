# SE2 Pink/Purple Fix

Local patcher for the view-instancing bug in Space Engineers 2 under Proton.
It enables the renderer's existing fallback by changing the assignment to
`DeviceWrap.IsViewInstancingSupported` to `false`.

The approach is based on [nirnx/se2-proton-viewinstancing-fix](https://github.com/nirnx/se2-proton-viewinstancing-fix).
This patcher locates the patch site using .NET metadata and IL instructions.
It does not rely on a fixed game version, checksum, file offset, local variable
index, or metadata token.

## Installation

Requires Python 3.10 or later.

Clone this repo:

```bash
git clone https://github.com/Hotohori/SE2-linux-purple-fix.git
```
<sup>Or if you don't have git installed, download it over the green "<> Code" button on
the top of the site over "Download ZIP".</sup>


Create a virtual environment:

```bash
python3 -m venv venv
```

Install the dependencies:

```bash
venv/bin/python -m pip install -r requirements.txt
```

## Usage

Close the game before patching or restoring the DLL.
First, check the DLL in the current working directory without making changes:

```bash
venv/bin/python patcher.py --status
```

Apply the patch or restore the original:

```bash
venv/bin/python patcher.py
venv/bin/python patcher.py --restore
```

Each command optionally accepts a path to the DLL, the `Game2` directory,
or the game directory:

```bash
venv/bin/python patcher.py "/path/to/SpaceEngineers2/Game2/VRage.Render12.dll"
```

A copy patched here only takes effect once it is placed in the game's actual
`Game2` directory. Alternatively, run the patcher directly on the DLL there.

In Steam, enter the following under **Properties → General → Launch Options**:

```text
DOTNET_ReadyToRunExcludeList=VRage.Render12 %command%
```

The DLL contains precompiled ReadyToRun code. This option makes .NET use the
modified IL instructions. It can be removed after restoring the original DLL.

## Game updates

Run the patcher again after a game update or Steam file verification.
The new DLL is checked regardless of its version number.

The supported assignment is `IsViewInstancingSupported = ViewInstancingTier >= 1`
in the constructor of `Keen.VRage.Render12.Core.Device.DeviceWrap`. The patcher
also checks the boolean property, its accessors, and the
`Vortice.Direct3D12.FeatureDataD3D12Options3.ViewInstancingTier` field being read.
It tolerates shifted file offsets, changed metadata references, different local
variable indices, short and long IL forms of the constants and local loads used
in the expression, and NOP instructions.

If the logic is missing, ambiguous, or structured differently, the patcher aborts.
Branches and exception boundaries inside the affected assignment are rejected.
Arbitrary future changes to the renderer are therefore not automatically supported.

The patch preserves the length of the method body and the entire DLL.
The result is analyzed again before it is written. An assignment that already
forces view instancing off is recognized, including the original nirnx patch.

## Backups

Before making changes, the patcher creates a file alongside the DLL:

```text
VRage.Render12.dll.purplefix-<SHA256 of the patched DLL>.orig
```

The checksum is used only to identify the matching backup, not to allow specific
game versions. Each patched version gets its own backup. `--restore` only accepts
a backup that produces the exact current DLL when patched, so old backups cannot
roll back a game update. Backups are kept after restoration.

For a DLL already patched by another tool, this patcher cannot create an original
backup. Use that tool's backup or verify the game files through Steam.

## Tests

```bash
venv/bin/python -m unittest discover -s tests -v
```

The integration tests use only temporary copies of the local
`VRage.Render12.dll`. If the DLL is missing, these tests are skipped.
Synthetically modified copies test shifted sections, changed reference indices,
and different versions, among other cases. This does not replace testing the game
with actual future builds as they are released.

## Extra

If you have a patched NvApiWrapper.dll, save it as NvApiWrapper.dll.fix inside the
Game2 directory inside Space Engineers 2 alongside the original NvApiWrapper.dll.
Open patch.sh in a text editor and change game_path to your Game2 directory of your
own SE2 installation. After that you can use to patch.sh to use this fix here and
to copy your NvApiWrapper.dll.fix over NvApiWrapper.dll after a SE2 Steam update
too. Mainly used for NVidia users to fix the deactived raytracing option. You can
find it on the official official Keen Software House Discord (pinned under #linux).
