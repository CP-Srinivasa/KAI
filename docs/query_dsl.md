# Query DSL Reference

The AI Analyst Trading Bot supports a Boolean query DSL for filtering documents.
Use it in API `POST /documents/search`, CLI `query validate`, and watchlist definitions.

## Syntax

### Simple Term
Matches any document containing the word anywhere in title, body, or metadata.
```
bitcoin
```

### Exact Phrase
Wrap in double quotes. Matches the full phrase verbatim (case-insensitive).
```
"Tim Cook"
"nft marketplace"
```

### Exclude Terms
Prefix with `-` or use `NOT` to exclude documents containing the term.
```
cryptocurrency -bitcoin
cryptocurrency NOT bitcoin
```

### AND (explicit and implicit)
Documents must contain **both** terms. `AND` is the default when terms are adjacent.
```
bitcoin AND defi
blockchain crypto          ← implicit AND
```

### OR
Documents must contain **at least one** of the terms.
```
ethereum OR bitcoin
ethereum OR bitcoin OR tether
```

### Grouping with Parentheses
Use `(...)` to control operator precedence.
```
(bitcoin OR ethereum) AND regulation
cryptocurrency NOT (ethereum OR bitcoin OR tether)
(gamestop AND wallstreetbets) NOT wallstreet
```

### Field-Specific Search
Search only within a specific field.

| Prefix     | Searches in     |
|------------|-----------------|
| `qintitle:` | Document title  |
| `intitle:`  | Document title  |
| `inurl:`    | Document URL    |
| `site:`     | Document URL    |

```
qintitle:"nft marketplace"
intitle:bitcoin
inurl:coindesk
```

### Combined Example
```
(bitcoin OR ethereum) AND (SEC OR regulation OR ETF) NOT scam
```

## Supported Operators (precedence low → high)
1. `OR`
2. `AND` (explicit or implicit)
3. `NOT` / `-` (unary negation)
4. Grouping `()`
5. Atoms: `TERM`, `"PHRASE"`, `field:value`

## Validation
Use the CLI or API to validate a query before using it in production:

```bash
trading-bot query validate 'bitcoin AND (DeFi OR "smart contract") NOT scam'
```

API:
```http
POST /query/validate
{"query_text": "bitcoin AND defi"}
```

## Implementation Notes
- Parser: `app/core/query/parser.py` — recursive-descent, pure Python
- AST nodes: `NodeType.AND`, `OR`, `NOT`, `TERM`, `PHRASE`, `FIELD`, `GROUP`
- Executor: `app/core/query/executor.py` — in-memory evaluation against `CanonicalDocument`
- DB translation: not yet implemented — in-memory filtering applied after SQL pre-fetch
