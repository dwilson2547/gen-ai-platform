#!/bin/bash
# One-time setup of gen-ai-platform's Secrets (modelled on infra/incident-operator/scripts/create-secrets.sh).
# Generates every password/token itself and prints none of them. Run from anywhere:
#   bash ai/gen-ai-platform/scripts/create-secrets.sh
#
# Run it after the cluster-config commit that adds the authentik blueprint has synced (step 5 re-applies it).
#
#   1. namespace gen-ai-platform
#   2. database gen_ai_platform (role + db on the plain postgres)        -> Secret gen-ai-platform-db (DATABASE_URL)
#   3. a gpu-scheduler client token "gen-ai-platform" appended to gpu-scheduler-tokens, the same token in
#      Secret gen-ai-platform-gpu-scheduler (SCHEDULER_TOKEN); restarts gpu-scheduler-api to load it
#   4. AIStor bucket keys (bucket + scoped user made by hand in the console; this step says how and asks for the keys)
#                                                                         -> Secret gen-ai-platform-bucket
#   5. an authentik OIDC client secret: Secret gen-ai-platform-oidc (here) and authentik-gen-ai-platform-oidc
#      (authentik ns), key client_secret in both; restarts authentik and re-applies the blueprint
# Re-running is safe: each step is skipped when its Secret already exists.
set -euo pipefail
NS=gen-ai-platform
HERE=$(cd "$(dirname "$0")" && pwd)
INIT_SQL="$HERE/../../../infra/cluster-config/postgres/init-gen-ai-platform.sql"
exists() { kubectl -n "$1" get secret "$2" >/dev/null 2>&1; }

kubectl get namespace "$NS" >/dev/null 2>&1 || kubectl create namespace "$NS"

# 2. database
if exists "$NS" gen-ai-platform-db; then
  echo "gen-ai-platform-db exists: skipping the database"
else
  PW=$(openssl rand -hex 24)
  kubectl -n postgres exec -i postgres-0 -- sh -c 'psql -U "$POSTGRES_USER" -d postgres -v ON_ERROR_STOP=1 -q -v pw="$1"' _ "$PW" < "$INIT_SQL"
  # Joined from its parts and only ever piped into the Secret, so nothing shaped like a connection URL is written down.
  DB_USER=gen_ai_platform DB_HOST=postgres.postgres.svc.cluster.local:5432 DB_NAME=gen_ai_platform
  printf '%s' "postgresql" "://" "$DB_USER" ":" "$PW" "@" "$DB_HOST" "/" "$DB_NAME" \
    | kubectl -n "$NS" create secret generic gen-ai-platform-db --from-file=DATABASE_URL=/dev/stdin >/dev/null
  echo "database gen_ai_platform created; Secret gen-ai-platform-db written"
fi

# 3. gpu-scheduler client token
if exists "$NS" gen-ai-platform-gpu-scheduler; then
  echo "gen-ai-platform-gpu-scheduler exists: skipping the gpu-scheduler token"
else
  TOK=$(openssl rand -hex 24)
  CUR=$(kubectl -n gpu-scheduler get secret gpu-scheduler-tokens -o jsonpath='{.data.GPU_SCHEDULER_TOKENS}' | base64 -d)
  case ",$CUR," in *,gen-ai-platform:*) echo "gpu-scheduler-tokens already has a gen-ai-platform token; remove it first"; exit 1;; esac
  kubectl -n gpu-scheduler create secret generic gpu-scheduler-tokens \
    --from-literal=GPU_SCHEDULER_TOKENS="$CUR,gen-ai-platform:$TOK" --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  kubectl -n gpu-scheduler annotate secret gpu-scheduler-tokens kubectl.kubernetes.io/last-applied-configuration- >/dev/null 2>&1 || true
  kubectl -n "$NS" create secret generic gen-ai-platform-gpu-scheduler --from-literal=SCHEDULER_TOKEN="$TOK" >/dev/null
  kubectl -n gpu-scheduler rollout restart deploy/gpu-scheduler-api >/dev/null
  echo "gpu-scheduler token for gen-ai-platform added; gpu-scheduler-api restarting to load it"
fi

# 4. AIStor bucket
if exists "$NS" gen-ai-platform-bucket; then
  echo "gen-ai-platform-bucket exists: skipping the bucket keys"
else
  cat <<'MSG'

AIStor bucket: in the console (http://192.168.0.10:30321)
  a. create bucket  gen-ai-platform
  b. create policy  gen-ai-platform  with:
       {"Version":"2012-10-17","Statement":[
         {"Effect":"Allow","Action":["s3:GetObject","s3:PutObject","s3:DeleteObject"],
          "Resource":["arn:aws:s3:::gen-ai-platform/*"]},
         {"Effect":"Allow","Action":["s3:ListBucket","s3:GetBucketLocation"],
          "Resource":["arn:aws:s3:::gen-ai-platform"]}]}
  c. create user    gen-ai-platform  with that policy only, then an access key for it
MSG
  read -rp "Access key: " AK
  read -rsp "Secret key (not echoed): " SK; echo
  [ -n "$AK" ] && [ -n "$SK" ] || { echo "both keys are needed"; exit 1; }
  printf '%s' "$SK" | kubectl -n "$NS" create secret generic gen-ai-platform-bucket \
    --from-literal=S3_ACCESS_KEY="$AK" --from-file=S3_SECRET_KEY=/dev/stdin >/dev/null
  unset SK
  echo "Secret gen-ai-platform-bucket written"
fi

# 5. authentik OIDC client secret
if exists "$NS" gen-ai-platform-oidc; then
  echo "gen-ai-platform-oidc exists: skipping the OIDC client secret"
else
  SEC=$(openssl rand -hex 32)
  printf '%s' "$SEC" | kubectl -n authentik create secret generic authentik-gen-ai-platform-oidc \
    --from-file=client_secret=/dev/stdin --dry-run=client -o yaml | kubectl apply -f - >/dev/null
  printf '%s' "$SEC" | kubectl -n "$NS" create secret generic gen-ai-platform-oidc --from-file=client_secret=/dev/stdin >/dev/null
  kubectl -n authentik rollout restart deploy/authentik-server deploy/authentik-worker >/dev/null
  kubectl -n authentik rollout status deploy/authentik-worker --timeout=300s >/dev/null
  # authentik re-applies a blueprint only when its file changes: one applied before this Secret existed has an empty
  # client secret (incident-operator, 2026-11-01: invalid_client). Apply it again now that the env var is set.
  kubectl -n authentik exec deploy/authentik-worker -- \
    ak apply_blueprint mounted/cm-authentik-blueprints-danwils/gen-ai-platform.yaml >/dev/null
  echo "OIDC client secret written to both Secrets; authentik restarted and the blueprint re-applied"
fi

echo
echo "Next: add yourself (and friends) to the authentik group gen-ai-platform-users."
echo "done:"; kubectl -n "$NS" get secrets
