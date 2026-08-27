"""Read-only worktree and claim-lease hygiene report (STAB-12)."""

from __future__ import annotations

import argparse
import json
import subprocess
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

JsonDict = dict[str, Any]


def parse_worktrees(raw: str) -> list[JsonDict]:
    """Parse the stable porcelain format without depending on display columns."""
    records: list[JsonDict] = []
    current: JsonDict = {}
    for line in [*raw.splitlines(), ""]:
        if not line.strip():
            if current.get("path") and current.get("head"):
                current.setdefault("branch", None)
                current.setdefault("detached", current["branch"] is None)
                records.append(current)
            current = {}
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            current["path"] = value
        elif key == "HEAD":
            current["head"] = value
        elif key == "branch":
            current["branch"] = value.removeprefix("refs/heads/")
            current["detached"] = False
        elif key == "detached":
            current["detached"] = True
    return records


def _parse_time(raw: object) -> datetime | None:
    value = str(raw or "").strip().strip("`")
    if not value or value in {"—", "-"}:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def parse_claims(path: Path, *, now: datetime) -> list[JsonDict]:
    """Read the legacy Markdown ledger, including scopes containing bare pipes."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    claims: list[JsonDict] = []
    for line in lines:
        if not line.lstrip().startswith("|"):
            continue
        parts = [part.strip() for part in line.strip().strip("|").split("|")]
        if len(parts) < 7 or parts[0] in {"claim_id", "---"} or set(parts[0]) == {"-"}:
            continue
        claim_id, owner, worktree = parts[:3]
        scope = "|".join(parts[3:-3]).strip()
        created_raw, expires_raw, status_raw = parts[-3:]
        status = status_raw.split(" ", 1)[0].lower()
        expires = _parse_time(expires_raw)
        if status == "active":
            if expires is None:
                lease_state = "active_missing_expiry"
            elif expires <= now:
                lease_state = "active_expired"
            else:
                lease_state = "active_valid"
        elif status == "closed":
            lease_state = "closed"
        elif status == "expired":
            lease_state = "expired"
        else:
            lease_state = "other"
        claims.append(
            {
                "claim_id": claim_id,
                "owner_agent": owner,
                "worktree_branch": worktree,
                "scope": scope,
                "created_at": created_raw,
                "expires_at": expires_raw,
                "status": status_raw,
                "lease_state": lease_state,
            }
        )
    return claims


def _merge_status(
    branch: str | None, head: str, pr_index: dict[str, JsonDict], fallback: str
) -> tuple[str, str, int | None, str | None]:
    if branch and branch in pr_index:
        pr = pr_index[branch]
        state = str(pr.get("state", "")).upper()
        status = {"MERGED": "merged", "OPEN": "open", "CLOSED": "closed_unmerged"}.get(
            state, "unknown"
        )
        number = pr.get("number")
        url = pr.get("url")
        return (
            status,
            "github_pr",
            number if isinstance(number, int) else None,
            url if isinstance(url, str) else None,
        )
    return fallback, "git_ancestry", None, None


def build_report(
    *,
    worktrees: Sequence[JsonDict],
    claims: Sequence[JsonDict],
    now: datetime,
    commit_time: Callable[[str], int | None],
    ancestry_status: Callable[[str], str],
    pr_index: dict[str, JsonDict],
) -> JsonDict:
    items: list[JsonDict] = []
    worktree_counts = {
        "total": len(worktrees),
        "older_than_14d": 0,
        "older_than_30d": 0,
        "missing_paths": 0,
        "merged": 0,
        "open": 0,
        "unmerged": 0,
        "closed_unmerged": 0,
        "unknown": 0,
    }
    for raw in worktrees:
        head = str(raw.get("head", ""))
        branch = raw.get("branch") if isinstance(raw.get("branch"), str) else None
        timestamp = commit_time(head)
        age_days = None
        if timestamp is not None:
            age_days = round(max(0.0, now.timestamp() - timestamp) / 86_400, 1)
            worktree_counts["older_than_14d"] += int(age_days > 14)
            worktree_counts["older_than_30d"] += int(age_days > 30)
        path_exists = Path(str(raw.get("path", ""))).exists()
        worktree_counts["missing_paths"] += int(not path_exists)
        fallback = ancestry_status(head) if head else "unknown"
        status, source, pr_number, pr_url = _merge_status(branch, head, pr_index, fallback)
        worktree_counts[status if status in worktree_counts else "unknown"] += 1
        items.append(
            {
                **raw,
                "path_exists": path_exists,
                "head_committed_at_utc": (
                    datetime.fromtimestamp(timestamp, tz=UTC).isoformat() if timestamp else None
                ),
                "age_days_by_head_commit": age_days,
                "merge_status": status,
                "merge_status_source": source,
                "pr_number": pr_number,
                "pr_url": pr_url,
            }
        )

    claim_counts = dict.fromkeys(
        ("active_valid", "active_expired", "active_missing_expiry", "closed", "expired", "other"),
        0,
    )
    for claim in claims:
        state = str(claim.get("lease_state", "other"))
        claim_counts[state if state in claim_counts else "other"] += 1
    return {
        "schema_version": "worktree_claims_hygiene/v1",
        "generated_at_utc": now.astimezone(UTC).isoformat(),
        "read_only": True,
        "worktrees": {"counts": worktree_counts, "items": items},
        "claims": {"counts": claim_counts, "items": list(claims)},
    }


def _run(args: Sequence[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    """Ein Kommando ausfuehren; ein fehlendes Programm ist ein Ergebnis, kein Absturz.

    ``check=False`` faengt nur einen Fehler*code* ab. Ist die Binaerdatei gar nicht
    da, wirft ``subprocess.run`` ``FileNotFoundError`` — und der Aufrufer, der
    sauber auf ``returncode != 0`` zurueckfaellt, kommt nie dazu. Genau so brach
    ``kai audit worktree-claims-report`` auf dem Pi ab: dort ist ``gh`` nicht
    installiert, der git-ancestry-Rueckfall war gebaut, getestet und unerreichbar.
    """
    try:
        return subprocess.run(args, cwd=cwd, capture_output=True, text=True, check=False)
    except OSError as exc:
        return subprocess.CompletedProcess(list(args), 127, stdout="", stderr=str(exc))


def collect_report(repo: Path, claims_path: Path, *, base_ref: str, now: datetime) -> JsonDict:
    listed = _run(["git", "worktree", "list", "--porcelain"], cwd=repo)
    if listed.returncode != 0:
        raise RuntimeError(f"git worktree list failed: {listed.stderr.strip()}")

    def commit_time(head: str) -> int | None:
        result = _run(["git", "show", "-s", "--format=%ct", head], cwd=repo)
        try:
            return int(result.stdout.strip()) if result.returncode == 0 else None
        except ValueError:
            return None

    def ancestry_status(head: str) -> str:
        result = _run(["git", "merge-base", "--is-ancestor", head, base_ref], cwd=repo)
        return (
            "merged"
            if result.returncode == 0
            else "unmerged"
            if result.returncode == 1
            else "unknown"
        )

    pr_index: dict[str, JsonDict] = {}
    pr_result = _run(
        [
            "gh",
            "pr",
            "list",
            "--state",
            "all",
            "--limit",
            "1000",
            "--json",
            "headRefName,state,mergedAt,number,url",
        ],
        cwd=repo,
    )
    if pr_result.returncode == 0:
        try:
            for pr in json.loads(pr_result.stdout):
                pr["state"] = "MERGED" if pr.get("mergedAt") else pr.get("state")
                pr_index[str(pr.get("headRefName", ""))] = pr
        except (json.JSONDecodeError, TypeError):
            pr_index = {}
    return build_report(
        worktrees=parse_worktrees(listed.stdout),
        claims=parse_claims(claims_path, now=now),
        now=now,
        commit_time=commit_time,
        ancestry_status=ancestry_status,
        pr_index=pr_index,
    )


def render_json(report: JsonDict) -> str:
    """Portable JSON even when the caller's console uses a legacy code page."""
    return json.dumps(report, indent=2, ensure_ascii=True, allow_nan=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only worktree/claim hygiene report")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--claims", type=Path, default=Path.home() / "KAI-mirror/ACTIVE_CLAIMS.md")
    parser.add_argument("--base-ref", default="origin/claude/p7/reentry-ia-codex-cycle")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    report = collect_report(
        args.repo.resolve(), args.claims, base_ref=args.base_ref, now=datetime.now(UTC)
    )
    if args.json:
        print(render_json(report))
    else:
        wc, cc = report["worktrees"]["counts"], report["claims"]["counts"]
        print(
            f"worktrees={wc['total']} old>14d={wc['older_than_14d']} missing={wc['missing_paths']} "
            f"claims_expired={cc['active_expired']} "
            f"claims_missing_expiry={cc['active_missing_expiry']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
