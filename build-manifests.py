#!/usr/bin/env python3
"""Regenerate manifest.yml, manifest-production.yml and manifest-tika.yml.

boot.py is the source of truth for the startup logic. It is embedded into the two Open WebUI
manifests as a zlib+base64 payload because Cloud Foundry limits a process command to 4096
characters. Run this after editing boot.py or the env blocks below:  python3 build-manifests.py
"""
import os, sys, textwrap
here = os.path.dirname(os.path.abspath(__file__))
boot = open(sys.argv[1] if len(sys.argv) > 1 else os.path.join(here, "boot.py")).read()
repo = sys.argv[2] if len(sys.argv) > 2 else here
import base64, zlib, textwrap
import ast


def minify(src):
    """Drop comments and docstrings (ast round-trip) so the payload fits in 4096 characters."""
    tree = ast.parse(src)
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if (isinstance(node, (ast.Module, ast.FunctionDef)) and body and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str)):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree)


b64 = base64.b64encode(zlib.compress(minify(boot).encode(), 9)).decode()
# Folded YAML scalar: base64 may be wrapped because b64decode() discards whitespace.
wrapped = textwrap.wrap(b64, 96)
command_block = (
    "    # boot.py, zlib+base64 encoded so the command stays under Cloud Foundry's 4096-character\n"
    "    # limit. Source of truth: boot.py in this repo. Regenerate with: python3 build-manifests.py\n"
    "    command: >-\n"
    "      python3 -c \"import base64,zlib;open('boot.py','wb').write(zlib.decompress(base64.b64decode('\n"
    + "\n".join("      " + l for l in wrapped) + "\n"
    "      ')))\" && exec python3 boot.py"
)
COMMON_ENV = '''\
      # --- Logging ---
      LOG_LEVEL: "INFO"
      PYTHONUNBUFFERED: "1"

      # --- Transport ---
      # "True" = browsers use a pure WebSocket, so no sticky sessions are needed behind gorouter.
      # "False" = HTTP long-polling, only safe with ONE instance (polling needs session affinity).
      ENABLE_WEBSOCKET_SUPPORT: "{WS}"

      # --- Air-gapped / offline ---
      OFFLINE_MODE: "true"            # no version-update check, no HuggingFace Hub downloads
      HF_HUB_OFFLINE: "1"
      TRANSFORMERS_OFFLINE: "1"
      ENABLE_OLLAMA_API: "False"
      OLLAMA_BASE_URL: ""
      TMPDIR: "/home/vcap/tmp"
      TIKTOKEN_CACHE_DIR: "/home/vcap/tmp/tiktoken"
      HF_HOME: "/home/vcap/tmp/hf"

      # --- Authentication ---
      WEBUI_AUTH: "True"
{SECRET}
      WEBUI_NAME: "Tanzu Platform"
      ENABLE_SIGNUP: "False"          # no local email/password accounts; SSO accounts are still created (boot.py sets ENABLE_OAUTH_SIGNUP)
      ENABLE_LOGIN_FORM: "False"      # hide the email/password form, leaving only the "Continue with Tanzu SSO" button
      DEFAULT_USER_ROLE: "pending"    # later SSO sign-ins wait for admin approval (first account becomes admin)
      # Display name = the "name" claim (UAA builds it from given_name + family_name of the
      # identity provider). Refreshed on every login, so existing users get fixed on next sign-in.
      OAUTH_USERNAME_CLAIM: "name"
      OAUTH_UPDATE_NAME_ON_LOGIN: "True"
      OAUTH_SCOPES: "openid"
      # Settings come from this manifest on every start. Admin Settings changes are NOT saved and,
      # with several instances, apply only to the instance that served the request. Change them here.
      ENABLE_PERSISTENT_CONFIG: "False"

      # --- TLS: trust lab / self-signed certificates (boot.py patches ssl as well) ---
      REQUESTS_VERIFY: "False"
      AIOHTTP_CLIENT_SESSION_SSL: "False"

      # --- RAG (embedding endpoint + model are discovered from the ai-models binding at boot) ---
      VECTOR_DB: "pgvector"
      RAG_EMBEDDING_ENGINE: "openai"
      RAG_EMBEDDING_BATCH_SIZE: "{BATCH}"
      RAG_EMBEDDING_CONCURRENT_REQUESTS: "2"      # parallel embedding calls per upload; more does not help, the GPU service is the limit

      # --- RTL/Hebrew PDF support via Apache Tika (push manifest-tika.yml first, see README) ---
      # Needs:  cf add-network-policy open-webui tika-server --port 9998 --protocol tcp
      # boot.py logs "BOOT: Tika OK" or "BOOT: Tika NOT reachable" at startup.
      # There is NO fallback: if Tika is unreachable, PDF/Office uploads fail. Remove these three
      # lines to use the built-in extractor instead.
      CONTENT_EXTRACTION_ENGINE: "tika"
      TIKA_SERVER_URL: "http://tika-server.apps.internal:9998"
      TIKA_SERVER_VERSION: "4"

      # --- Hebrew-optimized RAG settings ---
      RAG_TEXT_SPLITTER: "character"              # better for Hebrew than token-based
      CHUNK_OVERLAP: "200"
      RAG_TOP_K: "5"
      ENABLE_RAG_HYBRID_SEARCH: "True"            # keyword + semantic
      ENABLE_RAG_HYBRID_SEARCH_ENRICHED_TEXTS: "True"

      # --- Open Terminal (optional) ---
      # Pre-configured terminal servers, visible to everyone when access_grants is "*" (omit
      # access_grants for admin-only). Uncomment and fill in the URL and key (see README).
      # TERMINAL_SERVER_CONNECTIONS: '[{"id":"ops","name":"Ops box","url":"https://terminal.example.com","auth_type":"bearer","key":"...","config":{"access_grants":[{"principal_type":"user","principal_id":"*","permission":"read"}]}}]'

      # --- Keep chat titles in the user's language ---
      TITLE_GENERATION_PROMPT_TEMPLATE: "Generate a concise chat title in the SAME LANGUAGE as the user's first message. Do not translate to English."
'''

EMBED_BATCH = "8"    # chunks per embedding request; throughput is set by the GPU service, small batches keep each request short
COMMON_ENV = COMMON_ENV.replace("{BATCH}", EMBED_BATCH)

HEADER = '''\
# Open WebUI on Tanzu Platform for Cloud Foundry
#
# Pinned to Open WebUI v0.13.2 (released 2026-11-08). To upgrade: back up Postgres, change the
# image tag and `cf push`. Migrations are forward-only. Never upgrade with --strategy rolling:
# old and new versions cannot share a database (boot.py serializes migrations with a lock).
#
# boot.py (below) reads VCAP_SERVICES and wires up Postgres/pgvector, every ai-models binding
# (chat + embedding models discovered from /v1/models), Redis/Valkey, volume mounts and Tanzu SSO.
'''

IMAGE = "ghcr.io/open-webui/open-webui:v0.13.2"
HEALTH = '''    health-check-type: http
    health-check-http-endpoint: /health
    timeout: 300          # first start runs migrations and copies static assets'''

main = HEADER + f'''\
applications:
  - name: open-webui
    docker:
      image: {IMAGE}
    instances: 1          # for more than one instance use manifest-production.yml
    memory: 3G
    disk_quota: 8G        # the image alone is about 4.6 GB
{HEALTH}
    services:
      - qwen3.5-27b       # ai-models / tanzu-all-models: chat + embedding models in one binding
      - sso               # p-identity (UAA)
      - owui-postgres     # postgres: app DB + pgvector vector store
    env:
''' + COMMON_ENV.replace("{WS}", "False").replace("{SECRET}",
'''      # Signs login sessions. Replace with a long random string; changing it later logs everyone out.
      WEBUI_SECRET_KEY: "tanzu-secret-key"''') + '''
''' + command_block + "\n"

production = HEADER + f'''\
#
# Production variant: several instances behind gorouter. Create every service below first
# (commands in README, "Production deployment"), push manifest-tika.yml, then this file, then:
#   cf add-network-policy open-webui tika-server --port 9998 --protocol tcp
applications:
  - name: open-webui
    docker:
      image: {IMAGE}
    instances: 2
    memory: 3G
    disk_quota: 8G
{HEALTH}
    services:
      - owui-ai           # ai-models / tanzu-all-models: chat + embedding models
      - owui-sso          # p-identity (UAA)
      - owui-postgres     # postgres: app DB + pgvector vector store
      - owui-redis        # p.redis: websocket fan-out, shared locks and caches across instances
      - name: owui-smb    # smb volume: uploaded files, shared by every instance (DATA_DIR)
        parameters:       # used on the first bind only; to change them: cf unbind-service, then cf push
          username: "svc-openwebui"
          password: "CHANGE-ME"
          domain: "CORP"            # remove if the account is not in a Windows domain
          mount: "/home/vcap/data"
    env:
''' + COMMON_ENV.replace("{WS}", "True").replace("{SECRET}",
'''      # Signs login sessions. Must be identical on every instance (it is, since they all read this file).
      # Replace with a long random string; changing it later logs everyone out.
      WEBUI_SECRET_KEY: "CHANGE-ME-to-a-long-random-string"''') + '''
      # --- Multi-instance ---
      # REDIS_URL and WEBSOCKET_MANAGER=redis are set by boot.py from the owui-redis binding.
      # DATA_DIR is set by boot.py to the owui-smb mount path, so uploads are shared.
      WEBSOCKET_REDIS_OPTIONS: '{"retry_on_timeout": true, "health_check_interval": 30}'
      REDIS_SOCKET_KEEPALIVE: "True"
      UVICORN_WORKERS: "1"          # scale with instances, not workers

''' + command_block + "\n"

tika = '''\
# Apache Tika server for Open WebUI (text extraction for RTL languages such as Hebrew and Arabic).
# Reached only over container-to-container networking: internal route, no public route.
#
# Tika listens on port 9998, fixed below so it always matches the network policy:
#   cf add-network-policy open-webui tika-server --port 9998 --protocol tcp
# Tika 4 returns Markdown, so Open WebUI needs TIKA_SERVER_VERSION: "4" (set in its manifest).
applications:
  - name: tika-server
    docker:
      image: apache/tika:4.2.0-full
    instances: 2          # apps.internal resolves to every instance, so this gives redundancy
    memory: 4G            # Tika runs a pool of child JVMs; 2G peaked at 98% under 8 parallel PDFs
    disk_quota: 4G
    health-check-type: http
    health-check-http-endpoint: /version
    timeout: 180
    routes:
      - route: tika-server.apps.internal
    command: java -cp "/opt/tika-server/*:/opt/tika-server/lib/*:/tika-extras/*" org.apache.tika.server.core.TikaServerCli -h 0.0.0.0 -p 9998
'''

# Cloud Foundry rejects a process command longer than 4096 characters. A folded scalar joins the
# wrapped lines with single spaces, so measure exactly what the Cloud Controller will receive.
cmd_len = len(" ".join(l.strip() for l in command_block.splitlines() if not l.strip().startswith("#"))
              .replace("command: >- ", "", 1))
print(f"process command: {cmd_len} of 4096 characters")
if cmd_len > 4096:
    sys.exit("boot.py is too large for a Cloud Foundry command; shorten it")

for name, body in (("manifest.yml", main), ("manifest-production.yml", production), ("manifest-tika.yml", tika)):
    open(f"{repo}/{name}", "w").write(body)
    print("wrote", name, len(body.splitlines()), "lines")
