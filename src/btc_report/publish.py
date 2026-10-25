from __future__ import annotations

import base64
import time
from datetime import datetime, timezone
from typing import Any

import httpx

from btc_report import config as cfg


def publish_report_html(html: str, *, date_stamp: str | None = None) -> str | None:
    """Wstaw raport na GitHub (docs/) i zwróć link GitHub Pages (wykresy JS działają).

    Wymaga w .env: GITHUB_TOKEN, GITHUB_REPO (+ opcjonalnie GITHUB_BRANCH).
    """
    token = cfg.env("GITHUB_TOKEN")
    repo = cfg.env("GITHUB_REPO")
    if not token or not repo:
        print("! Brak GITHUB_TOKEN / GITHUB_REPO — nie publikuję linku.")
        return None

    branch = cfg.env("GITHUB_BRANCH") or "main"
    stamp = date_stamp or datetime.now(timezone.utc).strftime("%Y%m%d")
    files = {
        "docs/report_latest.html": html,
        f"docs/report_{stamp}.html": html,
        "docs/index.html": html,
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "btc-daily-report",
    }

    owner, name = repo.split("/", 1)
    try:
        with httpx.Client(timeout=90.0, headers=headers) as client:
            for path, content in files.items():
                _put_file(client, repo, branch, path, content, message=f"report: update {path}")
            pages_url = _ensure_github_pages(client, repo)
            archive_html = _build_archive_html(client, repo, branch, pages_url)
            _put_file(
                client,
                repo,
                branch,
                "docs/archiwum.html",
                archive_html,
                message="report: update archive",
            )
    except Exception as exc:
        print(f"! Publikacja na GitHub nieudana: {exc}")
        return None

    # Mail dostaje stały URL dnia — root (/) zawsze nadpisuje się najnowszym.
    githack = f"https://raw.githack.com/{owner}/{name}/{branch}/docs/report_{stamp}.html"
    if pages_url:
        latest = pages_url.rstrip("/") + "/"
        dated = pages_url.rstrip("/") + f"/report_{stamp}.html"
        archive = pages_url.rstrip("/") + "/archiwum.html"
        if _wait_url_ok(dated, attempts=12, delay=3.0):
            print(f"✓ GitHub Pages (ten dzień): {dated}")
            print(f"✓ GitHub Pages (najnowszy): {latest}")
            print(f"✓ GitHub Pages (archiwum): {archive}")
            return dated
        print("! GitHub Pages jeszcze nie gotowe — używam raw.githack.")

    print(f"✓ fallback raw.githack: {githack}")
    return githack


def archive_url_from(report_url: str | None) -> str | None:
    """Sibling /archiwum.html next to a dated report or Pages root."""
    if not report_url:
        return None
    base = report_url.rstrip("/")
    if base.endswith(".html"):
        base = base.rsplit("/", 1)[0]
    return base + "/archiwum.html"


def _list_dated_reports(client: httpx.Client, repo: str, branch: str) -> list[str]:
    r = client.get(f"https://api.github.com/repos/{repo}/contents/docs", params={"ref": branch})
    if r.status_code != 200:
        return []
    names = []
    for item in r.json() or []:
        name = str(item.get("name") or "")
        if name.startswith("report_") and name.endswith(".html") and name[7:15].isdigit():
            names.append(name)
    return sorted(names, reverse=True)


def _build_archive_html(
    client: httpx.Client,
    repo: str,
    branch: str,
    pages_url: str | None,
) -> str:
    names = _list_dated_reports(client, repo, branch)
    rows = []
    for name in names:
        stamp = name[7:15]
        label = f"{stamp[6:8]}.{stamp[4:6]}.{stamp[0:4]}"
        rows.append(f'      <li><a href="{name}">{label}</a></li>')
    body = "\n".join(rows) if rows else "      <li>Brak zapisanych dni.</li>"
    latest_href = (pages_url.rstrip("/") + "/") if pages_url else "./"
    return f"""<!DOCTYPE html>
<html lang="pl">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Archiwum raportów BTC/ETH</title>
  <style>
    body {{ font-family: Georgia, 'Times New Roman', serif; color:#1a1a1a; background:#f6f4ef; margin:0; }}
    .wrap {{ max-width:560px; margin:0 auto; padding:28px 16px 48px; }}
    h1 {{ font-size:1.35rem; margin:0 0 8px; }}
    .muted {{ color:#666; font-size:0.9rem; }}
    ul {{ padding-left:1.2rem; }}
    li {{ margin:0.55rem 0; }}
    a {{ color:#0b5fff; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>Archiwum raportów</h1>
    <p class="muted">Kliknij datę — otworzy się HTML w przeglądarce (bez ściągania).</p>
    <p><a href="{latest_href}">Najnowszy raport</a></p>
    <ul>
{body}
    </ul>
  </div>
</body>
</html>
"""


def _wait_url_ok(url: str, *, attempts: int = 10, delay: float = 2.0) -> bool:
    """Sprawdza publiczny GET bez tokenu — czy hosting już serwuje HTML."""
    for _ in range(attempts):
        try:
            r = httpx.get(url, timeout=15.0, follow_redirects=True, headers={"User-Agent": "btc-daily-report"})
            if r.status_code == 200 and len(r.content) > 1000:
                return True
        except Exception:
            pass
        time.sleep(delay)
    return False


def _ensure_github_pages(client: httpx.Client, repo: str) -> str | None:
    """Włącz Pages z /docs jeśli jeszcze nie ma; zwróć html_url."""
    get = client.get(f"https://api.github.com/repos/{repo}/pages")
    if get.status_code == 200:
        data = get.json()
        return data.get("html_url")

    create = client.post(
        f"https://api.github.com/repos/{repo}/pages",
        json={"build_type": "legacy", "source": {"branch": "main", "path": "/docs"}},
    )
    if create.status_code in (201, 204):
        # poczekaj i odczytaj URL
        for _ in range(8):
            time.sleep(2)
            again = client.get(f"https://api.github.com/repos/{repo}/pages")
            if again.status_code == 200:
                url = again.json().get("html_url")
                if url:
                    return url
        owner, name = repo.split("/", 1)
        return f"https://{owner}.github.io/{name}/"

    # czasem Pages już jest / inny błąd — spróbuj domyślnego URL
    print(f"! Pages API: {create.status_code} {create.text[:200]}")
    owner, name = repo.split("/", 1)
    return f"https://{owner}.github.io/{name}/"


def _put_file(
    client: httpx.Client,
    repo: str,
    branch: str,
    path: str,
    content: str,
    *,
    message: str,
) -> None:
    url = f"https://api.github.com/repos/{repo}/contents/{path}"
    sha = None
    r = client.get(url, params={"ref": branch})
    if r.status_code == 200:
        sha = r.json().get("sha")
    elif r.status_code != 404:
        r.raise_for_status()

    payload: dict[str, Any] = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": branch,
    }
    if sha:
        payload["sha"] = sha
    put = client.put(url, json=payload)
    if put.status_code >= 400:
        raise RuntimeError(f"GitHub {put.status_code}: {put.text[:300]}")
