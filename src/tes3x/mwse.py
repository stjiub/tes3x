"""Audit compiled plugins for legacy MWSE 0.9.4 bytecode.

MWEdit emits a small stack-machine instruction stream inside SCPT/SCDT.  This
tool reports that stream without changing the plugin, so an Xbox compatibility
payload can be checked against the exact operations a mod needs.
"""

import argparse
import json
import os
import re
import struct
import sys

from tes3x.records import records, subrecords  # noqa: E402


# name, inline operand bytes.  Function arguments are passed on the MWSE stack;
# only the VM's own control instructions carry operands in the instruction.
VM_OPS = {
    0x3801: ("Call", 4),
    0x3802: ("CallShort", 2),
    0x3803: ("Return", 0),
    0x3804: ("ReturnP", 1),
    0x3805: ("ReturnVP", 1),
    0x3806: ("CopyReg", 1),
    0x3807: ("CopyFromStack", 1),
    0x3808: ("CopyToStack", 1),
    0x3809: ("Jump", 4),
    0x380A: ("JumpShort", 2),
    0x380B: ("JumpZero", 4),
    0x380C: ("JumpShortZero", 2),
    0x380D: ("JumpNotZero", 4),
    0x380E: ("JumpShortNotZero", 2),
    0x380F: ("Pop", 2),
    0x3810: ("PopReg", 1),
    0x3811: ("Push", 4),
    0x3812: ("PushB", 1),
    0x3813: ("PushS", 2),
    0x3814: ("PushReg", 1),
    0x3815: ("DeclareLocal", 1),
    0x3816: ("JumpPositive", 4),
    0x3817: ("JumpShortPositive", 2),
    0x3818: ("JumpNegative", 4),
    0x3819: ("JumpShortNegative", 2),
    0x3820: ("Add", 0),
    0x3821: ("Sub", 0),
    0x3822: ("Mul", 0),
    0x3823: ("Div", 0),
    0x3824: ("Mod", 0),
    0x3828: ("IntToFloat", 0),
    0x3829: ("FloatToInt", 0),
    0x382A: ("FloatAdd", 0),
    0x382B: ("FloatSub", 0),
    0x382C: ("FloatMul", 0),
    0x382D: ("FloatDiv", 0),
    0x3C00: ("GetLocal", 0),
    0x3C01: ("GetForeign", 0),
    0x3C02: ("SetLocal", 0),
    0x3C03: ("SetForeign", 0),
    0x3C06: ("RefPCTarget", 0),
    0x3C18: ("SetRef", 0),
}


# Public command names and opcodes are the legacy MWEdit/MWSE 0.9.4 interface.
COMMANDS = {
    0x3830: "XTan",
    0x3831: "XSin",
    0x3832: "XCos",
    0x3833: "XArcTan",
    0x3834: "XArcSin",
    0x3835: "XArcCos",
    0x3836: "XDegRad",
    0x3837: "XRadDeg",
    0x3838: "XSqrt",
    0x3839: "XHypot",
    0x3C04: "XAITravel",
    0x3C05: "XPosition",
    0x3C07: "XGetPCTarget",
    0x3C08: "XRefType",
    0x3C09: "XLogMessage",
    0x3C10: "XFileRewind",
    0x3C11: "XFileReadShort",
    0x3C12: "XFileReadLong",
    0x3C13: "XFileReadFloat",
    0x3C14: "XFileReadString",
    0x3C15: "XFileSeek",
    0x3C1A: "XFirstNPC",
    0x3C1B: "XNextRef",
    0x3C1C: "XRefID",
    0x3C1D: "XGetRef",
    0x3C1E: "XFirstItem",
    0x3C1F: "XFirstStatic",
    0x3C20: "XGetCombat",
    0x3C21: "XStartCombat",
    0x3C22: "XDistance",
    0x3C28: "XAddItem",
    0x3C29: "XRemoveItem",
    0x3C2A: "XInventory",
    0x3C2B: "XNextStack",
    0x3C2C: "XPositionCell",
    0x3C2D: "XPCCellID",
    0x3C2E: "XPlace",
    0x3C2F: "XStringCompare",
    0x3C30: "XHasItemEquipped",
    0x3C31: "XFileWriteShort",
    0x3C32: "XFileWriteLong",
    0x3C33: "XFileWriteFloat",
    0x3C34: "XFileWriteString",
    0x3E61: "XSetValue",
    0x3E62: "XSetWeight",
    0x3E63: "XSetQuality",
    0x3E64: "XSetCondition",
    0x3E65: "XSetMaxCondition",
    0x3E66: "XSetCharge",
    0x3E67: "XSetMaxCharge",
    0x3E68: "XStringMatch",
    0x3F00: "XKeyPressed",
    0x3F01: "XTextInput",
    0x3F02: "XTextInputAlt",
    0x3F03: "XContentList",
    0x3F04: "XGetService",
    0x3F05: "XSetService",
    0x3F06: "XModService",
    0x3F08: "XFileReadText",
    0x3F09: "XFileWriteText",
    0x3F0A: "XStringLength",
    0x3F0B: "XStringBuild",
    0x3F0C: "XStringParse",
    0x3F0D: "XDrop",
    0x3F0E: "XEquip",
    0x3F0F: "XSetName",
    0x3F10: "XGetSpellEffects",
    0x3F11: "XSetOwner",
    0x3F12: "XCast",
    0x3F21: "XIsFemale",
    0x3F22: "XMyCellID",
    0x3F23: "XGetBaseGold",
    0x3F24: "XGetGold",
    0x3F25: "XSetBaseGold",
    0x3F26: "XSetGold",
    0x3F31: "XGetBaseStr",
    0x3F32: "XGetBaseInt",
    0x3F33: "XGetBaseWil",
    0x3F34: "XGetBaseAgi",
    0x3F35: "XGetBaseSpe",
    0x3F36: "XGetBaseEnd",
    0x3F37: "XGetBasePer",
    0x3F38: "XGetBaseLuc",
    0x3F3A: "XIsTrader",
    0x3F3F: "XMemLook",
    0x3F5E: "XIsTrainer",
    0x3F61: "XGetValue",
    0x3F62: "XGetOwner",
    0x3F63: "XGetWeight",
    0x3F64: "XGetEncumb",
    0x3F65: "XGetCondition",
    0x3F66: "XGetMaxCondition",
    0x3F67: "XGetCharge",
    0x3F68: "XGetMaxCharge",
    0x3F69: "XGetQuality",
    0x3F6E: "XGetName",
    0x3F6F: "XGetBaseID",
    0x3F7C: "XIsProvider",
    0x3F7E: "XMessageFix",
    0x3FA0: "XAddSpell",
    0x3FA1: "XRemoveSpell",
}

# The injected 0.9.4 interpreter implements the complete catalogue above. Keep
# these sets separate from recognition so audit output continues to expose any
# command added to the catalogue before its runtime implementation lands.
RUNTIME_VM_OPS = frozenset(VM_OPS)
RUNTIME_COMMANDS = frozenset(COMMANDS)


SOURCE_MARKER = re.compile(
    rb"(?i)(?:\b(?:setx|ifx|whilex)\b|\bx(?:"
    + b"|".join(re.escape(name[1:].encode("ascii")) for name in COMMANDS.values())
    + rb")\b)"
)


def source_code(source):
    """Drop comments and quoted literals before looking for command names."""
    lines = []
    for line in source.splitlines():
        line = line.split(b";", 1)[0]
        lines.append(re.sub(rb'"(?:[^"\\]|\\.)*"', b'""', line))
    return b"\n".join(lines)


def source_command_names(source):
    """Return public MWSE commands named by the script source."""
    source = source_code(source)
    found = set()
    for name in COMMANDS.values():
        if re.search(rb"(?i)\b" + re.escape(name.encode("ascii")) + rb"\b", source):
            found.add(name)
    return found


def opcode_info(opcode):
    if opcode in VM_OPS:
        name, operands = VM_OPS[opcode]
        return name, operands, "vm"
    if opcode in COMMANDS:
        return COMMANDS[opcode], 0, "command"
    return None


def scan_bytecode(data):
    """Return recognized MWSE instructions, excluding bytes consumed as operands."""
    instructions = []
    offset = 0
    while offset + 2 <= len(data):
        opcode = struct.unpack_from("<H", data, offset)[0]
        info = opcode_info(opcode)
        if not info:
            offset += 1
            continue
        name, operands, kind = info
        end = offset + 2 + operands
        if end > len(data):
            offset += 1
            continue
        operand = data[offset + 2:end]
        instructions.append({
            "offset": offset,
            "opcode": opcode,
            "name": name,
            "kind": kind,
            "operand": operand.hex(),
            "runtime_supported": (
                opcode in RUNTIME_VM_OPS if kind == "vm" else opcode in RUNTIME_COMMANDS
            ),
        })
        offset = end
    return instructions


def corroborated_instructions(data, source):
    """Remove byte-pair collisions in vanilla operands and string data.

    MWSE instructions are embedded in the ordinary TES3 instruction stream.  Public
    command names survive in SCTX, and VM setup instructions occur in contiguous runs.
    Those two facts let the audit remain conservative without having to emulate every
    variable-width retail command parser.
    """
    raw = scan_bytecode(data)
    named = source_command_names(source)
    plausible = [i for i in raw if i["kind"] == "vm" or i["name"] in named]
    keep = []
    for index, inst in enumerate(plausible):
        if inst["kind"] == "command":
            keep.append(inst)
            continue
        start = inst["offset"]
        end = start + 2 + len(inst["operand"]) // 2
        adjacent = (
            (index and plausible[index - 1]["offset"]
             + 2 + len(plausible[index - 1]["operand"]) // 2 == start)
            or (index + 1 < len(plausible) and plausible[index + 1]["offset"] == end)
        )
        if adjacent:
            keep.append(inst)
    return keep


def audit_plugin(path):
    scripts = []
    all_vm = set()
    all_commands = set()
    unsupported_vm = set()
    unsupported_commands = set()
    for tag, _flags, body in records(path):
        if tag != b"SCPT":
            continue
        subs = dict(subrecords(body))
        head = subs.get(b"SCHD", b"")
        data = subs.get(b"SCDT", b"")
        source = subs.get(b"SCTX", b"")
        instructions = corroborated_instructions(data, source)
        marked = bool(SOURCE_MARKER.search(source_code(source)))
        # A lone byte-pair collision in vanilla data is not useful evidence. Source text or
        # at least two decoded instructions makes a script a compatibility candidate.
        if not marked and len(instructions) < 2:
            continue
        name = head[:32].split(b"\0")[0].decode("latin-1", "replace")
        vm = sorted({i["name"] for i in instructions if i["kind"] == "vm"})
        commands = sorted({i["name"] for i in instructions if i["kind"] == "command"})
        all_vm.update(vm)
        all_commands.update(commands)
        unsupported_vm.update(
            i["name"] for i in instructions
            if i["kind"] == "vm" and not i["runtime_supported"]
        )
        unsupported_commands.update(
            i["name"] for i in instructions
            if i["kind"] == "command" and not i["runtime_supported"]
        )
        scripts.append({
            "name": name,
            "source_marked": marked,
            "instruction_count": len(instructions),
            "vm": vm,
            "commands": commands,
            "instructions": instructions,
        })
    return {
        "plugin": os.fspath(path),
        "scripts": scripts,
        "vm": sorted(all_vm),
        "commands": sorted(all_commands),
        "unsupported_vm": sorted(unsupported_vm),
        "unsupported_commands": sorted(unsupported_commands),
    }


def print_report(report, show_instructions=False):
    print(report["plugin"])
    if not report["scripts"]:
        print("  no legacy MWSE bytecode found")
        return
    print("  %d script(s), %d VM operation(s), %d command(s)" % (
        len(report["scripts"]), len(report["vm"]), len(report["commands"])))
    for script in report["scripts"]:
        marker = "source+bytecode" if script["source_marked"] else "bytecode candidate"
        print("  %s: %d instruction(s), %s" %
              (script["name"], script["instruction_count"], marker))
        if show_instructions:
            for inst in script["instructions"]:
                operand = (" " + inst["operand"]) if inst["operand"] else ""
                print("    %04X  %04X  %-20s%s" %
                      (inst["offset"], inst["opcode"], inst["name"], operand))
    if report["vm"]:
        print("  VM: " + ", ".join(report["vm"]))
    if report["commands"]:
        print("  commands: " + ", ".join(report["commands"]))
    unsupported = report["unsupported_vm"] + report["unsupported_commands"]
    if unsupported:
        print("  runtime unsupported: " + ", ".join(unsupported))
    else:
        print("  runtime: all decoded operations supported")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plugin", nargs="+", help="ESP or ESM to inspect")
    ap.add_argument("--instructions", action="store_true", help="list every decoded instruction")
    ap.add_argument("--json", action="store_true", help="write machine-readable results")
    args = ap.parse_args()

    reports = [audit_plugin(path) for path in args.plugin]
    if args.json:
        json.dump(reports[0] if len(reports) == 1 else reports, sys.stdout, indent=2)
        print()
        return
    for i, report in enumerate(reports):
        if i:
            print()
        print_report(report, args.instructions)


if __name__ == "__main__":
    main()
