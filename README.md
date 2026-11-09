# Open WebUI on Tanzu Platform (OCI Edition)

This repository contains a **zero-dependency, single-file manifest** for deploying [Open WebUI](https://github.com/open-webui/open-webui) on **Tanzu Platform** (Cloud Foundry).

It is specifically designed for enterprise environments where simplicity, speed, and air-gapped compatibility are priorities.

**Pinned version:** Open WebUI `v0.13.2` (2026-11-08) and Apache Tika `4.2.0`.

| File | Use it for |
| --- | --- |
| `manifest.yml` | One instance. Simplest setup, uploads on container disk. |
| `manifest-production.yml` | Two or more instances: Redis/Valkey and an SMB share for uploads. |
| `manifest-tika.yml` | The Tika text-extraction server both of the above use. |

Deploying into an air-gapped network? Follow **[AIRGAP-GUIDE.md](AIRGAP-GUIDE.md)**, a step-by-step command list for Windows cmd.

![Setup Plan](Setup%20plan.png)

---

## ⚠️ Disclaimer & Warnings
**Please read carefully before deploying.**

1. **Community Project:** This is a community contribution and is **NOT** an official VMware/Broadcom product. There is no official support SLA.
2. **Security Overrides:** This manifest disables TLS verification for outbound calls (`REQUESTS_VERIFY`, `AIOHTTP_CLIENT_SESSION_SSL` and an `ssl` monkey-patch in `boot.py`) so it works with lab certificates. In strict production environments, build a derived image with your CA bundle instead of disabling verification.
3. **Use at Your Own Risk:** You are responsible for ensuring this deployment complies with your organization's security and data privacy policies.

---

## 🎯 Deployment Strategy: Why the Docker Approach?
Tanzu Platform is famous for its powerful **Buildpacks**, which is the gold standard for building custom applications from source.

However, Open WebUI is a massive, complex, off-the-shelf product with thousands of dependencies. For this specific use case, using the **Official Docker Image** offers distinct advantages over compiling from source:

1. **🚀 Instant Deployments:** The Python Buildpack must compile Open WebUI from scratch, which can take significant time due to the project's size. The Docker image is pre-compiled and optimized by the vendor, booting almost immediately.
2. **📦 Air-Gap Simplicity:** "Vendoring" (caching) the thousands of Python libraries required by Open WebUI for an offline environment is a complex task involving cross-platform compatibility (Linux vs Mac/Windows). The Docker approach encapsulates everything into a single, portable artifact that "just works" offline.
3. **✅ Vendor Alignment:** You are running the exact binary released by the Open WebUI team, ensuring 1:1 parity with their documentation and releases without build-process interference.

**In short:** We use the Python Buildpack for code we write, and the Docker Image for products we simply want to run.

The image tag is **pinned** (`ghcr.io/open-webui/open-webui:v0.13.2`) rather than tracking `main`, so every `cf push` is reproducible and upgrades are a deliberate, reviewed change.

**Editing the boot script:** the startup logic lives in `boot.py`. Cloud Foundry limits a process `command` to 4096 characters, so the manifests carry it as a zlib+base64 payload that is decoded into `boot.py` at container start. After changing `boot.py` (or the env blocks in `build-manifests.py`), run `python3 build-manifests.py` to regenerate all three manifests. The script strips comments from the embedded copy and refuses to write a command longer than 4096 characters. Inside a running container, `cat /app/backend/boot.py` shows the decoded script.

---

## ✨ Key Features
This manifest adds the "Enterprise Glue" that the raw Docker image lacks:

* **🔌 Intelligent Auto-Discovery:** A Python boot script (injected at runtime) scans **all** bound services in your Tanzu space:
    * **AI Models:** Every `ai-models` binding (tagged `genai`) becomes an OpenAI-compatible connection. The script calls the endpoint's `/v1/models` at boot, so multi-model plans such as `tanzu-all-models` expose all their chat models in the dropdown.
    * **Embedding Models:** Any model whose name contains `embed` (for example `nomic-ai/nomic-embed-text-v2-moe`) is automatically configured as the **RAG** embedding model and hidden from the chat picker, enabling "Chat with Docs" without manual setup.
    * **Postgres, Redis, SSO, volumes:** Wired up from their bindings. Database, Redis and model credentials never appear in the manifest.
* **🔒 Safe multi-instance startup:** Open WebUI migrates its database on startup without any locking. `boot.py` wraps that step in a Postgres advisory lock, so instances that start together take turns instead of racing.
* **🩺 Tika check at boot:** `boot.py` calls Tika once at startup and logs `BOOT: Tika OK` or `BOOT: Tika NOT reachable`, so a missing network policy shows up in the logs instead of on the first upload.
* **📈 Horizontal Scalability:** Shared Postgres with pgvector instead of local SQLite. `manifest-production.yml` adds Redis, WebSockets and a shared SMB share (see [Production](#-production-deployment-multi-instance)).
* **🛡️ Firewall Compatible:** The single-instance `manifest.yml` uses **long-polling**, which works behind firewalls (F5, Palo Alto) that strip WebSocket headers. The production manifest needs WebSockets end to end.
* **🔑 Seamless SSO:** Pre-configured for Tanzu SSO / UAA. Users are shown by their full name (`name` claim), refreshed on every login.
* **🌐 Air-Gapped Ready:** `OFFLINE_MODE`, `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` prevent network calls to external services, including the version-update check.
* **📄 RTL Language Support:** Support for RTL languages like Hebrew and Arabic by integrating an external Tika 4 server.

---

## 🛠️ Prerequisites
Before deploying, ensure you have the necessary services in your Tanzu Space. The service names in `manifest.yml` must match yours; edit the `services:` block if you use different names.

### 1. Create an AI Models service (Chat & Embed)
One multi-model binding gives you every model on the plan, including the embedding model:

```bash
cf create-service ai-models tanzu-all-models qwen3.5-27b
```

Single-model plans also work, and you can bind several. The boot script discovers the models in each one:

```bash
cf create-service ai-models tanzu-gpt-oss-120b chat
cf create-service ai-models tanzu-nomic-embed-text-v2 embed
```

> Older GenAI tiles used the offering name `genai`. The script matches on the `genai` tag, so both naming schemes work.

### 2. Create a Database
Required for user management, chat history and as the RAG vector store (pgvector).

```bash
cf create-service postgres on-demand-postgres-db owui-postgres
```

### 3. Create SSO Service (Optional)
If you want Single Sign-On via your platform's UAA.

```bash
cf create-service p-identity uaa sso
```

---

## ⚙️ Configuration Guide
The `manifest.yml` uses environment variables to control behavior. Because `ENABLE_PERSISTENT_CONFIG` is `"False"`, these values win over the admin UI on every restart.

| Variable | Default | Description |
| --- | --- | --- |
| **`ENABLE_WEBSOCKET_SUPPORT`** | `"False"` / `"True"` | `"False"` in `manifest.yml`: long-polling, works behind firewalls that strip WebSocket headers, one instance only. `"True"` in `manifest-production.yml`: required for more than one instance. |
| **`ENABLE_SIGNUP`** | `"False"` | No local email/password accounts. SSO users are still created because `boot.py` enables `ENABLE_OAUTH_SIGNUP` whenever an SSO binding exists. |
| **`ENABLE_LOGIN_FORM`** | `"False"` | Hides the email/password form so the login page shows only **Continue with Tanzu SSO**. Set both to `"True"` if you deploy without an SSO binding, otherwise nobody can log in. |
| **`DEFAULT_USER_ROLE`** | `"pending"` | The first account becomes admin. Every later sign-up waits in **Admin Panel → Users** until an admin approves it. Set to `"user"` to auto-approve. |
| **`OAUTH_USERNAME_CLAIM`** | `"name"` | Display name. UAA builds `name` from the identity provider's first and last name. See [Display names](#-display-names-from-sso). |
| **`OAUTH_UPDATE_NAME_ON_LOGIN`** | `"True"` | Refreshes the display name on every login, which also fixes users created earlier. |
| **`ENABLE_PERSISTENT_CONFIG`** | `"False"` | The manifest is the source of truth on every start. Changes in **Admin Settings** are not saved, and with several instances they apply only to the instance that handled the click. Change settings in the manifest. |
| **`RAG_EMBEDDING_BATCH_SIZE`** / **`RAG_EMBEDDING_CONCURRENT_REQUESTS`** | `"8"` / `"2"` | Chunks per embedding call and calls in parallel. Embeddings always run on the platform's GPU embedding model. See [Slow uploads](#-slow-uploads-and-timeouts). |
| **`OFFLINE_MODE`** | `"true"` | Disables the GitHub version check and HuggingFace downloads. Required for air-gapped environments. |
| **`ENABLE_OLLAMA_API`** | `"False"` | Disables local Ollama discovery. Stops connection errors in cloud deployments. |
| **`WEBUI_SECRET_KEY`** | `"CHANGE-ME..."` | Signs login sessions. **Replace it with a long random string** before the first push. Every instance reads the same manifest, so they share it. Changing it later logs every user out. |
| **`CONTENT_EXTRACTION_ENGINE`** | `"tika"` | Sends PDF/Office uploads to the Tika sidecar. **No fallback:** if Tika is not deployed, those uploads fail. Remove the three Tika lines to use the built-in extractor. |

---

## 🚀 Deployment Steps
1. **Clone** this repository.
2. **Edit** the `services:` block in `manifest.yml` to match your service names, and change `WEBUI_SECRET_KEY`.
3. **Deploy Tika first** if you keep the Tika settings (see below), or remove them.
4. **Push** to Tanzu:

```bash
cf push
```

5. **Log in** with the SSO user who should become admin. The first account is automatically the admin.

### Connecting New Models
You can add new models at any time without changing code or redeploying:

1. **Bind:** `cf bind-service open-webui my-new-model`
2. **Restart:** `cf restart open-webui`
3. **Result:** The boot script re-scans, lists the endpoint's models, and they appear in the dropdown.

### Upgrading Open WebUI
1. Back up the Postgres database. Migrations run forward-only on first boot of a newer version.
2. Change the image tag in the manifest to the new release.
3. Push **without** `--strategy rolling`. A plain `cf push` stops every old instance before starting new ones, which Open WebUI requires: its release notes state that old and new versions cannot share a database during an upgrade.

```bash
cf push -f manifest-production.yml
cf logs open-webui --recent | grep "BOOT: migration"
```

One instance runs the migrations while the others log `waiting for migration lock`, then they start normally.

---

## 📦 Air-Gapped Transfer

Save both images on a connected machine as **linux/amd64** (Diego cells are x86_64, so on Apple Silicon the `--platform` flag is mandatory):

```bash
docker pull --platform linux/amd64 ghcr.io/open-webui/open-webui:v0.13.2
docker pull --platform linux/amd64 apache/tika:4.2.0-full
docker save -o openwebui-v0.13.2.tar ghcr.io/open-webui/open-webui:v0.13.2
docker save -o tika-server-4.2.0.tar apache/tika:4.2.0-full
shasum -a 256 openwebui-v0.13.2.tar tika-server-4.2.0.tar > IMAGES.sha256
```

Inside the air-gapped network, load, retag and push them to your private registry, then point the manifests at it:

```bash
shasum -a 256 -c IMAGES.sha256
docker load -i openwebui-v0.13.2.tar
docker load -i tika-server-4.2.0.tar
docker tag ghcr.io/open-webui/open-webui:v0.13.2 registry.internal.example.com/open-webui/open-webui:v0.13.2
docker tag apache/tika:4.2.0-full registry.internal.example.com/apache/tika:4.2.0-full
docker push registry.internal.example.com/open-webui/open-webui:v0.13.2
docker push registry.internal.example.com/apache/tika:4.2.0-full
```

Then change `docker.image` in `manifest.yml`, `manifest-production.yml` and `manifest-tika.yml` to the private registry paths. For a registry that needs credentials, set `CF_DOCKER_PASSWORD` and push with `cf push --docker-username <user>`.

---

## 🌍 RTL/Hebrew PDF Support (Optional)

For improved text extraction from Hebrew, Arabic, and other RTL documents, deploy the Apache Tika sidecar. `manifest.yml` is pre-configured to use it and tells Open WebUI to expect Tika 4 output (`TIKA_SERVER_VERSION: "4"`).

### 1. Deploy Tika Server

```bash
cf push -f manifest-tika.yml
```

Tika listens on port **9998**. The manifest fixes that port explicitly, health-checks `/version` on it, and gives the app only an internal route, `tika-server.apps.internal`. It runs two instances with 4 GB each: Tika 4 runs a pool of child JVMs, and in testing 2 GB peaked at 98% with eight large PDFs in parallel. The internal route resolves to every Tika instance, so one can fail without breaking uploads.

### 2. Enable Container-to-Container Networking

Run this once. Nothing in a manifest can create it.

```bash
cf add-network-policy open-webui tika-server --port 9998 --protocol tcp
cf network-policies --source open-webui
```

The policy is tied to the app's internal ID, not its name. If you `cf delete` either app and push it again, the policy is gone and must be added again. A normal `cf push` over an existing app keeps it.

### 3. Verify

Restart Open WebUI and read the boot check:

```bash
cf restart open-webui
cf logs open-webui --recent | grep "BOOT: Tika"
```

| Boot message | Meaning |
| --- | --- |
| `Tika OK at ...: Apache Tika 4.2.0` | Everything is wired correctly. |
| `Connection refused` or `timed out` | No network policy, a policy on the wrong port, or Tika is down. Check `cf network-policies` and `cf app tika-server`. |
| `Name or service not known` | Wrong internal route or domain. Compare `cf app tika-server` with `TIKA_SERVER_URL`. |

Each instance runs the check, so with several instances you get one line per instance. If you do not want Tika, delete `CONTENT_EXTRACTION_ENGINE`, `TIKA_SERVER_URL` and `TIKA_SERVER_VERSION` from the manifest; Open WebUI's built-in extractor handles PDFs, just with weaker RTL support.

---

## 🏢 Production Deployment (Multi-Instance)

`manifest-production.yml` runs two instances behind the gorouter. It was tested on Tanzu Platform with two Open WebUI instances, two Tika instances, Postgres, Valkey and the platform's GPU embedding model. A file uploaded through one instance was extracted, embedded and answered correctly through the other. Live chat events from one instance reached a browser connected to the other.

### What each piece is for

| Service | Why several instances need it |
| --- | --- |
| Postgres (`owui-postgres`) | Users, chats, settings, file records and pgvector embeddings. Shared by every instance. |
| Redis/Valkey (`owui-redis`) | Live WebSocket events, locks and caches. Without it, a reply produced on one instance never reaches a browser connected to another. |
| SMB share (`owui-smb`) | The uploaded files themselves. Without it, each file exists only on the instance that received it, and downloads or re-indexing through another instance fail with 404. This was reproduced in testing. |

Embeddings live in Postgres, so chat-with-documents keeps working without the share. The share is what lets users open or download an uploaded file and lets admins re-index documents.

### 1. Create the services

```bash
cf create-service ai-models tanzu-all-models owui-ai
cf create-service p-identity uaa owui-sso
cf create-service postgres on-demand-postgres-db owui-postgres
cf create-service p.redis on-demand-cache owui-redis
cf create-service smb Existing owui-smb -c '{"share":"//fileserver.corp.local/openwebui","version":"3.0"}'
```

Valkey high availability depends on what your platform's Valkey tile offers. The binding in testing exposed one host, port and password, which `boot.py` handles. If your plan exposes Sentinel instead, set `REDIS_SENTINEL_HOSTS` and `REDIS_SENTINEL_PORT` in the manifest.

On Windows, quoting JSON on the command line is fragile. Put the JSON in a file and pass the file name instead, for example `-c smb.json`.

### 2. Edit the manifest

In `manifest-production.yml`, fill in:

* `WEBUI_SECRET_KEY`: a long random string, for example the output of `openssl rand -hex 32`.
* The `owui-smb` parameters: the SMB account's `username`, `password` and `domain`. Remove `domain` if the account is not in a Windows domain. Keep `mount` as is.

The SMB parameters are used when `cf push` first binds the share. A later change to them has no effect until you run `cf unbind-service open-webui owui-smb` and push again. `boot.py` finds the mount and sets `DATA_DIR` to it.

### 3. Deploy

```bash
cf push -f manifest-tika.yml
cf push -f manifest-production.yml
cf add-network-policy open-webui tika-server --port 9998 --protocol tcp
cf restart open-webui
```

The restart makes the Tika check at boot run with the policy in place. It is only needed on the first deployment, since the policy stays for later pushes.

### 4. Check the boot log

```bash
cf logs open-webui --recent | grep BOOT:
```

Each instance should report Postgres, the models, `Redis: owui-redis`, `DATA_DIR on volume owui-smb` and `Tika OK`.

### Autoscaling (optional)

```bash
cf create-service app-autoscaler standard owui-autoscaler
cf bind-service open-webui owui-autoscaler
cf update-service owui-autoscaler -c '{"instance_limits":{"min":2,"max":6},"scaling_rules":[{"metric_type":"cpu","threshold":{"min":20,"max":70},"adjustment":"+1"}]}'
```

### Block storage

The `block-storage` offering attaches a disk to a single container. It cannot be shared, so it only fits `manifest.yml` with one instance. Add it to the `services:` list and `boot.py` sets `DATA_DIR` to its mount.

---

## 👤 Display Names from SSO

Open WebUI shows a user's display name from one claim, set by `OAUTH_USERNAME_CLAIM`. The manifests use `name`, which UAA builds from the first and last name of the identity provider. `user_name` is the login name, which in many companies is an employee number.

* If names still show as numbers after the next login, the SSO plan is not receiving names from your identity provider. In the SSO plan's identity provider settings, map `given_name` and `family_name` to the directory attributes, typically `givenName` and `sn` for Active Directory.
* `OAUTH_UPDATE_NAME_ON_LOGIN` fixes existing users the next time they log in. There is no need to recreate them.
* Open WebUI cannot combine two claims, so `given_name` alone would show the first name only.

This was not tested end to end, because the lab test could not log in through SSO interactively.

---

## 💻 Open Terminal Connections

Admins can pre-configure Open Terminal servers through `TERMINAL_SERVER_CONNECTIONS`, a JSON list. Each entry looks like this:

```json
[{"id": "ops", "name": "Ops box", "url": "https://terminal.corp.local", "auth_type": "bearer", "key": "<api key>",
  "config": {"access_grants": [{"principal_type": "user", "principal_id": "*", "permission": "read"}]}}]
```

* `access_grants` with `principal_id` `*` makes the terminal available to every user. Leave `access_grants` out to keep it admin-only, or use `"principal_type": "group"` with a group ID.
* `auth_type` can be `bearer` with a fixed key, `session` to forward the user's Open WebUI token, `system_oauth` to forward the user's SSO token, or `none`.
* The URL points at an Open Terminal server running on the remote machine. Open WebUI does not SSH into machines itself.

The manifests contain a commented-out `TERMINAL_SERVER_CONNECTIONS` line. Uncomment it, fill in the URL and key on one line inside single quotes, and push.

---

## 🐢 Slow Uploads and Timeouts

Measured in the lab with a 1.4 MB, 400-section Hebrew PDF:

| Step | Time |
| --- | --- |
| Tika text extraction | about 5 seconds |
| Embedding 801 chunks on the platform GPU model | about 316 seconds |

The embedding service delivered a flat 2.2 to 2.5 chunks per second, whatever the batch size or parallelism. Larger batches only made each request slower: 64 chunks with 4 in parallel averaged 66 seconds per request. Open WebUI aborts any single embedding request after 300 seconds, so large batches under load can turn into a timeout. The manifests therefore send 8 chunks per request, 2 at a time.

If large documents are slow in your environment, the limit is the embedding service, not Open WebUI or Tika. Ask the platform team about the GPU capacity or rate limit of the embedding model plan. Uploads are processed in the background in v0.13.x, so the browser no longer waits on one long HTTP request.

---

## 🔍 Troubleshooting

* **Boot output:** `cf logs open-webui --recent | grep BOOT:` shows which services were wired up and which models were discovered.
* **Models missing from the dropdown:** the `/v1/models` call failed at boot (look for `could not list models`). Check the binding and TLS settings, then `cf restart open-webui`.
* **PDF upload fails or hangs:** run `cf logs open-webui --recent | grep "BOOT: Tika"` and use the table in [Verify](#3-verify). Without `cf ssh`, that boot line is the quickest test of container-to-container networking.
* **Uploaded file returns 404 with several instances:** the SMB share is not bound. Check for `DATA_DIR on volume` in the boot log.
* **Replies appear only after a page reload:** Redis is missing or `ENABLE_WEBSOCKET_SUPPORT` is `"False"` with more than one instance.
* **`Command must be between 1 and 4096 characters` on push:** `boot.py` grew too large. `build-manifests.py` checks this, so regenerate instead of editing the manifest by hand.
* **Users cannot log in after SSO:** they are waiting for approval. Approve them in **Admin Panel → Users**, or set `DEFAULT_USER_ROLE: "user"`.

---

## 📋 Manifest Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Instances | 1 / 2 | `manifest.yml` / `manifest-production.yml` |
| Memory | 3G | Each instance used about 650 MB idle in testing |
| Disk | 8G | The v0.13.2 image alone is about 4.6 GB |
| Health check | HTTP `/health` | Instances receive traffic only once the app answers |
| Start timeout | 300s | First start runs migrations |
| Tika | 2 × 4G | HTTP health check on `/version`, port 9998 |
