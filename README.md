# Open WebUI on Tanzu Platform (OCI Edition)

This repository contains a **zero-dependency, single-file manifest** for deploying [Open WebUI](https://github.com/open-webui/open-webui) on **Tanzu Platform** (Cloud Foundry).

It is specifically designed for enterprise environments where simplicity, speed, and air-gapped compatibility are priorities.

---

## ⚠️ Disclaimer & Warnings
**Please read carefully before deploying.**

1. **Community Project:** This is a community contribution and is **NOT** an official VMware/Broadcom product. There is no official support SLA.
2. **Security Overrides:** This manifest includes settings to bypass SSL verification (e.g., `HTTP_CLIENT_VERIFY_SSL_CERT: "false"`). In strict production environments, you should properly inject trusted CA certificates into a new docker image based on the base image, instead of disabling verification.
3. **Use at Your Own Risk:** You are responsible for ensuring this deployment complies with your organization's security and data privacy policies.

---

## 🎯 Deployment Strategy: Why the Docker Approach?
Tanzu Platform is famous for its powerful **Python Buildpack**, which is the gold standard for building custom Python applications from source.

However, Open WebUI is a massive, complex, off-the-shelf product with thousands of dependencies. For this specific use case, using the **Official Docker Image** offers distinct advantages over compiling from source:

1. **🚀 Instant Deployments:** The Python Buildpack must compile Open WebUI from scratch, which can take significant time due to the project's size. The Docker image is pre-compiled and optimized by the vendor, booting almost immediately.
2. **📦 Air-Gap Simplicity:** "Vendoring" (caching) the thousands of Python libraries required by Open WebUI for an offline environment is a complex task involving cross-platform compatibility (Linux vs Mac/Windows). The Docker approach encapsulates everything into a single, portable artifact that "just works" offline.
3. **✅ Vendor Alignment:** You are running the exact binary released by the Open WebUI team, ensuring 1:1 parity with their documentation and releases without build-process interference.

**In short:** We use the Python Buildpack for code we write, and the Docker Image for products we simply want to run.

---

## ✨ Key Features
This manifest adds the "Enterprise Glue" that the raw Docker image lacks:

* **🔌 Intelligent Auto-Discovery:** A Python boot script (injected at runtime) automatically scans **all** bound services in your Tanzu space:
    * **Chat Models:** Aggregates text generation models (GPT-4, Mistral, Llama) into a single dropdown, enabling multi-model switching instantly.
    * **Embedding Models:** Automatically detects embedding services and dynamically configures the **RAG Engine** with the correct model name, enabling "Chat with Docs" without manual setup.
* **📈 Horizontal Scalability:** Unlike standard deployments that rely on local SQLite files (locking you to a single instance), this manifest configures a shared Postgres backend. This allows you to safely scale the application (`cf scale open-webui -i 5`) to handle hundreds of users and ensure High Availability (HA) without data conflicts.
* **🛡️ Firewall Compatible:** Works with strict corporate firewalls (F5, Palo Alto) that strip WebSocket headers by forcing standard **HTTP Streaming (SSE)**.
* **🔑 Seamless SSO:** Pre-configured for Tanzu SSO / UAA with correct scopes (`openid`) to prevent invalid token errors.
* **🌐 Air-Gapped Ready:** Includes `HF_HUB_OFFLINE` and `TRANSFORMERS_OFFLINE` settings to prevent network calls to external services.

---

## 🛠️ Prerequisites
Before deploying, ensure you have the necessary services in your Tanzu Space.

### 1. Create GenAI Services (Chat & Embed)
You can bind multiple models. The script will use all of them.

```bash
# Chat Model (e.g., Mistral, GPT)
cf create-service genai multi-gpt-oss-120b chat

# Embedding Model (Required for RAG/Docs)
cf create-service genai nomic-embed embed
```

### 2. Create a Database
Required for user management, chat history and functions as the RAG vector store.

```bash
cf create-service postgres on-demand-postgres-db owui-postgres
```

### 3. Create SSO Service (Optional)
If you want Single Sign-On via your platform's UAA.

```bash
cf create-service p-identity standard sso
```

---

## ⚙️ Configuration Guide
The `manifest.yml` uses environment variables to control behavior.

| Variable | Default | Description |
| --- | --- | --- |
| **`ENABLE_WEBSOCKET_SUPPORT`** | `"False"` | **Critical.** Disables WebSockets. Fixes "Unexpected token" errors behind corporate firewalls. Set to `"True"` if behind Cloudflare. |
| **`ENABLE_OLLAMA_API`** | `"False"` | Disables local Ollama search. Essential for cloud deployments to stop connection errors. |
| **`WHITELISTED_EMAILS`** | `admin` | Comma-separated list of usernames allowed to login. **Add your username here.** |
| **`ENABLE_SIGNUP`** | `"True"` | Allows account creation on first SSO login. |
| **`HF_HUB_OFFLINE`** | `"1"` | Prevents HuggingFace Hub downloads. Required for air-gapped environments. |
| **`TRANSFORMERS_OFFLINE`** | `"1"` | Prevents transformer model downloads. Required for air-gapped environments. |

---

## 🚀 Deployment Steps
1. **Download** the `manifest.yml` from this repository.
2. **Edit** the `WHITELISTED_EMAILS` to include your username.
3. **Push** to Tanzu:

```bash
cf push
```

### Connecting New Models
You can add new models at any time without changing code or redeploying:

1. **Bind:** `cf bind-service open-webui my-new-gpt4`
2. **Restart:** `cf restart open-webui`
3. **Result:** The boot script re-scans, finds the new credentials, and the model appears in the dropdown instantly.

---

## 🌍 RTL/Hebrew PDF Support (Optional)

For improved text extraction from Hebrew, Arabic, and other RTL documents, deploy the Apache Tika sidecar:

### 1. Deploy Tika Server

```bash
cd tika
cf push
cd ..
```

### 2. Enable Container-to-Container Networking

```bash
cf add-network-policy open-webui tika-server --port 8080 --protocol tcp
```

The main `manifest.yml` is pre-configured to use Tika when available. If Tika is unreachable, Open WebUI falls back to its default PDF parser.

---

## 🏢 Enterprise Edition (manifest-v3.yml)

For production deployments requiring persistent storage, secrets management, and auto-scaling, use `manifest-v3.yml`.

### Additional Services Required

#### 1. CredHub (Secrets Management)
Store sensitive values like `WEBUI_SECRET_KEY` securely:

```bash
# Generate a secure random key
SECRET_KEY=$(openssl rand -hex 32)

# Create CredHub service with the secret
cf create-service credhub default owui-secrets -c "{\"webui_secret_key\":\"$SECRET_KEY\"}"
```

#### 2. App Autoscaler
Automatically scale instances based on load:

```bash
cf create-service autoscaler standard owui-autoscaler
```

After deployment, configure scaling rules:
```bash
# Example: Scale between 2-10 instances based on HTTP latency
cf update-service owui-autoscaler -c '{
  "instance_limits": {"min": 2, "max": 10},
  "scaling_rules": [{
    "metric_type": "http_latency",
    "threshold": {"min": 10, "max": 500},
    "adjustment": "+1"
  }]
}'
```

#### 3. NFS Volume (Persistent Uploads)
Persist uploaded documents across restarts and scaling:

```bash
# Replace with your NFS server details
cf create-service nfs Existing owui-nfs -c '{"share":"nfs-server.example.com/exports/openwebui","mount":"/home/vcap/nfs/openwebui"}'
```

### Deploy Enterprise Edition

```bash
cf push -f manifest-v3.yml
```

---

## 📋 Manifest Defaults

| Setting | Value | Notes |
|---------|-------|-------|
| Instances | 2 | HA-ready |
| Memory | 3G | Optimized for typical usage |
| Disk | 6G | Sufficient for Docker layers |
| Timeout | 180s | 3 minute startup window |
