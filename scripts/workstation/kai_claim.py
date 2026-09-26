# Quelle: Repo scripts/workstation/kai_claim.py (MindBlow 2.0, W3). Hier NICHT editieren --
# im Repo aendern, dann: pwsh -File scripts/workstation/install_workstation.ps1 -Apply
"""Claims im Register ACTIVE_CLAIMS.md anlegen, schliessen, pruefen — ohne zweite Wahrheit.

Die Markdown-Tabelle in ``KAI-mirror/ACTIVE_CLAIMS.md`` bleibt die EINZIGE Quelle;
wer von Hand eintraegt, tut das weiter. Dieses Werkzeug schreibt dieselben Zeilen
nur verlaesslich (Spaltenzahl, Zeitstempel, Lease 24 h) und prueft vor einem
Worktree/PR, ob Pfade einen fremden aktiven Claim beruehren. Anlass 26.09.2026:
rund zehn Einweg-Skripte fuer Claim-Zeilen an einem Tag, Kollisionspruefung nur
von Hand, acht abgelaufene Claims bis zurueck zum 11.07.

    python kai_claim.py list
    python kai_claim.py check app/alerts/health_check_host.py scripts/workstation/ --owner bin-ea
    python kai_claim.py add --id 20260927-bin-ea-x --owner "claude bin-ea" \\
        --where "C:/tmp/kai-x / claude/x" --scope "app/foo.py, tests/unit/test_foo.py: ..."
    python kai_claim.py close --id 20260927-bin-ea-x --note "#1234 gemergt abcd1234"
    python kai_claim.py expire            # Regel (2): abgelaufene Leases als expired markieren

Exit: 0 ok, 1 Kollision (check/add), 2 Bedienfehler oder abgelehnt.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import re
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

DEFAULT_FILE = Path(r"C:\Users\sasch\KAI-mirror\ACTIVE_CLAIMS.md")
OPEN_STATES = frozenset({"active", "open", "blocked", "paused", "uebergeben"})
LEASE = timedelta(hours=24)  # Regel (2) des Registers: Lease max. 24 h
_EXT = r"(?:py|sh|ps1|md|json|jsonl|txt|toml|ya?ml|service|timer|xml|conf|cfg|ini|lock)"
_PATH_TOKEN = re.compile(
    r"[A-Za-z0-9_.*{}\-]+(?:/[A-Za-z0-9_.*{},\-]+)+/?"  # mit Verzeichnis
    rf"|\b[A-Za-z0-9_.\-]+\.{_EXT}\b"  # nackter Dateiname
)


@dataclass(frozen=True)
class Claim:
    index: int  # Zeilennummer (0-basiert) in der Datei
    claim_id: str
    owner: str
    where: str
    scope: str
    created: str
    expires: str
    status: str

    @property
    def state(self) -> str:
        return self.status.replace("*", "").split(" ")[0].lower().rstrip(".,:")

    @property
    def is_open(self) -> bool:
        return self.state in OPEN_STATES

    def expiry(self) -> datetime | None:
        """``expires_at``; fehlt es, gilt ``created_at`` + 24 h (Regel 2)."""
        end = parse_ts(self.expires)
        if end is None and (start := parse_ts(self.created)) is not None:
            end = start + LEASE
        return end

    def is_live(self, now: datetime) -> bool:
        end = self.expiry()
        return self.is_open and (end is None or end >= now)

    def paths(self) -> list[str]:
        return [_norm(t.rstrip(".,;:")) for t in _PATH_TOKEN.findall(self.scope)]


def parse_ts(raw: str) -> datetime | None:
    try:
        ts = datetime.fromisoformat(raw.strip().strip("`"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


def _norm(path: str) -> str:
    p = path.replace("\\", "/")
    return p[2:] if p.startswith("./") else p


def parse(text: str) -> list[Claim]:
    """Alle Tabellenzeilen. Ein ``|`` im Freitext verschiebt nur die Mitte, nie die Raender."""
    claims = []
    for index, line in enumerate(text.splitlines()):
        if not line.startswith("|") or line.startswith("|---") or "claim_id" in line:
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 7:
            continue
        claims.append(
            Claim(
                index=index,
                claim_id=cells[0],
                owner=cells[1],
                where=cells[2],
                scope=" | ".join(cells[3:-3]),
                created=cells[-3],
                expires=cells[-2],
                status=cells[-1],
            )
        )
    return claims


def split_live(claims: Sequence[Claim], now: datetime) -> tuple[list[Claim], list[Claim]]:
    """(aktive, abgelaufene-aber-nie-geschlossene)."""
    live = [c for c in claims if c.is_live(now)]
    stale = [c for c in claims if c.is_open and not c.is_live(now)]
    return live, stale


def _expand_braces(pattern: str) -> list[str]:
    m = re.search(r"\{([^{}]*)\}", pattern)
    if not m:
        return [pattern]
    head, tail = pattern[: m.start()], pattern[m.end() :]
    return [e for part in m.group(1).split(",") for e in _expand_braces(head + part + tail)]


def overlaps(path: str, token: str) -> bool:
    """Beruehrt ``path`` den Claim-Pfad ``token`` (gleich, darin, darueber, Muster, Dateiname)?"""
    p, t = _norm(path).rstrip("/"), _norm(token).rstrip("/")
    if not p or not t:
        return False
    for pattern in _expand_braces(t):
        if any(ch in pattern for ch in "*?["):
            if fnmatch.fnmatch(p, pattern) or fnmatch.fnmatch(p, pattern + "/*"):
                return True
            continue
        if p == pattern or p.startswith(pattern + "/") or pattern.startswith(p + "/"):
            return True
        if "/" not in pattern and p.rsplit("/", 1)[-1] == pattern:
            return True
    return False


def collisions(
    claims: Sequence[Claim], paths: Sequence[str], now: datetime, owner: str | None = None
) -> list[tuple[Claim, str, str]]:
    """(Claim, gepruefter Pfad, Claim-Pfad) fuer jeden aktiven FREMDEN Claim, der beruehrt wird."""
    hits = []
    live, _ = split_live(claims, now)
    for claim in live:
        if owner and owner.lower() in claim.owner.lower():
            continue
        for token in claim.paths():
            for path in paths:
                if overlaps(path, token):
                    hits.append((claim, path, token))
    return hits


@contextmanager
def _locked(path: Path, *, wait_s: float = 10.0, stale_s: float = 120.0) -> Iterator[None]:
    """Exklusiver Schreibzugriff ueber eine Sperrdatei; eine verwaiste Sperre verfaellt."""
    lock = path.with_name(path.name + ".lock")
    deadline = time.monotonic() + wait_s
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_s:
                    lock.unlink(missing_ok=True)
                    continue
            except FileNotFoundError:
                continue
            if time.monotonic() > deadline:
                raise TimeoutError(f"Register gesperrt: {lock}") from None
            time.sleep(0.1)
    try:
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
        yield
    finally:
        lock.unlink(missing_ok=True)


def _read(path: Path) -> list[str]:
    with path.open(encoding="utf-8", newline="") as fh:
        return fh.read().splitlines(keepends=True)


def _write(path: Path, lines: list[str]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as fh:
        fh.write("".join(lines))
    os.replace(tmp, path)


def _eol(lines: Sequence[str]) -> str:
    return "\r\n" if lines and lines[0].endswith("\r\n") else "\n"


def _cell(text: str) -> str:
    return " ".join(text.replace("|", "/").split())


def _now() -> datetime:
    return datetime.now(UTC).replace(second=0, microsecond=0)


def cmd_add(path: Path, a: argparse.Namespace) -> int:
    now = _now()
    with _locked(path):
        lines = _read(path)
        claims = parse("".join(lines))
        if any(c.claim_id == a.id for c in claims):
            print(f"ABGELEHNT: claim_id {a.id} existiert schon", file=sys.stderr)
            return 2
        new_paths = Claim(0, a.id, a.owner, a.where, a.scope, "", "", "active").paths()
        hits = collisions(claims, new_paths, now, owner=a.owner)
        if hits and not a.force:
            _print_hits(hits)
            print("ABGELEHNT: Ueberschneidung — erst koordinieren (oder --force)", file=sys.stderr)
            return 1
        end = now + timedelta(hours=a.hours)
        row = (
            f"| {_cell(a.id)} | {_cell(a.owner)} | {_cell(a.where)} | {_cell(a.scope)} | "
            f"{now:%Y-%m-%dT%H:%MZ} | {end:%Y-%m-%dT%H:%MZ} | active |{_eol(lines)}"
        )
        sep = next((i for i, line in enumerate(lines) if line.startswith("|---")), None)
        if sep is None:
            print("ABGELEHNT: keine Tabelle im Register", file=sys.stderr)
            return 2
        lines.insert(sep + 1, row)
        _write(path, lines)
    print(f"CLAIM {a.id} aktiv bis {end:%Y-%m-%dT%H:%MZ} ({len(new_paths)} Pfad(e) erfasst)")
    return 0


def _set_status(path: Path, claim_id: str, status: str, *, only_open: bool = True) -> bool:
    lines = _read(path)
    for claim in parse("".join(lines)):
        if claim.claim_id != claim_id or (only_open and not claim.is_open):
            continue
        raw = lines[claim.index]
        eol = "\r\n" if raw.endswith("\r\n") else "\n"
        body = raw.rstrip("\r\n").rstrip()
        cut = body.rstrip("|").rstrip().rfind("|")
        lines[claim.index] = f"{body[: cut + 1]} {_cell(status)} |{eol}"
        _write(path, lines)
        return True
    return False


def cmd_close(path: Path, a: argparse.Namespace) -> int:
    with _locked(path):
        ok = _set_status(path, a.id, f"closed {_now():%Y-%m-%dT%H:%MZ} — {a.note}")
    if not ok:
        print(f"ABGELEHNT: kein offener Claim {a.id}", file=sys.stderr)
        return 2
    print(f"CLAIM {a.id} geschlossen")
    return 0


def cmd_expire(path: Path, a: argparse.Namespace) -> int:
    now = _now()
    with _locked(path):
        _, stale = split_live(parse(path.read_text(encoding="utf-8")), now)
        for claim in stale:
            if not a.dry_run:
                note = f"expired (Lease abgelaufen, markiert {now:%Y-%m-%dT%H:%MZ}) — vorher: "
                _set_status(path, claim.claim_id, note + claim.status)
            print(f"{'WUERDE markieren' if a.dry_run else 'EXPIRED'}: {claim.claim_id}")
    return 0


def cmd_list(path: Path, a: argparse.Namespace) -> int:
    live, stale = split_live(parse(path.read_text(encoding="utf-8")), _now())
    for c in live:
        print(f"{c.claim_id} [{c.owner}] {c.state} bis {c.expires or '—'}")
    print(
        f"{len(live)} aktiv" + (f", {len(stale)} abgelaufen (kai_claim.py expire)" if stale else "")
    )
    return 0


def _print_hits(hits: Sequence[tuple[Claim, str, str]]) -> None:
    for claim, path, token in hits:
        print(f"KOLLISION: {path} beruehrt {token} — {claim.claim_id} [{claim.owner}]")


def cmd_check(path: Path, a: argparse.Namespace) -> int:
    hits = collisions(parse(path.read_text(encoding="utf-8")), a.paths, _now(), owner=a.owner)
    if hits:
        _print_hits(hits)
        return 1
    print(f"frei: {len(a.paths)} Pfad(e), kein aktiver fremder Claim beruehrt")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kai_claim.py", description=__doc__.split("\n")[0])
    parser.add_argument(
        "--file", type=Path, default=Path(os.environ.get("KAI_CLAIMS_FILE", DEFAULT_FILE))
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add", help="Claim anlegen (prueft vorher auf Ueberschneidung)")
    add.add_argument("--id", required=True)
    add.add_argument("--owner", required=True)
    add.add_argument("--where", required=True, help="Worktree / Branch oder OPS-Ort")
    add.add_argument(
        "--scope", required=True, help="Pfade + Thema; Pfade werden fuer check erkannt"
    )
    add.add_argument("--hours", type=float, default=24.0)
    add.add_argument("--force", action="store_true", help="trotz Ueberschneidung anlegen")
    close = sub.add_parser("close", help="offenen Claim schliessen")
    close.add_argument("--id", required=True)
    close.add_argument("--note", required=True)
    expire = sub.add_parser("expire", help="abgelaufene Leases als expired markieren (Regel 2)")
    expire.add_argument("--dry-run", action="store_true")
    sub.add_parser("list", help="aktive Claims")
    check = sub.add_parser("check", help="beruehren Pfade einen aktiven fremden Claim?")
    check.add_argument("paths", nargs="+")
    check.add_argument("--owner", help="eigener Owner-Name; eigene Claims zaehlen nicht")
    a = parser.parse_args(argv)
    if not a.file.is_file():
        print(f"Register fehlt: {a.file}", file=sys.stderr)
        return 2
    handlers = {
        "add": cmd_add,
        "close": cmd_close,
        "expire": cmd_expire,
        "list": cmd_list,
        "check": cmd_check,
    }
    try:
        return handlers[a.cmd](a.file, a)
    except TimeoutError as exc:
        print(f"ABGELEHNT: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
