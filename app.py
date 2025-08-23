#!/usr/bin/env python3
import os, sys, json, urllib.parse, datetime
from typing import Literal, Dict, Any, List, Optional

import requests
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# -------- Config --------
GITLAB_URL = os.environ.get("GITLAB_URL", "http://localhost")
GITLAB_TOKEN = os.environ.get("GITLAB_TOKEN", "")
VERIFY_SSL = os.environ.get("GITLAB_VERIFY_SSL", "true").lower() != "false"

if not GITLAB_URL.endswith("/"):
    GITLAB_URL += "/"

API = urllib.parse.urljoin(GITLAB_URL, "api/v4/")  # 15.11 REST path
SESSION = requests.Session()
SESSION.headers.update({"PRIVATE-TOKEN": GITLAB_TOKEN} if GITLAB_TOKEN else {})
SESSION.verify = VERIFY_SSL

# GitLab 15.x default roles (planner/minimal omitted intentionally)
ROLE_TO_LEVEL = {
    "guest": 10,
    "reporter": 20,
    "developer": 30,
    "maintainer": 40,
    "owner": 50,
}

# -------- Helpers --------
def _fail(message: str, status: int = 400):
    raise HTTPException(status_code=status, detail=message)

def _get(path: str, **params):
    r = SESSION.get(urllib.parse.urljoin(API, path), params=params)
    if r.status_code >= 400:
        _fail(f"GET {path} failed ({r.status_code}): {r.text}", r.status_code)
    return r

def _post(path: str, data: Dict[str, Any]):
    r = SESSION.post(urllib.parse.urljoin(API, path), data=data)
    if r.status_code >= 400:
        _fail(f"POST {path} failed ({r.status_code}): {r.text}", r.status_code)
    return r

def _put(path: str, data: Dict[str, Any]):
    r = SESSION.put(urllib.parse.urljoin(API, path), data=data)
    if r.status_code >= 400:
        _fail(f"PUT {path} failed ({r.status_code}): {r.text}", r.status_code)
    return r

def _resolve_user_id(username: str) -> int:
    r = _get("users", username=username)
    users = r.json()
    if not users:
        _fail(f"User '{username}' not found", 404)
    # exact match safeguard
    for u in users:
        if u.get("username") == username:
            return u["id"]
    _fail(f"User '{username}' not found (exact match)", 404)

def _resolve_project_or_group_id(path: str) -> Dict[str, Any]:
    """
    Try project by URL-encoded full path, else group by URL-encoded full path.
    Returns dict: {"kind": "project"|"group", "id": <int>}
    """
    encoded = urllib.parse.quote_plus(path)
    # Projects first
    rp = SESSION.get(urllib.parse.urljoin(API, f"projects/{encoded}"))
    if rp.status_code == 200:
        return {"kind": "project", "id": rp.json()["id"]}
    # Then groups
    rg = SESSION.get(urllib.parse.urljoin(API, f"groups/{encoded}"))
    if rg.status_code == 200:
        return {"kind": "group", "id": rg.json()["id"]}
    _fail(f"Target '{path}' not found as project or group", 404)

def _current_member(kind: str, target_id: int, user_id: int) -> Optional[Dict[str, Any]]:
    r = SESSION.get(urllib.parse.urljoin(API, f"{kind}s/{target_id}/members/{user_id}"))
    if r.status_code == 404:
        return None
    if r.status_code >= 400:
        _fail(f"GET {kind}s/{target_id}/members/{user_id} failed ({r.status_code}): {r.text}", r.status_code)
    return r.json()

# -------- Core Function A --------
def grant_or_change_role(username: str, target_path: str, role: str) -> Dict[str, Any]:
    if not GITLAB_TOKEN:
        _fail("GITLAB_TOKEN is not set", 401)
    user_id = _resolve_user_id(username)  # GET /users?username=...  (exact match)
    target = _resolve_project_or_group_id(target_path)  # projects/:id or groups/:id by path
    try:
        level = int(role)
    except ValueError:
        level = ROLE_TO_LEVEL.get(role.lower())
    if level not in ROLE_TO_LEVEL.values():
        _fail(f"Invalid role '{role}'. Allowed: {list(ROLE_TO_LEVEL.keys())} or {sorted(ROLE_TO_LEVEL.values())}")

    kind, tid = target["kind"], target["id"]
    member = _current_member(kind, tid, user_id)

    if member and member.get("access_level") == level:
        return {
            "action": "noop",
            "message": f"user already has access_level={level}",
            "target_kind": kind, "target_id": tid, "user_id": user_id
        }

    payload = {"user_id": user_id, "access_level": level}
    if member:
        _put(f"{kind}s/{tid}/members/{user_id}", payload)   # update
        action = "updated"
    else:
        _post(f"{kind}s/{tid}/members", payload)            # add
        action = "added"

    return {"action": action, "target_kind": kind, "target_id": tid, "user_id": user_id, "access_level": level}

# -------- Core Function B --------
def list_created_in_year(kind: Literal["mr","issues"], year: int) -> List[Dict[str, Any]]:
    if not (1900 <= year <= 2100):
        _fail("year must be a 4-digit integer within a sane range (1900..2100)")
    endpoint = "merge_requests" if kind == "mr" else "issues"

    start = f"{year}-01-01T00:00:00Z"
    end   = f"{year}-12-31T23:59:59Z"

    out: List[Dict[str, Any]] = []
    page = 1
    while True:
        r = _get(endpoint, scope="all", created_after=start, created_before=end, per_page=100, page=page)
        batch = r.json()
        if not batch:
            break
        out.extend(batch)
        next_page = r.headers.get("X-Next-Page")
        if not next_page:
            break
        page = int(next_page)
    return out

# -------- FastAPI service --------
app = FastAPI(title="GitLab 15.11 Tasks", version="1.0.0")

class GrantBody(BaseModel):
    username: str
    target: str      # project path ("group/subgroup/proj") OR group path ("group[/subgroup]")
    role: str        # "developer" | "30" | etc.

@app.post("/roles/grant")
def api_grant(body: GrantBody):
    return grant_or_change_role(body.username, body.target, body.role)

@app.get("/created/{kind}/{year}")
def api_created(kind: Literal["mr","issues"], year: int):
    return list_created_in_year(kind, year)

# -------- CLI for local use --------
def _cli():
    if len(sys.argv) < 2:
        print("Usage:\n  grant-role <username> <target_path> <role>\n  list <issues|mr> <year>")
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "grant-role" and len(sys.argv) == 5:
        print(json.dumps(grant_or_change_role(sys.argv[2], sys.argv[3], sys.argv[4]), indent=2))
    elif cmd == "list" and len(sys.argv) == 4:
        kind = sys.argv[2]
        if kind not in {"issues","mr"}:
            print("kind must be 'issues' or 'mr'")
            sys.exit(2)
        year = int(sys.argv[3])
        print(json.dumps(list_created_in_year("merge_requests" if kind=="mr" else "issues" if False else kind, year), indent=2))
    else:
        print("Bad arguments.")
        sys.exit(2)

if __name__ == "__main__":
    _cli()
