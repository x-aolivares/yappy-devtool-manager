#!/usr/bin/env bash
#
# dump-reference-launcher.sh
#
# Extrae de otro repo (por defecto bbit-release-manager) todo lo necesario para
# entender **cómo levanta la API y el frontend al mismo tiempo**: scripts, cómo
# se orquestan los procesos, y si sirve un build estático en vez de un dev
# server.
#
# Es un dump de solo lectura: no instala, no compila, no ejecuta nada del repo.
#
#   ./scripts/dump-reference-launcher.sh                       # busca el repo solo
#   ./scripts/dump-reference-launcher.sh /ruta/al/repo         # ruta explícita
#   ./scripts/dump-reference-launcher.sh /ruta/al/repo salida.txt
#
# Escribe el resultado a un archivo y además lo imprime, para pegarlo entero.
#
set -uo pipefail

# --- localizar el repo ----------------------------------------------------

guess_repo() {
    for c in \
        "/c/Development/config/bbit-release-manager" \
        "/c/Development/bbit-release-manager" \
        "$HOME/Development/bbit-release-manager" \
        "$HOME/bbit-release-manager" \
        "../bbit-release-manager" \
        "./bbit-release-manager"
    do
        [ -d "$c" ] && { printf '%s\n' "$c"; return 0; }
    done
    return 1
}

REPO="${1:-}"
if [ -z "$REPO" ]; then
    if ! REPO="$(guess_repo)"; then
        cat >&2 <<'EOF'
No encontré el repo y no me pasaste una ruta.

  ./scripts/dump-reference-launcher.sh /c/Development/config/bbit-release-manager
EOF
        exit 1
    fi
fi

if [ ! -d "$REPO" ]; then
    echo "No existe el directorio: $REPO" >&2
    exit 1
fi

REPO="$(cd "$REPO" && pwd)"
OUT="${2:-$(pwd)/reference-launcher-dump.txt}"

# Excluido en todas las búsquedas: pesado e irrelevante para esto. Va en dos
# formas porque `find` necesita -name/-prune y `grep` un regex sobre la ruta.
PRUNE='-name node_modules -o -name .git -o -name dist -o -name build -o -name .venv -o -name venv -o -name __pycache__ -o -name .angular -o -name .next'
# El propio script se autoexcluye: su patrón de grep matchea consigo mismo.
EXCLUDE="node_modules/|\\.git/|dist/|build/|\\.angular/|\\.next/|\\.venv/|venv/|__pycache__/|package-lock\\.json|yarn\\.lock|pnpm-lock|\\.min\\.js|dump-reference-launcher\\.sh"

# --- helpers de salida ----------------------------------------------------

sec() { printf '\n\n===== %s =====\n' "$1"; }

# Muestra un archivo, buscándolo también en subcarpetas: en un monorepo el
# `angular.json` o el `package.json` del front no están en la raíz.
show() {
    local rel="$1"
    local f="$REPO/$rel"
    if [ ! -f "$f" ]; then
        f="$(find "$REPO" -maxdepth 3 \( $PRUNE \) -prune -o -name "$(basename "$rel")" -print 2>/dev/null | head -1)"
    fi

    sec "$rel"
    if [ -n "$f" ] && [ -f "$f" ]; then
        [ "$f" != "$REPO/$rel" ] && echo "(encontrado en ${f#"$REPO"/})"
        head -c 20000 "$f"
    else
        echo "(no existe)"
    fi
    return 0
}

# Lista archivos raíz que suelen contener el arranque.
list_globs() {
    sec "archivos de arranque en la raíz"
    local found=0 f
    for pat in Makefile makefile docker-compose.yml docker-compose.yaml \
               compose.yml compose.yaml Procfile justfile Taskfile.yml \
               "*.sh" "*.ps1" "*.bat" "*.cmd"
    do
        for f in "$REPO"/$pat; do
            [ -f "$f" ] || continue
            echo "  ${f#"$REPO"/}"
            found=1
        done
    done
    [ "$found" = 0 ] && echo "  (ninguno)"
    return 0
}

# --- armado del dump ------------------------------------------------------

{
echo "########################################################################"
echo "# Dump de arranque API+UI  ->  $REPO"
echo "# Fecha: $(date -u '+%Y-%m-%dT%H:%M:%SZ')"
echo "########################################################################"

sec "git"
if [ -d "$REPO/.git" ]; then
    git -C "$REPO" remote -v 2>/dev/null | head -4
    echo "--- branch/HEAD ---"
    git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null
    git -C "$REPO" log --oneline -3 2>/dev/null
else
    echo "(no es un repo git)"
fi

sec "estructura (profundidad 2)"
find "$REPO" -maxdepth 2 \( $PRUNE \) -prune -o -print 2>/dev/null \
    | sed "s|^$REPO|.|" | sort | head -80

# --- manifiestos ----------------------------------------------------------

show "package.json"
show "pyproject.toml"
show "setup.py"
show "requirements.txt"

sec "package.json — solo scripts"
if [ -f "$REPO/package.json" ]; then
    if command -v node >/dev/null 2>&1; then
        node -e '
          const p = require(process.argv[1]);
          console.log(JSON.stringify({scripts: p.scripts || {}, workspaces: p.workspaces || null}, null, 2));
        ' "$REPO/package.json" 2>/dev/null || echo "(node no pudo leerlo)"
    else
        echo "(node no está en PATH; mirar package.json completo arriba)"
    fi
else
    echo "(no existe)"
fi

sec "package.json en subcarpetas"
found=0
while IFS= read -r pj; do
    [ -n "$pj" ] || continue
    echo "  - ${pj#"$REPO"/}"
    found=1
done < <(find "$REPO" -maxdepth 3 \( $PRUNE \) -prune -o -name package.json -print 2>/dev/null)
[ "$found" = 0 ] && echo "  (ninguno)"

# --- arranque -------------------------------------------------------------

list_globs
show "docker-compose.yml"
show "docker-compose.yaml"
show ".vscode/tasks.json"
show ".vscode/launch.json"
show "angular.json"

# --- proxy / servido de estaticos ----------------------------------------

sec "configs de proxy del frontend"
found=0
while IFS= read -r f; do
    [ -n "$f" ] || continue
    echo "--- ${f#"$REPO"/} ---"
    head -c 3000 "$f"
    echo
    found=1
done < <(find "$REPO" -maxdepth 4 \( $PRUNE \) -prune -o \
    \( -name "proxy*.json" -o -name "proxy*.conf.*" -o -name "proxy*.js" \) -print 2>/dev/null)
[ "$found" = 0 ] && echo "(ninguno)"

# --- la clave: como se levantan juntos ------------------------------------

sec "patrones de arranque (concurrently / dev server / uvicorn / etc.)"
grep -rnE \
  "concurrently|npm-run-all|run-p |wait-on|ng serve|next dev|vite|uvicorn|gunicorn|flask run|fastapi dev|nodemon|start:dev|dev:api|dev:ui|electron" \
  "$REPO" \
  --include="*.py" --include="*.json" --include="*.js" --include="*.ts" \
  --include="*.sh" --include="*.ps1" --include="*.bat" --include="*.cmd" \
  --include="*.yml" --include="*.yaml" --include="*.toml" --include="*.md" \
  --include="Makefile" --include="Procfile" --include="justfile" --include="Taskfile.yml" \
  2>/dev/null \
  | grep -vE "$EXCLUDE" \
  | sed "s|^$REPO|.|" \
  | head -80
echo "(fin)"

sec "quien sirve archivos estaticos (StaticFiles / express.static / sendFile)"
grep -rnE "StaticFiles|express\.static|sendFile|serveStatic|static_folder|/assets|webDir|outputPath" \
  "$REPO" \
  --include="*.py" --include="*.js" --include="*.ts" --include="*.json" \
  2>/dev/null \
  | grep -vE "$EXCLUDE" \
  | sed "s|^$REPO|.|" \
  | head -40
echo "(fin)"

sec "puertos declarados"
grep -rnoE "(port|PORT|puerto)[\"' :=]+[0-9]{2,5}|[0-9]{4,5}[\"']?[[:space:]]*#" \
  "$REPO" \
  --include="*.py" --include="*.json" --include="*.ts" --include="*.js" \
  --include="*.env" --include="*.yml" --include="*.yaml" --include="*.toml" --include="*.sh" \
  2>/dev/null \
  | grep -vE "$EXCLUDE" \
  | sed "s|^$REPO|.|" \
  | head -40
echo "(fin)"

sec "FIN DEL DUMP"
} > "$OUT" 2>&1

BYTES=$(wc -c < "$OUT" | tr -d ' ')
echo "Dump escrito en: $OUT ($BYTES bytes)"
echo
cat "$OUT"
