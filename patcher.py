#!/usr/bin/env python3
"""Force SE2's existing view-instancing fallback without fixed offsets or builds. Written by ChatGPT Codex 6.1"""

import argparse
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
import sys
import tempfile

import dnfile
from dncil.cil.body import CilMethodBody
from dncil.cil.body.reader import CilMethodBodyReaderBytes
from dncil.cil.enums import OperandType
from dncil.clr.token import Token


DEVICE_NAMESPACE = "Keen.VRage.Render12.Core.Device"
PROPERTY = "IsViewInstancingSupported"
LAUNCH_OPTIONS = "DOTNET_ReadyToRunExcludeList=VRage.Render12 %command%"


class PatchError(Exception):
    """An unsupported assembly or an unsafe file operation."""


def require(condition, message):
    if not condition:
        raise PatchError(message)


def only(items, description):
    require(len(items) == 1, f"{description}: expected exactly one match; found {len(items)}.")
    return items[0]


def token_value(instruction):
    return instruction.operand.value if isinstance(instruction.operand, Token) else None


def load_argument(instruction, number):
    name = instruction.mnemonic
    return name == f"ldarg.{number}" or (
        name in ("ldarg", "ldarg.s") and instruction.operand.index == number
    )


def integer(instruction, value):
    return instruction.mnemonic.startswith("ldc.i4") and instruction.get_ldc() == value


def meaningful(body):
    return [i for i in body.instructions if i.mnemonic != "nop"]


def read_body(pe, data, method):
    require(method.Rva != 0, f"{method.Name}: no IL method body.")
    require(method.ImplFlags.miIL and not method.ImplFlags.miNative,
            f"{method.Name}: not an IL method.")
    offset = pe.get_offset_from_rva(method.Rva)
    section = pe.get_section_by_rva(method.Rva)
    require(section is not None, "Method body is outside a PE section.")
    end = section.PointerToRawData + section.SizeOfRawData
    require(section.PointerToRawData <= offset < end <= len(data),
            "Method body is outside the file data.")
    body = CilMethodBody(CilMethodBodyReaderBytes(data[offset:end]))
    require(body.instructions and
            body.instructions[-1].offset + body.instructions[-1].size ==
            body.header_size + body.code_size,
            "Invalid IL length.")
    if body.flags.MoreSects:
        extra = (body.header_size + body.code_size + 3) & ~3
        header = data[offset + extra]
        require(header & 0x3F == 1 and not header & 0x80,
                "Unknown or chained extra method data sections.")
    return offset, body


def check_control_flow(body, start, store):
    """No other path may enter the expression or its consuming assignment."""
    boundaries = {i.offset for i in body.instructions}
    for instruction in body.instructions:
        kind = instruction.opcode.operand_type
        if kind in (OperandType.InlineBrTarget, OperandType.ShortInlineBrTarget):
            targets = [instruction.operand]
        elif kind == OperandType.InlineSwitch:
            targets = instruction.operand
        else:
            continue
        for target in targets:
            require(target in boundaries, "Invalid IL branch target.")
            require(not start < target <= store,
                    "A branch enters the assignment to be replaced.")
    for handler in body.exception_handlers:
        for boundary in (handler.try_start, handler.try_end, handler.handler_start,
                         handler.handler_end, handler.filter_start):
            if boundary >= 0:
                require(not start < body.header_size + boundary <= store,
                        "An exception boundary falls inside the assignment to be replaced.")


@dataclass(frozen=True)
class Patch:
    version: str
    offset: int
    before: bytes
    after: bytes
    disabled: bool

    def apply(self, data):
        require(data[self.offset:self.offset + len(self.before)] == self.before,
                "The patch site has changed.")
        return data[:self.offset] + self.after + data[self.offset + len(self.before):]


def _analyze(pe, data):
    require(pe.net and pe.net.mdtables, "Not a .NET assembly.")
    tables = pe.net.mdtables
    assembly = only(list(tables.Assembly or []), "Assembly")
    require(str(assembly.Name) == "VRage.Render12", "The assembly is not named VRage.Render12.")
    version = ".".join(str(getattr(assembly, name)) for name in
                       ("MajorVersion", "MinorVersion", "BuildNumber", "RevisionNumber"))
    device = only([t for t in tables.TypeDef
                   if str(t.TypeNamespace) == DEVICE_NAMESPACE and str(t.TypeName) == "DeviceWrap"],
                  "DeviceWrap")
    field = only([f for f in device.FieldList
                  if str(f.row.Name) == f"<{PROPERTY}>k__BackingField"], "View-instancing field")
    require(field.row.Signature.value == b"\x06\x02" and not field.row.Flags.fdStatic,
            "The view-instancing field is not a boolean instance field.")
    field_token = 0x04000000 | field.row_index

    # Verify that the named accessors still read/write only this boolean.
    accessor_tokens = {}
    for prefix, signature, expected in (
        ("get", b"\x20\x00\x02", ["ldarg.0", "ldfld", "ret"]),
        ("set", b"\x20\x01\x01\x02", ["ldarg.0", "ldarg.1", "stfld", "ret"]),
    ):
        method = only([m for m in device.MethodList
                       if str(m.row.Name) == f"{prefix}_{PROPERTY}"], f"{prefix}_{PROPERTY}")
        require(method.row.Signature.value == signature, "Unexpected accessor signature.")
        _, body = read_body(pe, data, method.row)
        instructions = meaningful(body)
        require([i.mnemonic for i in instructions] == expected and
                token_value(instructions[-2]) == field_token and not body.exception_handlers,
                "The view-instancing accessors contain unexpected logic.")
        accessor_tokens[prefix] = 0x06000000 | method.row_index

    assignments = []
    for method in device.MethodList:
        if str(method.row.Name) != ".ctor":
            continue
        offset, body = read_body(pe, data, method.row)
        instructions = meaningful(body)
        for index, instruction in enumerate(instructions):
            if ((instruction.mnemonic in ("call", "callvirt") and
                 token_value(instruction) == accessor_tokens["set"]) or
                (instruction.mnemonic == "stfld" and token_value(instruction) == field_token)):
                assignments.append((offset, body, instructions, index))
    method_offset, body, instructions, index = only(assignments, "Constructor assignment")
    store = instructions[index]

    # Also recognize upstream's patch, without relying on its hash or padding length.
    disabled = (index >= 2 and load_argument(instructions[index - 2], 0) and
                integer(instructions[index - 1], 0))
    if disabled:
        start, expression = instructions[index - 2:index]
    else:
        require(index >= 7, "The assignment does not contain a supported tier check.")
        start, expression, field_load, one, comparison, zero, equal = instructions[index - 7:index]
        require(load_argument(start, 0) and
                expression.mnemonic in ("ldloc", "ldloc.s", "ldloc.0", "ldloc.1",
                                        "ldloc.2", "ldloc.3") and
                field_load.mnemonic == "ldfld" and integer(one, 1) and
                comparison.mnemonic in ("clt", "clt.un") and integer(zero, 0) and
                equal.mnemonic == "ceq",
                "Unknown logic: expected ViewInstancingTier >= Tier1.")
        token = field_load.operand
        require(isinstance(token, Token) and token.table == 0x0A and
                tables.MemberRef and 1 <= token.rid <= len(tables.MemberRef.rows),
                "Invalid reference to ViewInstancingTier.")
        member = tables.MemberRef.rows[token.rid - 1]
        owner = member.Class.row
        require(str(member.Name) == "ViewInstancingTier" and
                str(getattr(owner, "TypeNamespace", "")) == "Vortice.Direct3D12" and
                str(getattr(owner, "TypeName", "")) == "FeatureDataD3D12Options3" and
                member.Signature.value.startswith(b"\x06"),
                "The check does not read D3D12Options3.ViewInstancingTier.")

    check_control_flow(body, start.offset, store.offset)
    offset = method_offset + expression.offset
    before = data[offset:method_offset + store.offset]
    require(before, "Empty patch range.")
    after = before if disabled else b"\x16" + b"\x00" * (len(before) - 1)
    return Patch(version, offset, before, after, disabled)


def analyze(data):
    try:
        pe = dnfile.dnPE(data=data, clr_lazy_load=True)
        try:
            return _analyze(pe, data)
        finally:
            pe.close()
    except PatchError:
        raise
    except Exception as error:
        raise PatchError(f"Cannot safely analyze the DLL: {error}") from error


def backup_path(path, patched):
    # The current patched contents select their own backup, even after game updates.
    digest = hashlib.sha256(patched).hexdigest()
    return path.with_name(f"{path.name}.purplefix-{digest}.orig")


def save_backup(path, data, mode):
    require(not path.is_symlink(), f"Backup must not be a symbolic link: {path}")
    try:
        handle = path.open("xb")
    except FileExistsError:
        require(path.read_bytes() == data, f"A different backup already exists: {path}")
        return
    try:
        with handle:
            os.fchmod(handle.fileno(), mode)
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        path.unlink()
        raise


def replace_file(path, expected, replacement):
    mode = stat.S_IMODE(path.stat().st_mode)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(prefix=f".{path.name}.", dir=path.parent,
                                         delete=False) as handle:
            temporary = Path(handle.name)
            os.fchmod(handle.fileno(), mode)
            handle.write(replacement)
            handle.flush()
            os.fsync(handle.fileno())
        require(path.read_bytes() == expected, "The DLL changed during this operation. Aborting.")
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def patch_file(path):
    data = path.read_bytes()
    patch = analyze(data)
    if patch.disabled:
        return False, backup_path(path, data)
    patched = patch.apply(data)
    verified = analyze(patched)
    require(verified.disabled and len(patched) == len(data),
            "Patched DLL verification failed.")
    backup = backup_path(path, patched)
    save_backup(backup, data, stat.S_IMODE(path.stat().st_mode))
    replace_file(path, data, patched)
    return True, backup


def restore_file(path):
    current = path.read_bytes()
    require(analyze(current).disabled, "The DLL does not force view instancing off.")
    backup = backup_path(path, current)
    require(backup.is_file() and not backup.is_symlink(),
            f"No matching backup for this DLL: {backup}")
    original = backup.read_bytes()
    patch = analyze(original)
    require(not patch.disabled and patch.apply(original) == current,
            "The backup does not match the current patched DLL.")
    replace_file(path, current, original)
    return backup


def resolve_target(path):
    if path.is_dir():
        candidates = [path / "VRage.Render12.dll", path / "Game2" / "VRage.Render12.dll"]
        path = only([candidate for candidate in candidates if candidate.is_file()],
                    "VRage.Render12.dll in the specified directory")
    require(path.is_file(), f"DLL not found: {path}")
    return path.resolve()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="SE2 Proton fix: disable view instancing using .NET metadata.")
    parser.add_argument("path", nargs="?", type=Path, default=Path("VRage.Render12.dll"),
                        help="DLL, Game2 directory, or game directory (default: ./VRage.Render12.dll)")
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--status", action="store_true", help="Check only; do not modify any files")
    modes.add_argument("--restore", action="store_true", help="Restore the matching original DLL")
    args = parser.parse_args(argv)
    try:
        path = resolve_target(args.path)
        if args.status:
            data = path.read_bytes()
            patch = analyze(data)
            print(f"DLL: {path}\nAssembly version: {patch.version}")
            print("Status: View instancing is forced off." if patch.disabled else
                  "Status: Supported tier check found; ready to patch.")
            print(f"Detected patch site: 0x{patch.offset:X} ({len(patch.before)} bytes)")
            backup = backup_path(path, patch.apply(data))
            print(f"Backup: {backup if backup.is_file() else 'not found'}")
        elif args.restore:
            restore_file(path)
            print(f"Original restored: {path}\nThe backup has been kept.")
            print("The DOTNET_ReadyToRunExcludeList launch option can be removed.")
        else:
            changed, backup = patch_file(path)
            print(f"Patched: {path}" if changed else "View instancing is already forced off.")
            if backup.is_file():
                print(f"Backup: {backup}")
            else:
                print("No backup from this patcher was found; no changes were made.")
            print(f"\nRequired Steam launch option:\n{LAUNCH_OPTIONS}")
        return 0
    except (PatchError, OSError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
