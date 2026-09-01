# `mempalace.hub_client`

Source: [`mempalace/hub_client.py`](https://github.com/techempower-org/mempalace/blob/main/mempalace/hub_client.py)

Small authenticated client for forwarding JSON-RPC to a live Palace hub.

## Functions

### `discover_hub`

```python
def discover_hub(palace_path: str | None) -> tuple[str, dict[str, str]] | None
```

Return the authenticated endpoint for another live hub, if available.

### `forward_json_rpc`

```python
def forward_json_rpc(base_url: str, headers: Mapping[str, str], request: Mapping, *, timeout: float = HUB_PROXY_TIMEOUT_S)
```

POST one JSON-RPC request to the hub; return ``None`` for an empty body.
