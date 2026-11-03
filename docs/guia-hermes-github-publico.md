# Guía para Hermes — GitHub sin UI manual

Repo de referencia: https://github.com/Gelois0/hermes-nymops  
Estado actual: **público** (confirmado 2026-11-03).

Copia el bloque **Prompt para pegar** en un chat de Hermes (laptop o VPS). Hermes debe usar `gh` autenticado como `Gelois0`; no abras Settings en github.com a mano.

---

## Prompt para pegar

```
Trabaja solo con GitHub CLI (gh). No me pidas pasos en la web de GitHub salvo que gh falle por auth.

Repo: https://github.com/Gelois0/hermes-nymops
Cuenta esperada: Gelois0

Objetivo ahora: <REEMPLAZA: p.ej. "haz el repo público" | "confirma visibilidad" | "crea tag v0.1.0-alpha draft" | "no toques nada, solo reporta">

Reglas:
1. Primero: `gh auth status` y `gh repo view Gelois0/hermes-nymops --json name,visibility,isPrivate,url,defaultBranchRef`.
2. Si pido cambiar visibilidad a público:
   - Ejecuta: `gh repo edit Gelois0/hermes-nymops --visibility public`
   - Verifica: visibility=PUBLIC e isPrivate=false
3. Si pido privado: `gh repo edit Gelois0/hermes-nymops --visibility private` y verifica.
4. No crees releases estables ni tags salvo que yo nombre el tag exacto.
5. No borres el repo. No forces push. No cambies default branch sin pedirlo.
6. No imprimas tokens, secrets, ni contenido de .env / claves.
7. No contactes nodos Nym, no toques NYMOPS en el VPS, no habilites maintenance/apply.
8. Al final reporta solo: URL, visibility, isPrivate, y el comando exacto que usaste.
```

---

## Comandos que Hermes debe preferir

| Acción | Comando |
|---|---|
| Ver estado | `gh repo view Gelois0/hermes-nymops --json name,visibility,isPrivate,url` |
| Hacer público | `gh repo edit Gelois0/hermes-nymops --visibility public` |
| Hacer privado | `gh repo edit Gelois0/hermes-nymops --visibility private` |
| Auth | `gh auth status` (scopes con `repo`) |

Si `gh` no está logueado: detente y pide `gh auth login` en el host correcto (no inventes tokens en el chat).

---

## Usar el repo público desde Hermes (solo lectura / offline)

Cuando quieras que Hermes trabaje el código **sin** instalar en el VPS ni tocar nodos:

```
Clona o actualiza solo lectura el repo público:
  git clone https://github.com/Gelois0/hermes-nymops.git
  # o: git -C hermes-nymops pull --ff-only

Luego, desde la raíz del clone, SOLO offline:
  bash -n src/nym-ansible/run-nym-ansible.sh
  python3 -m py_compile src/nym-mcp/*.py src/nym-controller/*.py
  python3 tests/test_m3_m6_offline.py
  python3 tests/test_human_approval_offline.py

Lee README.md, INSTALL.md, SECURITY.md y AGENTS.md.
NO ejecutes deploy/install-nymops.sh --install.
NO contactes nodos, NO toques systemd/MCP del VPS, NO pongas NYM_MAINTENANCE_ENABLED=1.
Reporta resultados de tests y qué archivos leíste.
```

Instalación real en un Hermes/VPS: requiere **aprobación explícita** aparte y seguir `INSTALL.md` (alpha; greenfield no probado).

---

## Checklist rápido (humano)

1. Pegas el prompt en Hermes y sustituyes el objetivo.
2. Hermes corre `gh` y te devuelve URL + visibility.
3. Abres la URL solo para comprobar; no hace falta Settings → Danger Zone.

Si ya está público y solo quieres confirmar:

```
Confirma con gh que Gelois0/hermes-nymops es PUBLIC. No cambies nada más.
```
