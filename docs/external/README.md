# `docs/external/` — Provenance-Snapshots externer Tool-Integrationen

Dieser Ordner hält **eingefrorene Abhängigkeits-Snapshots** von externen Tools, die KAI zur Laufzeit ansteuert, aber die nicht als Python-Dependency in `pyproject.toml` stehen.

## Was hier reingehört
- **Lockfiles** (`package-lock.json`, `yarn.lock`, `Cargo.lock`) externer Integrationen, von denen KAI abhängt (z.B. MCP-Server, CLI-Tools, Chrome-Extensions)
- **ADR-Quellmaterial**: Upstream-Schemas, API-Contracts, Spec-Fragmente, die im Repo dokumentiert sein müssen, damit Rebuilds reproduzierbar sind
- **Upstream-Version-Pinning-Dokumentation**: welche Commit-SHA / Release-Tag wir für eine externe Integration als „bekannt gut" markieren

## Was **nicht** hier reingehört
- Voller Source-Tree einer externen Integration — der gehört ins Quell-Repo, nicht ins konsumierende. Hier nur **Metadaten**.
- Per-Host-Configs (z.B. `.mcp.json` mit absoluten Pfaden) — die bleiben außerhalb des Repos (`.gitignore`).
- Secrets, Tokens, Keys — egal ob upstream oder downstream.

## Naming-Convention
`<tool-name>.<artifact>.<ext>`, z.B.:
- `tradingview-mcp-jackson.lock.json` — npm-Lockfile des TradingView-MCP-Servers (Port 9222 CDP-Bridge, siehe DECISION_LOG D-175)
- `<future>.adr.md` — wenn ein externes Tool-Upgrade einen eigenen ADR braucht

## Warum das dokumentiert wird
Ohne diese Snapshots ist nicht mehr nachvollziehbar, **gegen welche Version einer externen Integration KAI ursprünglich getestet/deployt** wurde. Upstream-Projekte löschen oder umschreiben Tags/Releases; ein Lockfile-Snapshot im KAI-Repo ist die einzige belastbare Provenance-Quelle.

Grundsatz: **wenn KAI gegen etwas Externes integriert, muss die Version dieses Externen im KAI-Repo nachschlagbar sein** — ohne auf einen Dritten angewiesen zu sein.
