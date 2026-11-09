# Production Deployment in an Air-Gapped Environment

Step-by-step commands for Windows **cmd**, to create a production Open WebUI (two instances) on Tanzu Platform with no internet access.

You end up with:

| App | Instances | Memory each | Purpose |
| --- | --- | --- | --- |
| `open-webui` | 2 | 3 GB | The chat UI |
| `tika-server` | 2 | 4 GB | Text extraction for Hebrew and other RTL documents |

The space needs about **14 GB** of free memory quota, and tasks or restarts need some headroom on top.

---

## Part A: On the connected machine

### A1. Collect the files

Copy these from this repository to your transfer media:

| File | What it is |
| --- | --- |
| `openwebui-v0.13.2.tar` | Open WebUI image, linux/amd64, about 4.3 GB |
| `tika-server-4.2.0.tar` | Tika image, linux/amd64, about 1.3 GB |
| `IMAGES.sha256` | Checksums of the two tar files |
| `manifest-production.yml` | Open WebUI, two instances |
| `manifest-tika.yml` | Tika server |
| `AIRGAP-GUIDE.md` | This guide |

If you need to rebuild the tar files (for example on a Mac):

```bash
docker pull --platform linux/amd64 ghcr.io/open-webui/open-webui:v0.13.2
docker pull --platform linux/amd64 apache/tika:4.2.0-full
docker save -o openwebui-v0.13.2.tar ghcr.io/open-webui/open-webui:v0.13.2
docker save -o tika-server-4.2.0.tar apache/tika:4.2.0-full
shasum -a 256 openwebui-v0.13.2.tar tika-server-4.2.0.tar > IMAGES.sha256
```

---

## Part B: Inside the air-gapped network

### B1. Verify the files arrived intact

```bat
type IMAGES.sha256
certutil -hashfile openwebui-v0.13.2.tar SHA256
certutil -hashfile tika-server-4.2.0.tar SHA256
```

Each hash printed by `certutil` must match the line for that file in `IMAGES.sha256`. Letter case does not matter. If one differs, copy that file again.

### B2. Push the images to your internal registry

Cloud Foundry pulls Docker images from a registry, so the tar files must go into your internal registry (Harbor, Artifactory, and so on). Run this on any machine with Docker that can reach the registry. Replace `registry.corp.local` with your registry's address, and `openwebui` with the project or folder you use there.

```bat
docker login registry.corp.local

docker load -i openwebui-v0.13.2.tar
docker load -i tika-server-4.2.0.tar

docker tag ghcr.io/open-webui/open-webui:v0.13.2 registry.corp.local/openwebui/open-webui:v0.13.2
docker tag apache/tika:4.2.0-full registry.corp.local/openwebui/tika:4.2.0-full

docker push registry.corp.local/openwebui/open-webui:v0.13.2
docker push registry.corp.local/openwebui/tika:4.2.0-full
```

> If the registry uses a certificate from an internal CA, the platform must already trust it. Ask the platform team if a push later fails with an x509 or certificate error.

### B3. Log in to the platform

```bat
cf login -a https://api.sys.YOUR-DOMAIN --skip-ssl-validation
cf target -o YOUR-ORG -s YOUR-SPACE
```

Leave out `--skip-ssl-validation` if your platform certificate is trusted on this machine.

### B4. Check the service names in your marketplace

Plan names differ between foundations. Check before creating anything:

```bat
cf marketplace
cf marketplace -e ai-models
cf marketplace -e postgres
cf marketplace -e p.redis
cf marketplace -e p-identity
cf marketplace -e smb
```

Use whatever plan names these show in the next step. If `smb` is missing, the platform team must enable the SMB volume service first (`cf enable-service-access smb`, run by an admin).

### B5. Create the JSON file for the SMB share

Create the share definition. Replace the server and share name with yours:

```bat
echo {"share":"//fileserver.corp.local/openwebui","version":"3.0"} > smb-share.json
type smb-share.json
```

Leave out `,"version":"3.0"` if you are not sure which SMB version the server speaks. The client and server then agree on one.

### B6. Create the services

```bat
cf create-service ai-models tanzu-all-models owui-ai --wait
cf create-service p-identity uaa owui-sso --wait
cf create-service postgres on-demand-postgres-db owui-postgres --wait
cf create-service p.redis on-demand-cache owui-redis --wait
cf create-service smb Existing owui-smb -c smb-share.json --wait
```

`--wait` returns only when each service is ready. Postgres and Valkey can take several minutes each. If your cf CLI says `unknown flag: --wait`, run the commands without it and repeat `cf services` until every row shows `create succeeded`.

Check the result:

```bat
cf services
```

All five must show `create succeeded` before you continue.

**About the AI models:** `tanzu-all-models` gives every model on the platform, including the embedding model, in one binding. If you only have single-model plans, create one chat plan and the embedding plan, and list both names in the manifest instead of `owui-ai`:

```bat
cf create-service ai-models tanzu-gpt-oss-120b owui-chat --wait
cf create-service ai-models tanzu-nomic-embed-text-v2 owui-embed --wait
```

### B7. Edit `manifest-tika.yml`

Change one line, the image:

```yaml
      image: registry.corp.local/openwebui/tika:4.2.0-full
```

### B8. Edit `manifest-production.yml`

Open it in Notepad and change these lines:

1. **Image**, near the top:
   ```yaml
         image: registry.corp.local/openwebui/open-webui:v0.13.2
   ```
2. **Secret key.** Generate a random value in cmd and paste it in:
   ```bat
   powershell -NoProfile -Command "[guid]::NewGuid().ToString('N') + [guid]::NewGuid().ToString('N')"
   ```
   ```yaml
         WEBUI_SECRET_KEY: "the-64-character-value-you-generated"
   ```
   Keep this value. Changing it later logs every user out.
3. **SMB account**, under `owui-smb` in the `services:` list:
   ```yaml
         - name: owui-smb
           parameters:
             username: "svc-openwebui"
             password: "the-real-password"
             domain: "CORP"
             mount: "/home/vcap/data"
   ```
   Delete the `domain` line if the account is not a Windows domain account. Do not change `mount`.
4. **Service names.** If you used different names in step B6, change them in the `services:` list.

Do not change anything else. In particular, leave the long `command:` block exactly as it is.

### B9. Registry credentials (only if your registry needs a login)

Add your registry user under `docker:` in **both** manifests:

```yaml
    docker:
      image: registry.corp.local/openwebui/open-webui:v0.13.2
      username: svc-registry
```

Then set the password for this cmd window. There must be no spaces around `=`:

```bat
set CF_DOCKER_PASSWORD=the-registry-password
```

### B10. Deploy Tika

```bat
cf push -f manifest-tika.yml
cf app tika-server
```

Wait until both instances show `running`. Tika gets no public address, only the internal one `tika-server.apps.internal`, on port 9998.

### B11. Deploy Open WebUI

```bat
cf push -f manifest-production.yml
```

The first start takes a few minutes. One instance creates the database tables while the other waits its turn.

### B12. Allow Open WebUI to reach Tika

```bat
cf add-network-policy open-webui tika-server --port 9998 --protocol tcp
cf network-policies --source open-webui
cf restart open-webui
```

The output of the second command must show `open-webui  tika-server  tcp  9998`. The restart makes Open WebUI re-check Tika now that the policy exists. You only do this step once: the policy stays through later pushes. It is lost only if you `cf delete` either app.

### B13. Verify

```bat
cf app open-webui
cf logs open-webui --recent | findstr "BOOT:"
```

`cf app` must show `instances: 2/2`. Each instance must log these lines (in any order):

| Log line | Means |
| --- | --- |
| `BOOT: Postgres: owui-postgres` | Database connected |
| `BOOT: owui-ai: chat=[...] embed=[...]` | Models discovered. `embed=` must not be empty |
| `BOOT: RAG embedding model: ...` | Document search uses the platform's GPU embedding model |
| `BOOT: Redis: owui-redis (websocket manager = redis)` | Instances share live events |
| `BOOT: DATA_DIR on volume owui-smb: /home/vcap/data` | Uploads go to the SMB share |
| `BOOT: SSO: owui-sso` | Login through Tanzu SSO |
| `BOOT: Tika OK at http://tika-server.apps.internal:9998: Apache Tika 4.2.0` | Text extraction reachable |

### B14. First login

1. Run `cf app open-webui` and open the address under `routes:` in a browser.
2. Click **Continue with Tanzu SSO**. **The first person to log in becomes the admin**, so the intended admin should log in first.
3. Everyone after that waits as *pending* until the admin approves them in **Admin Panel > Users**.

### B15. Test an upload

1. Start a chat, attach a Hebrew PDF and ask a question about it.
2. Watch Tika receive it:
   ```bat
   cf logs tika-server --recent | findstr /i "tika"
   ```
3. Large documents take a while: embedding speed depends on the platform's GPU embedding model. In the lab it embedded about 2.4 chunks per second, so a 1.4 MB PDF of about 500,000 characters (800 chunks) took about 5 minutes. Small documents take seconds.

---

## Part C: If something is wrong

| Symptom | Check |
| --- | --- |
| `BOOT: Tika NOT reachable ... Connection refused` or `timed out` | Run `cf network-policies --source open-webui`. If the policy is missing, repeat step B12. Also check `cf app tika-server` shows instances running. |
| `BOOT: Tika NOT reachable ... Name or service not known` | Run `cf domains` and look for the internal domain. If it is not `apps.internal`, change the route in `manifest-tika.yml` and `TIKA_SERVER_URL` in `manifest-production.yml` to match. |
| No `DATA_DIR on volume` line | The SMB share did not bind. Run `cf services` and check `owui-smb` is bound to `open-webui`. Run `cf events open-webui` for mount errors such as a wrong password. |
| No `embed=` model in the boot log | The bound AI plan has no embedding model. Bind one: `cf create-service ai-models tanzu-nomic-embed-text-v2 owui-embed --wait`, `cf bind-service open-webui owui-embed`, `cf restart open-webui`. |
| `instances: 1/2` for a long time | Run `cf events open-webui` and `cf logs open-webui --recent`. A quota error means the space or org is out of memory. |
| Push fails pulling the image | Check the image path in the manifest, the registry login (step B9), and that the platform trusts the registry certificate. |
| Users show as numbers instead of names | The SSO plan must map `given_name` and `family_name` from your directory, typically `givenName` and `sn`. Names refresh at each user's next login. |

To change the SMB username or password after the first deployment:

```bat
cf unbind-service open-webui owui-smb
cf push -f manifest-production.yml
```

---

## Part D: Upgrading later

1. Back up Postgres and the SMB share.
2. Load and push the new image as in step B2, and change the image tag in `manifest-production.yml`.
3. Push normally, **never** with `--strategy rolling`. Old and new versions cannot share the database during an upgrade.
   ```bat
   cf push -f manifest-production.yml
   cf logs open-webui --recent | findstr /c:"BOOT: migration"
   ```
