# GitLab 15.11 API Mini-Service (Roles + Created-In-Year)

A minimal FastAPI service (Alpine-based) that implements two features against **GitLab 15.11 REST (v4)**:

1. **Grant / change role** for a given user on a **project or group**
2. **List all issues or merge requests created in a given year**

---

## Requirements

- Docker
- A reachable GitLab instance (gitlab.com or local Dockerized GitLab 15.11)
- A GitLab **Personal Access Token (PAT)** with at least `api` scope

> On macOS with Docker Desktop: when your GitLab runs on the **host** (e.g., `-p 80:80`), containers should reach it via `http://host.docker.internal/`.

---

## Quick Start

### 1) Build the image

```bash
docker build -t gitlab-api-service:15.11 .
```

### 2) Run the container

```bash
docker run --rm -p 8080:8080 \
  -e GITLAB_URL="http://host.docker.internal/" \
  -e GITLAB_TOKEN="glpat-REPLACE_ME" \
  gitlab-api-service:15.11
```

- Service is now at http://localhost/
- GITLAB_URL points to your GitLab base URL (containers → host)
- GITLAB_TOKEN is your PAT (do not commit it)

### 3) Sanity check (host → GitLab)

```bash
curl -s --header "PRIVATE-TOKEN: $GITLAB_TOKEN" \
  http://localhost/api/v4/version | jq
```

You should see version JSON (e.g., 15.11.13-ee).

---

## API Reference

### POST /roles/grant

Grant or change a user’s role on a project or group.

```bash
{
  "username": "testuest",
  "target":   "group/subgroup/project",   // or "group[/subgroup]" for groups
  "role":     "developer"                 // or "10|20|30|40|50"
}
```

Role map (GitLab access levels)
guest=10, reporter=20, developer=30, maintainer=40, owner=50

Notes

- Owner (50) is group-only; not assignable directly at project level.
- If the user already has the requested level as a direct member, returns "action":"noop".
- Attempting to “downgrade” someone who inherits Owner from a parent group will 400 (GitLab behavior).

```bash
curl -s -X POST http://localhost:8080/roles/grant \
  -H 'Content-Type: application/json' \
  -d '{"username":"testuser","target":"gpt/many_groups_and_projects/gpt-subgroup-1/gpt-project-1","role":"developer"}' | jq .
```

### GET /created/{kind}/{year}

Return all items of a kind created in a calendar year.

Examples:

```bash
# How many issues created in 2025?
curl -s "http://localhost:8080/created/issues/2025" | jq 'length'

# Inspect first few MRs created in 2025
curl -s "http://localhost:8080/created/mr/2025" | jq '.[0:3]'
```

### Local GitLab playground (optional)

If you don’t want to use gitlab.com, you can run a local GitLab 15.11:

```bash
GITLAB_HOME=/tmp/gitlab
docker run -d --hostname gitlab.example.com \
  -e GITLAB_OMNIBUS_CONFIG="external_url 'http://gitlab.example.com'" \
  -p 443:443 -p 80:80 -p 22:22 \
  --name gitlab --restart always \
  -v $GITLAB_HOME/config:/etc/gitlab \
  -v $GITLAB_HOME/logs:/var/log/gitlab \
  -v $GITLAB_HOME/data:/var/opt/gitlab \
  --shm-size 256m \
  gitlab/gitlab-ee:15.11.13-ee.0
```

- Log in as root (password in /etc/gitlab/initial_root_password inside the container).
- Create a PAT (scope: api).
- (Optional) Seed data using GitLab’s GPT data generator. If you do, point the generator JSON URL to http://host.docker.internal/ so its container can reach the host-published GitLab.

---

## Design notes

- FastAPI + Uvicorn (ASGI): clean validation (Pydantic), auto OpenAPI docs, and simple async-ready runtime.
- Requests Session: reuses connections; sets PRIVATE-TOKEN once; consistent error handling.
- Full-path resolution: callers can pass group/subgroup/project directly; we resolve to numeric IDs internally.
- Idempotency: “noop” if membership already at desired level.
- Pagination: exhaustively fetch all results for year queries.

---

## Troubleshooting

- {"detail":"Not Found"} or HTML login page: Wrong GITLAB_URL or missing/invalid PAT.
- From a container, localhost doesn’t reach host: use http://host.docker.internal/ (macOS/Windows).
- Cannot assign Owner at project: Owner (50) is group-scope only, and inherited Owner cannot be downgraded via project membership.
- No results for a year: create some issues/MRs in the UI for that year and retry.

---
