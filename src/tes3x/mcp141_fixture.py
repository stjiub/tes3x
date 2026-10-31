"""Build isolated compiled-script probes for the MCP-141 parser defects."""

from pathlib import Path
import argparse
import struct


def record(tag, subrecords):
    body = b"".join(name + struct.pack("<I", len(value)) + value
                    for name, value in subrecords)
    return tag + struct.pack("<III", len(body), 0, 0) + body


def op(value):
    return struct.pack("<H", value)


def name(value):
    data = value.encode("ascii")
    if len(data) > 255:
        raise ValueError("name is too long")
    return bytes([len(data)]) + data


def target(value):
    return b"G" + name(value)


def local(index):
    return b"s" + struct.pack("<H", index)


def set_value(destination, expression):
    if len(expression) > 255:
        raise ValueError("expression is too long")
    return op(0x0105) + destination + bytes([len(expression)]) + expression


def random_expression(limit):
    digits = str(limit).encode("ascii")
    # The Construction Set writes the u16 parameter over the first digit and
    # keeps the rest of the literal in the compiled expression.
    return b" X" + op(0x1021) + struct.pack("<H", limit) + digits[1:]


def script(script_name, statements, lines, short_count=0):
    code = b"".join(statements) + op(0x0101)
    variable_names = [f"v{i:02d}" for i in range(1, short_count + 1)]
    scvr = b"".join(v.encode("ascii") + b"\0" for v in variable_names)
    head = (script_name.encode("ascii").ljust(32, b"\0")
            + struct.pack("<5I", short_count, 0, 0, len(code), len(scvr)))
    subs = [(b"SCHD", head)]
    if scvr:
        subs.append((b"SCVR", scvr))
    source_lines = [*(f"short {v}" for v in variable_names), *lines]
    subs.extend(((b"SCDT", code),
                 (b"SCTX", (f"Begin {script_name}\r\n" + "\r\n".join(source_lines)
                            + "\r\nEnd\r\n").encode("ascii"))))
    return record(b"SCPT", subs)


def global_var(var_name, initial):
    return record(b"GLOB", [(b"NAME", var_name.encode("ascii") + b"\0"),
                            (b"FNAM", b"f"),
                            (b"FLTV", struct.pack("<f", initial))])


def plugin(master):
    result = target("tes3x141Result")
    input_value = b" G" + name("tes3x141Input") + b"\0"
    long_expression = b" 3" + b" 0 +" * 17
    assert len(long_expression) == 70
    short_expression = b" 2" + b" 0 +" * 15
    assert len(short_expression) == 62
    medium_expression = b" 1" + b" 0 +" * 9
    assert len(medium_expression) == 38
    local_34_condition = b" " + local(34) + b" == 7"
    if_short = b" 1" + b" + 0" * 14 + b" == 1"
    if_long = b" 1" + b" + 0" * 15 + b" == 1"
    assert len(if_short) == 63 and len(if_long) == 67

    def conditional(script_name, condition, answer, source):
        return script(script_name, [op(0x0106) + b"\x01" + bytes([len(condition)])
                                    + condition, set_value(result, f" {answer}".encode()),
                                    op(0x0109)],
                      [f"If ({source})", f"Set tes3x141Result to {answer}", "EndIf"])
    probes = [
        script("tes3x141R256", [set_value(result, random_expression(256))],
               ["Set tes3x141Result to Random 256"]),
        script("tes3x141R257", [set_value(result, random_expression(257))],
               ["Set tes3x141Result to Random 257"]),
        script("tes3x141L34", [set_value(local(34), b" 7"),
                                set_value(result, b" " + local(34) + b" 1 +")],
               ["Set v34 to 7",
                "Set tes3x141Result to v34 + 1"], 34),
        script("tes3x141L34If", [set_value(local(34), b" 7"),
                                  op(0x0106) + b"\x01" + bytes([len(local_34_condition)])
                                  + local_34_condition,
                                  set_value(result, b" 1"), op(0x0109)],
               ["Set v34 to 7", "If (v34 == 7)",
                "Set tes3x141Result to 1", "EndIf"], 34),
        script("tes3x141L65G", [set_value(local(65), input_value),
                                 set_value(result, b" " + local(65))],
               ["Set v65 to tes3x141Input",
                "Set tes3x141Result to v65"], 84),
        script("tes3x141L84G", [set_value(local(84), input_value),
                                 set_value(result, b" " + local(84))],
               ["Set v84 to tes3x141Input",
                "Set tes3x141Result to v84"], 84),
        script("tes3x141Len70", [set_value(result, long_expression)],
               ["Set tes3x141Result to 3" + " + 0" * 17]),
        script("tes3x141Len62", [set_value(result, short_expression)],
               ["Set tes3x141Result to 2" + " + 0" * 15]),
        script("tes3x141Len38", [set_value(result, medium_expression)],
               ["Set tes3x141Result to 1" + " + 0" * 9]),
        script("tes3x141Len6", [set_value(result, b" 6 0 +")],
               ["Set tes3x141Result to 6 + 0"]),
        script("tes3x141Global", [set_value(result, input_value)],
               ["Set tes3x141Result to tes3x141Input"]),
        script("tes3x141Reset", [set_value(result, b" 9999")],
               ["Set tes3x141Result to 9999"]),
        conditional("tes3x141If7", b" 1 == 1", 17, "1 == 1"),
        conditional("tes3x141If63", if_short, 63, "1" + " + 0" * 14 + " == 1"),
        conditional("tes3x141If67", if_long, 67, "1" + " + 0" * 15 + " == 1"),
    ]
    hedr = (struct.pack("<fI", 1.3, 0) + b"TES3X".ljust(32, b"\0")
            + b"MCP-141 parser probes".ljust(256, b"\0")
            + struct.pack("<I", 2 + len(probes)))
    header = record(b"TES3", [(b"HEDR", hedr),
                             (b"MAST", b"Morrowind.esm\0"),
                             (b"DATA", struct.pack("<Q", master.stat().st_size))])
    return b"".join([header, global_var("tes3x141Input", 13),
                     global_var("tes3x141Result", -1), *probes])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("master", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_bytes(plugin(args.master))
    print(args.output)


if __name__ == "__main__":
    main()
