from __future__ import annotations

import argparse
import json
import logging
import os
import sys

import requests

from . import harvest as hv
from . import planner, publisher, report, sales, site
from .config import DATA_DIR, SITE_DIR, STATE_DIR, load_categories, load_settings
from .db import connect, iso, kv_get, kv_set, utcnow
from .feed import FeedClient

log = logging.getLogger("app")


def _client(settings):
    z = settings["shop"]
    return FeedClient(z["feed_base"], z.get("request_delay_seconds", 3))


def notify(settings, text: str):
    url = os.environ.get("NOTIFY_WEBHOOK")
    if not url:
        return
    try:
        requests.post(url, json={"content": text[:1900], "text": text[:3900]}, timeout=15)
    except Exception as e:
        log.warning("notify failed: %s", e)


def step_summary(md: str):
    p = os.environ.get("GITHUB_STEP_SUMMARY")
    if p:
        with open(p, "a", encoding="utf-8") as f:
            f.write(md + "\n")


def set_github_secret(name: str, value: str) -> bool:
    tok, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    if not (tok and repo):
        return False
    from base64 import b64encode

    from nacl import encoding, public
    h = {"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json"}
    key = requests.get(f"https://api.github.com/repos/{repo}/actions/secrets/public-key", headers=h, timeout=30).json()
    box = public.SealedBox(public.PublicKey(key["key"].encode(), encoding.Base64Encoder()))
    enc = b64encode(box.encrypt(value.encode())).decode()
    r = requests.put(f"https://api.github.com/repos/{repo}/actions/secrets/{name}", headers=h,
                     json={"encrypted_value": enc, "key_id": key["key_id"]}, timeout=30)
    return r.status_code in (201, 204)


def cmd_probe(con, S, C, a):
    cl = _client(S)
    cat = C[0]
    items = cl.search(cat.qs, 1, 5, S["shop"].get("sort_period", 30))
    print(f"[search '{cat.qs}' p1] {len(items)} items")
    for it in items:
        print(json.dumps(it.__dict__, ensure_ascii=False)[:400])
    for st in S["shop"]["own_stores"]:
        own = cl.store(st, 1, 5)
        print(f"[store {st}] {len(own)} items", *(f"  {i.product_id} {i.title[:60]} store={i.store}" for i in own), sep="\n")
    return {"search_items": len(items)}


def cmd_harvest(con, S, C, a):
    today = utcnow().date()
    res = {}
    if a.own or today.weekday() == 0 or not con.execute("SELECT 1 FROM products WHERE is_own=1 LIMIT 1").fetchone():
        res["own_products"] = hv.harvest_own_stores(con, _client(S), S)
    res.update(hv.run_harvest(con, _client(S), C, S, today))
    return res


def cmd_plan(con, S, C, a):
    return planner.plan(con, S, C)


def cmd_post(con, S, C, a):
    return publisher.run_posts(con, S, C, per_run=a.per_run)


def cmd_hourly(con, S, C, a):
    return {"plan": cmd_plan(con, S, C, a), "post": cmd_post(con, S, C, a)}


def cmd_export_pin(con, S, C, a):
    pc = S["platforms"]["pin"]
    if pc.get("enabled") and pc.get("mode") == "rss":
        from .pinrss import export_rss
        return {"rss_items": export_rss(con, S)}
    if not pc.get("enabled") or pc.get("mode") != "csv":
        return {"skipped": "pin not in csv/rss mode"}
    first = kv_get(con, "pin_last_export") is None
    if not (a.force or first or utcnow().date().weekday() == int(pc.get("csv_export_weekday", 0))):
        return {"skipped": "not export day"}
    files = publisher.export_pin_csv(con, S, C, STATE_DIR / "exports" / "pin")
    kv_set(con, "pin_last_export", iso(utcnow()))
    con.commit()
    return {"files": [f.name for f in files]}


def cmd_pin_boards(con, S, C, a):
    from .boards import board_specs, cover_suggestions
    from .platforms.pin import export_boards_csv
    specs = board_specs(C, S)
    f = export_boards_csv(specs, cover_suggestions(con, specs), set(specs), STATE_DIR / "exports" / "pin",
                          utcnow().strftime("%Y%m%d"))
    res = {"boards": len(specs), "file": f.name}
    if S["platforms"]["pin"].get("mode") == "api":
        from .platforms.pin import Pin
        pin = Pin(S)
        before = set(pin.boards())
        for name, sp in specs.items():
            pin.board_id(name, sp["description"])
        res["created"] = sorted(set(pin.boards()) - before)
    for name, sp in specs.items():
        print(f"- {name}\n    {sp['description']}")
    return res


def cmd_metrics(con, S, C, a):
    return publisher.collect_metrics(con, S)


def cmd_import_sales(con, S, C, a):
    return sales.import_dir(con, DATA_DIR / "reports")


def cmd_report(con, S, C, a):
    st = report.build_stats(con, S, C)
    public = not S.get("privacy", {}).get("public_money", False)
    md_full = report.to_markdown(st)
    md = report.to_markdown(st, hide_money=public)
    out = STATE_DIR / "reports"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{utcnow().date().isoformat()}.md").write_text(md, encoding="utf-8")
    if S.get("privacy", {}).get("public_logs", False):
        step_summary(md)
        print(md)
    if S.get("notify", {}).get("daily_summary") or (st["alerts"] and S.get("notify", {}).get("on_error")):
        notify(S, md_full)
    return {"alerts": len(st["alerts"])}


def cmd_build_site(con, S, C, a):
    st = report.build_stats(con, S, C)
    if not S.get("privacy", {}).get("public_money", False):
        st = report.redact_money(st)
    return site.build(con, S, C, st, SITE_DIR)


def cmd_daily(con, S, C, a):
    out = {}
    for name, fn in [("harvest", cmd_harvest), ("plan", cmd_plan), ("export_pin", cmd_export_pin),
                     ("metrics", cmd_metrics), ("import_sales", cmd_import_sales), ("report", cmd_report),
                     ("site", cmd_build_site)]:
        try:
            out[name] = fn(con, S, C, a)
        except Exception as e:
            log.exception("daily stage %s failed", name)
            out[name] = {"error": str(e)[:300]}
    return out


def cmd_status(con, S, C, a):
    print(report.to_markdown(report.build_stats(con, S, C)))
    return {}


def cmd_threads_refresh(con, S, C, a):
    from .platforms.threads import refresh_token
    data = refresh_token(os.environ["THREADS_ACCESS_TOKEN"])
    new = data["access_token"]
    kv_set(con, "threads_token_set_at", iso(utcnow()))
    con.commit()
    if set_github_secret("THREADS_ACCESS_TOKEN", new):
        return {"refreshed": True, "secret_updated": True, "expires_in": data.get("expires_in")}
    print("NEW THREADS TOKEN (update the repo secret THREADS_ACCESS_TOKEN):\n" + new)
    return {"refreshed": True, "secret_updated": False}


def cmd_auth_pin(con, S, C, a):
    app_id, secret = os.environ["PIN_APP_ID"], os.environ["PIN_APP_SECRET"]
    redirect = a.redirect or "http://localhost:8085/"
    if not a.code:
        print("1) 開啟以下網址授權，完成後複製網址列 ?code= 後面的值：\n"
              f"{S['platforms']['pin']['oauth_url']}?client_id={app_id}&redirect_uri={redirect}"
              "&response_type=code&scope=boards:read,boards:write,pins:read,pins:write,user_accounts:read")
        print("2) 再執行：python -m app.cli auth-pin --code <CODE>")
        return {}
    r = requests.post(f"{S['platforms']['pin']['api']}/oauth/token", auth=(app_id, secret),
                      data={"grant_type": "authorization_code", "code": a.code, "redirect_uri": redirect}, timeout=30)
    d = r.json()
    print(json.dumps({k: d.get(k) for k in ("refresh_token", "refresh_token_expires_in", "scope")}, indent=1))
    return {}


def cmd_auth_tumblr(con, S, C, a):
    from requests_oauthlib import OAuth1Session
    ck, cs = os.environ["TUMBLR_CONSUMER_KEY"], os.environ["TUMBLR_CONSUMER_SECRET"]
    oa = OAuth1Session(ck, client_secret=cs, callback_uri="http://localhost:8085/")
    tok = oa.fetch_request_token("https://www.tumblr.com/oauth/request_token")
    print("開啟並授權：", oa.authorization_url("https://www.tumblr.com/oauth/authorize"))
    verifier = input("貼上跳轉網址中的 oauth_verifier：").strip()
    oa = OAuth1Session(ck, client_secret=cs, resource_owner_key=tok["oauth_token"],
                       resource_owner_secret=tok["oauth_token_secret"], verifier=verifier)
    acc = oa.fetch_access_token("https://www.tumblr.com/oauth/access_token")
    print(f"TUMBLR_TOKEN={acc['oauth_token']}\nTUMBLR_TOKEN_SECRET={acc['oauth_token_secret']}")
    return {}


def cmd_keygen(con, S, C, a):
    from .crypto import keygen
    print("把下面這串存成 GitHub repo secret APP_KEY（也請自己另外備份）：\n" + keygen())
    return {}


def cmd_encrypt_file(con, S, C, a):
    from pathlib import Path

    from .crypto import encrypt_file
    out = encrypt_file(Path(a.path))
    return {"written": str(out)}


def cmd_unpack(con, S, C, a):
    from pathlib import Path

    from .crypto import unpack_dir
    src = Path(a.path)
    return {"written": str(unpack_dir(src, src.with_suffix("")))}


def _local(a, *parts):
    from pathlib import Path

    from .config import ROOT
    return Path(a.out) if a.out else ROOT.joinpath("local", *parts)


def cmd_guide_draft(con, S, C, a):
    from .guides import draft
    if not a.category:
        return {"error": "use --category <key>", "categories": [c.key for c in C]}
    f, how = draft(con, S, C, a.category, a.count or 24, a.topic, _local(a, "drafts"), use_ai=not a.no_ai)
    return {"draft": str(f), "written_by": how, "next": f"edit it, then: python -m app.cli guide-publish --path {f}"}


def cmd_guide_publish(con, S, C, a):
    from pathlib import Path

    from .config import DATA_DIR
    from .guides import publish
    out = publish(Path(a.path), DATA_DIR / "guides")
    return {"encrypted": str(out), "next": "git add data/guides && git commit -m update && git push"}


def cmd_trends(con, S, C, a):
    from . import trends
    f = _local(a, f"trends-{utcnow().date().isoformat()}.html")
    f.parent.mkdir(parents=True, exist_ok=True)
    rows = trends.analyze(con, S, C, a.days or 28)
    f.write_text(trends.to_html(rows, S), encoding="utf-8")
    return {"report": str(f), "categories": len(rows), "top": [r["cat"].key for r in rows[:5]]}


def cmd_shorts(con, S, C, a):
    from .shorts import make_shorts
    return make_shorts(con, S, C, _local(a, "shorts"), a.count or 10)


def local_sync() -> dict:
    import subprocess

    from .config import ROOT
    from .crypto import decrypt_file, unpack_dir
    subprocess.run(["git", "fetch", "-q", "origin", "state"], cwd=ROOT, check=True)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    for name in ("state.enc", "data.sqlite.enc"):
        r = subprocess.run(["git", "show", f"origin/state:{name}"], cwd=ROOT, capture_output=True)
        if r.returncode == 0:
            f = STATE_DIR / name
            f.write_bytes(r.stdout)
            if name == "state.enc":
                unpack_dir(f, STATE_DIR)
            else:
                decrypt_file(f, STATE_DIR / "data.sqlite")
            return {"synced": name, "state_dir": str(STATE_DIR)}
    raise RuntimeError("no state found on origin/state")


COMMANDS = {
    "probe": cmd_probe, "harvest": cmd_harvest, "plan": cmd_plan, "post": cmd_post, "hourly": cmd_hourly,
    "daily": cmd_daily, "export-pin": cmd_export_pin, "pin-boards": cmd_pin_boards, "metrics": cmd_metrics,
    "import-sales": cmd_import_sales, "report": cmd_report, "build-site": cmd_build_site, "status": cmd_status,
    "threads-refresh": cmd_threads_refresh, "auth-pin": cmd_auth_pin, "auth-tumblr": cmd_auth_tumblr,
    "keygen": cmd_keygen, "encrypt-file": cmd_encrypt_file, "unpack": cmd_unpack,
    "guide-draft": cmd_guide_draft, "guide-publish": cmd_guide_publish, "trends": cmd_trends, "shorts": cmd_shorts,
    "local-sync": None,
}
NO_RUN_LOG = {"probe", "status", "auth-pin", "auth-tumblr", "keygen", "encrypt-file", "unpack",
              "guide-draft", "guide-publish", "trends", "shorts"}


def main(argv=None):
    ap = argparse.ArgumentParser(prog="app")
    ap.add_argument("command", choices=COMMANDS)
    ap.add_argument("--per-run", type=int, default=3, help="max posts per platform per run")
    ap.add_argument("--own", action="store_true", help="force own-store resync")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--code")
    ap.add_argument("--redirect")
    ap.add_argument("--path", help="file for encrypt-file / unpack / guide-publish")
    ap.add_argument("--category", help="guide-draft: category key")
    ap.add_argument("--topic", help="guide-draft: topic / angle")
    ap.add_argument("--count", type=int, help="guide-draft products / shorts videos")
    ap.add_argument("--days", type=int, help="trends: look-back days for risers")
    ap.add_argument("--out", help="output path for local commands")
    ap.add_argument("--no-ai", action="store_true", help="guide-draft without the Claude API")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if a.command == "local-sync":
        print(json.dumps(local_sync(), ensure_ascii=False, indent=1))
        return 0
    S, C = load_settings(), load_categories()
    con = connect(STATE_DIR / "data.sqlite")
    for old, new in (S.get("rename_platforms") or {}).items():
        for t in ("posts", "metrics"):
            con.execute(f"UPDATE {t} SET platform=? WHERE platform=?", (new, old))
        con.execute("UPDATE OR IGNORE kv SET k=replace(k, ?, ?)", (old, new))
    con.commit()
    started = iso(utcnow())
    ok, res = True, {}
    try:
        res = COMMANDS[a.command](con, S, C, a) or {}
    except Exception as e:
        ok, res = False, {"error": str(e)[:500]}
        log.exception("command failed")
        notify(S, f"❌ app {a.command} failed: {e}")
    finally:
        if a.command not in NO_RUN_LOG:
            con.execute("INSERT INTO runs(command,started_at,finished_at,ok,summary) VALUES(?,?,?,?,?)",
                        (a.command, started, iso(utcnow()), int(ok), json.dumps(res, ensure_ascii=False, default=str)[:2000]))
            con.commit()
    print(json.dumps(res, ensure_ascii=False, default=str, indent=1))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

