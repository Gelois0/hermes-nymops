# Guía para Hermes — GitHub sin UI manual

Repo: https://github.com/Gelois0/hermes-nymops  
Cuenta GitHub: `Gelois0`  
Branch por defecto: `main`

Úsala para que **Hermes** gestione este repo con `gh` + `git`, sin Settings en github.com.  
Complementa [AGENTS.md](../AGENTS.md), [SECURITY.md](../SECURITY.md) y [CONTRIBUTING.md](../CONTRIBUTING.md). No sustituye [INSTALL.md](../INSTALL.md).

---

## Antes de pegar un prompt

1. Hermes debe poder correr `gh` y `git` en el **host** donde está autenticado `Gelois0` (no inventes tokens en el chat).
2. Comprueba auth una vez: `gh auth status` → cuenta `Gelois0`, scope `repo`.
3. Sustituye solo la línea **Objetivo ahora**.
4. Si el objetivo implica nodos Nym, VPS NYMOPS, `apply()` o `NYM_MAINTENANCE_ENABLED=1`, **no uses esta guía**; usa aprobación explícita y los agent cards en [`agents/`](../agents/).

---

## Prompt A — Visibilidad / estado del repo

```
Trabaja solo con GitHub CLI (gh). No me pidas pasos en la web de GitHub salvo que falle la autenticación.

Repo: https://github.com/Gelois0/hermes-nymops
Cuenta esperada: Gelois0
Branch: main

Objetivo ahora: <REEMPLAZA: "confirma visibilidad" | "haz el repo público" | "haz el repo privado" | "solo reporta metadatos">

Pasos obligatorios:
1. `gh auth status` — debe ser Gelois0 con scope repo. Si no, DETENTE y pide `gh auth login` en el host correcto.
2. `gh repo view Gelois0/hermes-nymops --json name,visibility,isPrivate,url,defaultBranchRef`
3. Si el objetivo es hacer público y aún no lo es:
   `gh repo edit Gelois0/hermes-nymops --visibility public`
   luego vuelve a `gh repo view ...` y exige visibility=PUBLIC e isPrivate=false
4. Si el objetivo es hacer privado: `--visibility private` y verifica.
5. Si ya cumple el objetivo, no vuelvas a editar.

Prohibido:
- borrar el repo, force-push, cambiar default branch sin pedirlo
- crear releases/tags salvo que yo nombre el tag exacto
- imprimir tokens, secrets, .env, claves SSH o approval secrets
- contactar nodos Nym, tocar NYMOPS en el VPS, habilitar maintenance/apply
- montar o pedir docker.sock

Reporte final (solo esto): URL, visibility, isPrivate, default branch, comando(s) exactos usados.
```

---

## Prompt B — Clonar / tests offline (sin instalar)

```
Trabaja el repo público sin instalar NYMOPS ni tocar VPS/nodos.

1. Clone limpio o actualización fast-forward:
   git clone https://github.com/Gelois0/hermes-nymops.git
   # o, si ya existe: git -C hermes-nymops fetch origin && git -C hermes-nymops pull --ff-only origin main
2. Desde la raíz del clone, SOLO offline:
   bash -n src/nym-ansible/run-nym-ansible.sh
   python3 -m py_compile src/nym-mcp/*.py src/nym-controller/*.py
   python3 tests/test_m3_m6_offline.py
   python3 tests/test_human_approval_offline.py
3. Lee README.md, INSTALL.md, SECURITY.md, AGENTS.md y docs/guia-hermes-github-publico.md.

Prohibido: deploy/install-nymops.sh --install, systemctl, MCP del VPS,
NYM_MAINTENANCE_ENABLED=1, apply(), contacto a nodos, commits salvo que yo lo pida.

Reporte: PASS/FAIL de cada check, commit SHA de HEAD, archivos leídos.
```

---

## Prompt C — Subir un cambio de docs (con mi aprobación)

Usa esto solo cuando ya autorizaste el contenido exacto a subir.

```
Repo local o clone de https://github.com/Gelois0/hermes-nymops
Branch: main. Cuenta: Gelois0.

Objetivo: subir SOLO el/los archivo(s) que yo indiqué: <RUTAS>

1. git status && git diff && git log -5 --oneline
2. No staged secrets (.env, *secret*, *token*, claves, inventarios reales, evidence con IPs/identities).
3. git pull --rebase origin main
4. git add <RUTAS> && commit con mensaje corto en inglés centrado en el porqué
5. git push origin main  (sin --force)
6. Si push rechazado: pull --rebase de nuevo y reintentar una vez; si sigue fallando, DETENTE.

Prohibido: force-push, --no-verify, amend de commits ajenos, tags/releases no pedidos,
tocar src/deploy salvo que las rutas lo incluyan explícitamente, nodos/VPS/maintenance.

Reporte: commit SHA, URL del archivo en GitHub, `git status` limpio.
```

---

## Comandos de referencia

| Acción | Comando |
|---|---|
| Auth | `gh auth status` |
| Ver repo | `gh repo view Gelois0/hermes-nymops --json name,visibility,isPrivate,url,defaultBranchRef` |
| Público | `gh repo edit Gelois0/hermes-nymops --visibility public` |
| Privado | `gh repo edit Gelois0/hermes-nymops --visibility private` |
| Último commit remoto | `gh api repos/Gelois0/hermes-nymops/commits/main --jq '{sha:.sha,message:.commit.message}'` |
| Ver archivo en web | `https://github.com/Gelois0/hermes-nymops/blob/main/<path>` |
| Sync local | `git fetch origin && git pull --ff-only origin main` |

---

## Fallos frecuentes

| Síntoma | Qué hacer |
|---|---|
| `gh` no autenticado / wrong user | Parar; `gh auth login` en el host correcto. No pegar tokens en chat. |
| Push rejected (remote ahead) | `git pull --rebase origin main` y un solo reintento de push. Sin force. |
| Visibilidad no cambia | Releer `gh repo view`; confirmar permisos `viewerPermission` admin/maintain. |
| Tests offline fallan | No “arreglar” instalando en VPS; reportar salida y pedir alcance. |
| Hermes pide UI de GitHub | Recordarle esta guía; solo UI si `gh` no puede autenticarse. |

---

## Checklist humano (30 segundos)

1. Eliges Prompt A, B o C y sustituyes el objetivo / rutas.
2. Pegas en Hermes.
3. Verificas el reporte (URL + visibility o SHA).
4. Instalación real o cambios en nodos: **otra** autorización + `INSTALL.md` / agent cards — fuera de esta guía.

Confirmación mínima si ya es público:

```
Confirma con gh que Gelois0/hermes-nymops es PUBLIC. No cambies nada más.
```
