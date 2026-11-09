import json, os, ssl, sys
from urllib.parse import quote

# --- Trust lab / self-signed certificates (see README disclaimer) ---
_unverified = ssl._create_unverified_context
ssl.create_default_context = lambda *a, **k: _unverified()
ssl._create_default_https_context = lambda *a, **k: _unverified()
try:
    import requests, urllib3
    urllib3.disable_warnings()
    _request = requests.Session.request
    # verify=False for lab certificates; a 10s connect timeout so a blocked network path (missing
    # network policy, wrong port) fails fast instead of hanging. Read timeout stays generous.
    requests.Session.request = lambda self, *a, **k: _request(
        self, *a, **{"timeout": (10, 900), **k, "verify": False})
except Exception:
    pass


def log(msg):
    print(f"BOOT: {msg}", flush=True)


def bindings():
    for label, items in json.loads(os.environ.get("VCAP_SERVICES", "{}")).items():
        for svc in items:
            yield label, svc, (svc.get("credentials") or {})


def tagged(label, svc, *words):
    hay = (label + " " + " ".join(svc.get("tags") or [])).lower()
    return any(w in hay for w in words)


def list_models(base, key):
    import urllib.request
    req = urllib.request.Request(f"{base}/models", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=20, context=_unverified()) as r:
        return [m["id"] for m in json.load(r).get("data", []) if m.get("id")]


def configure():
    env = os.environ
    for p in ("/home/vcap/tmp/tiktoken", "/home/vcap/tmp/hf", "/home/vcap/tmp/cache"):
        try:
            os.makedirs(p, exist_ok=True)
        except OSError as e:
            log(f"could not create {p}: {e}")

    # Secrets (CredHub / user-provided service). webui_secret_key -> WEBUI_SECRET_KEY, and any
    # UPPER_CASE key is exported as that env var, e.g. TERMINAL_SERVER_CONNECTIONS with its API key.
    for label, svc, c in bindings():
        if c.get("webui_secret_key"):
            env["WEBUI_SECRET_KEY"] = c["webui_secret_key"]
            log(f"WEBUI_SECRET_KEY loaded from {svc['name']}")
        if label in ("credhub", "user-provided") or tagged(label, svc, "credhub"):
            for k, v in c.items():
                if k.isupper() and k.replace("_", "").isalnum():
                    env[k] = v if isinstance(v, str) else json.dumps(v)
                    log(f"{k} loaded from {svc['name']}")

    # Postgres -> application DB and pgvector store
    for _, svc, c in bindings():
        for u in (c.get("jdbcUrl"), c.get("uri"), c.get("url")):
            if u and "postgres" in u and not u.startswith("http"):
                u = u.replace("jdbc:", "", 1).replace("postgres://", "postgresql://", 1)
                env["DATABASE_URL"] = env["PGVECTOR_DB_URL"] = u
                log(f"Postgres: {svc['name']}")
                break

    # GenAI / ai-models -> OpenAI-compatible connections + RAG embedding model
    urls, keys, configs, embed = [], [], {}, None
    for label, svc, c in bindings():
        if not tagged(label, svc, "genai", "ai-models", "llm"):
            continue
        ep = c.get("endpoint") or c
        base = (ep.get("api_base") or ep.get("uri") or "").rstrip("/")
        if not base:
            continue
        key = ep.get("api_key") or ep.get("api_token") or c.get("api_key") or "sk-unused"
        base += "/v1" if base.endswith("/openai") else ("" if "/v1" in base else "/openai/v1")
        try:
            models = list_models(base, key)
        except Exception as e:
            models = []
            log(f"{svc['name']}: could not list models ({e})")
        declared = c.get("model_name") or ep.get("model")
        if declared and declared not in models:
            models.append(declared)
        embeds = [m for m in models if "embed" in m.lower()]
        chats = [m for m in models if m not in embeds]
        if embeds and not embed:
            embed = (base, key, embeds[0])
        if chats or not models:
            configs[str(len(urls))] = {"enable": True, "model_ids": chats}
            urls.append(base)
            keys.append(key)
        log(f"{svc['name']}: chat={chats or 'all'} embed={embeds}")
    if urls:
        env["OPENAI_API_BASE_URLS"] = ";".join(urls)
        env["OPENAI_API_KEYS"] = ";".join(keys)
        env["OPENAI_API_CONFIGS"] = json.dumps(configs)
    if embed:
        env["RAG_OPENAI_API_BASE_URL"], env["RAG_OPENAI_API_KEY"], env["RAG_EMBEDDING_MODEL"] = embed
        log(f"RAG embedding model: {embed[2]}")
    elif urls:
        env["RAG_OPENAI_API_BASE_URL"], env["RAG_OPENAI_API_KEY"] = urls[0], keys[0]
        log("No embedding model found in any binding; set RAG_EMBEDDING_MODEL manually")

    # Redis / Valkey -> shared websocket and cache state (required for instances > 1)
    for label, svc, c in bindings():
        if not tagged(label, svc, "redis", "valkey"):
            continue
        url = c.get("uri") or c.get("url")
        if not url and c.get("host"):
            url = f"redis://:{quote(str(c.get('password', '')), safe='')}@{c['host']}:{c.get('port', 6379)}/0"
        if url:
            env["REDIS_URL"] = url
            env["WEBSOCKET_MANAGER"] = "redis"
            log(f"Redis: {svc['name']} (websocket manager = redis)")
            break

    # Volume services (block-storage / nfs / smb) -> persistent DATA_DIR for uploads
    if not env.get("DATA_DIR"):
        for _, svc, _c in bindings():
            for vm in svc.get("volume_mounts") or []:
                d = vm.get("container_dir")
                if d and vm.get("mode", "rw") != "r":
                    env["DATA_DIR"] = d
                    log(f"DATA_DIR on volume {svc['name']}: {d}")
                    break
            if env.get("DATA_DIR"):
                break

    # Tanzu SSO (p-identity / UAA) -> OIDC
    for label, svc, c in bindings():
        if "p-identity" in label or "sso" in label or tagged(label, svc, "sso", "identity"):
            d = c.get("auth_domain", "")
            d = d if d.startswith("http") else f"https://{d}"
            env.update({
                "ENABLE_OAUTH_SIGNUP": "True",
                "OAUTH_MERGE_ACCOUNTS_BY_EMAIL": "True",
                "OAUTH_PROVIDER_NAME": "Tanzu SSO",
                "OAUTH_CLIENT_ID": c["client_id"],
                "OAUTH_CLIENT_SECRET": c["client_secret"],
                "OPENID_PROVIDER_URL": f"{d.rstrip('/')}/.well-known/openid-configuration",
            })
            log(f"SSO: {svc['name']}")
            break


def check_tika():
    """Log whether Tika answers, so a missing network policy shows up at boot, not on upload."""
    url = os.environ.get("TIKA_SERVER_URL", "")
    if os.environ.get("CONTENT_EXTRACTION_ENGINE", "").lower() != "tika" or not url:
        return
    import urllib.request
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/version", timeout=5) as r:
            log(f"Tika OK at {url}: {r.read().decode().strip()}")
    except Exception as e:
        log(f"Tika NOT reachable at {url}: {e!r}. Check that the Tika app is running and that "
            f"'cf add-network-policy <this-app> <tika-app> --port 9998 --protocol tcp' exists.")


def migrate():
    """Run Open WebUI's startup migrations under a Postgres advisory lock.

    Open WebUI migrates the schema when open_webui.config is first imported, with no locking.
    When several instances start at once (cf push, cf restart, crash recovery) they would race.
    The lock makes them take turns; the second instance finds the schema current and moves on.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgres"):
        import open_webui.config  # noqa: F401
        return
    import sqlalchemy as sa
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    with engine.connect() as conn:
        log("waiting for migration lock")
        conn.execute(sa.text("SELECT pg_advisory_lock(7426110)"))
        try:
            log("migration lock acquired")
            import open_webui.config  # noqa: F401  (runs alembic upgrade head)
        finally:
            conn.execute(sa.text("SELECT pg_advisory_unlock(7426110)"))
            log("migration lock released")
    engine.dispose()


if __name__ == "__main__":
    log(f"instance {os.environ.get('CF_INSTANCE_INDEX', '?')} starting")
    configure()
    check_tika()
    migrate()
    import uvicorn
    uvicorn.run("open_webui.main:app", host="0.0.0.0", port=int(os.environ.get("PORT", 8080)),
                forwarded_allow_ips="*", ws_per_message_deflate=True)
