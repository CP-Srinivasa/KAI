"""Wurzel-ID und Rechte von lnd-Macaroons zeigen/pruefen (nur Identifier, nie die Signatur).

mac_ops.py DATEI...                     zeigen
mac_ops.py --expect WURZEL OPS DATEI    pruefen (Exit 1 bei Abweichung)
    WURZEL: Dezimalzahl, oder "account" (litd-Account: Hex-Praefix ffeeddcc)
    OPS:    wie die Anzeige, z. B. "info:read invoices:read,write"
"""

import sys

ACCOUNT_PREFIX = 0xFFEEDDCC


def varint(b, i):
    shift = result = 0
    while True:
        byte = b[i]
        i += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, i
        shift += 7


def identifier(raw):
    if raw[0] != 2:
        raise ValueError("kein V2-Macaroon")
    i = 1
    while True:
        ftype, i = varint(raw, i)
        if ftype == 0:
            raise ValueError("kein Identifier")
        n, i = varint(raw, i)
        data, i = raw[i : i + n], i + n
        if ftype == 2:
            return data


def fields(pb):
    i = 0
    while i < len(pb):
        key, i = varint(pb, i)
        n, i = varint(pb, i)
        yield key >> 3, pb[i : i + n]
        i += n


def parse(path):
    with open(path, "rb") as fh:
        ident = identifier(fh.read())
    if ident[0] != 3:
        raise ValueError(f"Identifier-Version {ident[0]} (kein lnd-v3)")
    root, ops = "?", []
    for num, val in fields(ident[1:]):
        if num == 2:
            root = val.decode()
        elif num == 3:
            entity, actions = "", []
            for n2, v2 in fields(val):
                if n2 == 1:
                    entity = v2.decode()
                elif n2 == 2:
                    actions.append(v2.decode())
            ops.append(f"{entity}:{','.join(actions)}")
    return root, " ".join(sorted(ops))


def main():
    args = sys.argv[1:]
    if len(args) == 4 and args[0] == "--expect":
        want_root, want_ops, path = args[1], args[2], args[3]
        try:
            root, ops = parse(path)
        except Exception as exc:  # noqa: BLE001
            print(f"{path}: FEHLER {exc}")
            return 1
        if want_root == "account":
            ok_root = root.isdigit() and int(root) >> 32 == ACCOUNT_PREFIX
        else:
            ok_root = root == want_root
        ok = ok_root and ops == want_ops
        print(
            f"{'OK' if ok else 'ABWEICHUNG'} {path.rsplit('/', 1)[-1]}: root_key_id={root} ops={ops}"
        )
        return 0 if ok else 1
    if not args or args[0].startswith("-"):
        print(__doc__)
        return 64
    rc = 0
    for p in args:
        try:
            root, ops = parse(p)
            print(f"{p}: root_key_id={root} ops={ops}")
        except Exception as exc:  # noqa: BLE001
            print(f"{p}: FEHLER {exc}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
