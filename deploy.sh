#!/usr/bin/env bash
# Deploys the Tome Raider proof of concept to Azure App Service on the Free (F1) tier, in the personal subscription only.
#
# Reads ../azure.md (service principal), ../anthropic.md (Claude API key), ../admin-claude-key.md (optional: an Admin API key,
# used only to read the account's spending for the "credit left" figure), ../azure-storage.md (optional: the storage
# account's connection string, where captures, runs and marks are kept) and ../shelfscan.md (app name, passcode and
# session secret; created on first run). The code's git commit goes into version.json, so each run records its baseline. Secret values go to Azure through a temporary file, never on a command line.
#
# usage: AZURE_CONFIG_DIR=<an isolated az profile folder> ./deploy.sh [--settings-only]
#   --settings-only   update the app settings (for example after rotating an Anthropic key) without redeploying code
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
NORRIS=$(cd "$HERE/.." && pwd)
RG=norris-bookshelf
PLAN=norris-shelfscan-centralus
LOCATION=centralus             # this subscription has no Free (F1) App Service quota in East US or East US 2
: "${AZURE_CONFIG_DIR:?set AZURE_CONFIG_DIR to an isolated az profile folder, so a work login is never used}"
export AZURE_CONFIG_DIR

set -a; . "$NORRIS/azure.md"; set +a
case "$AZURE_TENANT_ID" in 5d70cd3b*) echo "refusing: that is the ParcelShield tenant" >&2; exit 1;; esac
az login --service-principal -u "$AZURE_CLIENT_ID" -p "$AZURE_CLIENT_SECRET" --tenant "$AZURE_TENANT_ID" -o none
az account set -s "$AZURE_SUBSCRIPTION_ID"

# app name, passcode and session secret, made once and kept beside the other key files
SECRETS="$NORRIS/shelfscan.md"
if [ ! -f "$SECRETS" ]; then
  umask 077
  python3 - "$SECRETS" <<'EOF'
import secrets, sys
open(sys.argv[1], 'w').write(f"SHELFSCAN_APP=norris-shelfscan-{secrets.token_hex(3)}\n"
                             f"SHELFSCAN_PASSCODE={secrets.token_urlsafe(9)}\nSHELFSCAN_SECRET={secrets.token_hex(32)}\n")
EOF
fi
set -a; . "$SECRETS"; set +a
APP="$SHELFSCAN_APP"

if [ "${1:-}" != "--settings-only" ]; then
  if ! az appservice plan show -g "$RG" -n "$PLAN" -o none 2>/dev/null; then
    echo "creating App Service plan $PLAN (Linux, Free F1)"
    az appservice plan create -g "$RG" -n "$PLAN" --is-linux --sku F1 -l "$LOCATION" -o none
  fi
  if ! az webapp show -g "$RG" -n "$APP" -o none 2>/dev/null; then
    echo "creating web app $APP"
    az webapp create -g "$RG" -p "$PLAN" -n "$APP" --runtime "PYTHON:3.12" -o none
  fi
fi

# settings, from a temporary file that only this user can read
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
python3 - "$NORRIS/anthropic.md" "$TMP/settings.json" "$NORRIS/admin-claude-key.md" "$NORRIS/azure-storage.md" <<'EOF'
import json, os, re, sys
key = re.search(r'sk-ant-[A-Za-z0-9_-]+', open(sys.argv[1]).read()).group(0)
admin = re.search(r'sk-ant-admin[A-Za-z0-9_-]+', open(sys.argv[3]).read()) if os.path.exists(sys.argv[3]) else None
if os.path.exists(sys.argv[3]) and not admin: sys.exit('admin-claude-key.md has no Admin API key (sk-ant-admin...) in it')
s = {'ANTHROPIC_API_KEY': key, 'SHELFSCAN_PASSCODE': os.environ['SHELFSCAN_PASSCODE'], 'SHELFSCAN_SECRET': os.environ['SHELFSCAN_SECRET'],
     'SHELFSCAN_HOME': '/home/shelfscan', 'SHELFSCAN_PHOTO_CAP': '2.00', 'SHELFSCAN_DAILY_CAP': '10.00', 'SCM_DO_BUILD_DURING_DEPLOYMENT': 'true'}
if admin: s['ANTHROPIC_ADMIN_KEY'] = admin.group(0)
storage = re.search(r'AZURE_STORAGE_CONNECTION_STRING=(\S+)', open(sys.argv[4]).read()) if os.path.exists(sys.argv[4]) else None
if os.path.exists(sys.argv[4]) and not storage: sys.exit('azure-storage.md has no AZURE_STORAGE_CONNECTION_STRING= line')
if storage: s.update(AZURE_STORAGE_CONNECTION_STRING=storage.group(1), SHELFSCAN_CONTAINER='shelfscan')
json.dump([{'name': k, 'value': v, 'slotSetting': False} for k, v in s.items()], open(sys.argv[2], 'w'))
EOF
az webapp config appsettings set -g "$RG" -n "$APP" --settings "@$TMP/settings.json" -o none
az webapp config set -g "$RG" -n "$APP" -o none \
  --startup-file "gunicorn --bind=0.0.0.0 --workers 1 --threads 8 --timeout 120 app:app"
az webapp update -g "$RG" -n "$APP" --https-only true -o none

if [ "${1:-}" != "--settings-only" ]; then
  python3 - "$HERE" "$TMP/app.zip" <<'EOF'
import json, os, subprocess, sys, zipfile
root, out = sys.argv[1], sys.argv[2]
def git(*a): return subprocess.run(['git', '-C', root, *a], capture_output=True, text=True).stdout.strip()
sha = git('rev-parse', 'HEAD')
version = {'sha': sha or None, 'committed_at': git('log', '-1', '--format=%cI') or None, 'dirty': bool(git('status', '--porcelain'))} if sha else {'sha': None}
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
    for top in ('app.py', 'requirements.txt', 'shelfscan', 'templates', 'data', 'tools'):
        p = os.path.join(root, top)
        if not os.path.exists(p): continue
        files = [p] if os.path.isfile(p) else [os.path.join(d, f) for d, _, fs in os.walk(p) if '__pycache__' not in d for f in fs]
        for f in files:
            if not f.endswith('.xlsx'): z.write(f, os.path.relpath(f, root))      # the sales spreadsheets come from storage
    z.writestr('version.json', json.dumps(version))
print('baseline', version.get('sha', '')[:12] if version.get('sha') else 'none', '(uncommitted changes)' if version.get('dirty') else '')
EOF
  echo "deploying code (the server installs the packages; this takes a few minutes)"
  az webapp deploy -g "$RG" -n "$APP" --src-path "$TMP/app.zip" --type zip --timeout 900000 -o none
else
  az webapp restart -g "$RG" -n "$APP" -o none
fi

URL="https://$APP.azurewebsites.net"
for i in $(seq 1 40); do
  code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "$URL/healthz" || true)
  [ "$code" = 200 ] && { echo "up: $URL"; exit 0; }
  sleep 15
done
echo "not answering yet at $URL/healthz; check the log stream in the Azure portal" >&2
exit 1
