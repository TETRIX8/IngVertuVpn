# IngVertuVpn — веб-админка бота (04.11.2026). Монтируется в aiohttp-приложение бота: /admin/*
# Доступ: только через nginx NL (example.invalid/admin/), вход — одноразовая ссылка из бота, сессия 30 дней.
import time, json, os, hashlib, secrets, asyncio, re, io, csv, ipaddress
from datetime import datetime, timezone, timedelta
from aiohttp import web

B = None                                   # модуль бота (bot.py)
ALLOW_IPS = {"127.0.0.1", "::1"}
COOKIE = "ingvertu_admin_session"
SESSION_TTL = 30 * 86400
MSK = 3 * 3600
HERE = os.path.dirname(os.path.abspath(__file__))
_HDR = {"Cache-Control": "no-store", "X-Robots-Tag": "noindex, nofollow", "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}

def setup(app, mod):
    global B
    B = mod
    app.router.add_get("/admin", lambda r: web.HTTPFound("/admin/"))
    app.router.add_get("/admin/", page)
    app.router.add_get("/admin/login", login)
    app.router.add_get("/admin/logout", logout)
    app.router.add_route("*", "/admin/api/{name:.*}", api)

# ------------------------------------------------------------------ helpers
def _h(s): return hashlib.sha256(s.encode()).hexdigest()
def now(): return int(time.time())
def day0(ts=None):
    ts = now() if ts is None else ts
    return ((ts + MSK) // 86400) * 86400 - MSK
def dkey(ts): return (int(ts) + MSK) // 86400
def dlabel(k): return datetime.fromtimestamp(k * 86400, timezone.utc).strftime("%d.%m")

def _ip_ok(request):
    remote = request.remote or ""
    if remote in ALLOW_IPS: return True
    try: return ipaddress.ip_address(remote) in ipaddress.ip_network("172.18.0.0/16")
    except ValueError: return False

def _client_ip(request):
    return request.headers.get("X-Real-IP") or request.headers.get("X-Forwarded-For", "").split(",")[0].strip() or request.remote

def session_tg(request):
    tok = request.cookies.get(COOKIE)
    if not tok: return None
    c = B.db()
    r = c.execute("SELECT tg_id FROM admin_sessions WHERE token=? AND expires_at>?", (_h(tok), now())).fetchone()
    c.close()
    if not r or not B.is_admin(r[0]): return None
    return r[0]

def J(data, status=200):
    return web.json_response(data, status=status, headers=_HDR, dumps=lambda o: json.dumps(o, ensure_ascii=False, default=str))

def q1(c, sql, *a):
    r = c.execute(sql, a).fetchone()
    return (r[0] if r and r[0] is not None else 0)

def plan_title(pid):
    p = B.PLANS.get(pid or "")
    return p["title"] if p else (pid or "—")

def uname(username, first_name):
    if username: return "@" + username
    return first_name or ""

# ------------------------------------------------------------------ pages
async def page(request):
    if not _ip_ok(request): return web.Response(status=403, text="forbidden")
    html = open(os.path.join(HERE, "admin_web.html"), encoding="utf-8").read()
    tg = session_tg(request)
    html = html.replace("{%AUTH%}", "1" if tg else "0").replace("{%BOT%}", B.BOT_USERNAME)
    return web.Response(text=html, content_type="text/html", headers=_HDR)

async def login(request):
    if not _ip_ok(request): return web.Response(status=403, text="forbidden")
    t = request.query.get("t", "")
    c = B.db()
    r = c.execute("SELECT tg_id FROM admin_links WHERE token=? AND used=0 AND expires_at>?", (_h(t), now())).fetchone() if t else None
    if r:
        c.execute("UPDATE admin_links SET used=1 WHERE token=?", (_h(t),))
    c.commit(); c.close()
    if not r or not B.is_admin(r[0]):
        return web.HTTPFound("/admin/?e=link")
    raw = secrets.token_urlsafe(32)
    c = B.db()
    c.execute("DELETE FROM admin_sessions WHERE expires_at<?", (now(),))
    c.execute("INSERT INTO admin_sessions(token,tg_id,created_at,expires_at,ua,ip) VALUES(?,?,?,?,?,?)",
              (_h(raw), r[0], now(), now() + SESSION_TTL, (request.headers.get("User-Agent") or "")[:200], _client_ip(request)))
    c.commit(); c.close()
    B.log_event(r[0], "admin_login", _client_ip(request))
    resp = web.HTTPFound("/admin/")
    resp.set_cookie(COOKIE, raw, max_age=SESSION_TTL, path="/admin", secure=True, httponly=True, samesite="Lax")
    return resp

async def logout(request):
    tok = request.cookies.get(COOKIE)
    if tok:
        c = B.db(); c.execute("DELETE FROM admin_sessions WHERE token=?", (_h(tok),)); c.commit(); c.close()
    resp = web.HTTPFound("/admin/")
    resp.del_cookie(COOKIE, path="/admin")
    return resp

# ------------------------------------------------------------------ API router
async def api(request):
    if not _ip_ok(request): return J({"error": "forbidden"}, 403)
    tg = session_tg(request)
    if not tg: return J({"error": "auth"}, 401)
    if request.method != "GET" and request.headers.get("X-ZPA") != "1":
        return J({"error": "csrf"}, 403)
    name = request.match_info["name"].strip("/")
    fn = ROUTES.get((request.method, name.split("/")[0]))
    if not fn: return J({"error": "not found"}, 404)
    try:
        res = await fn(request, tg, name)
        return res if isinstance(res, web.StreamResponse) else J(res)
    except ValueError as e:
        return J({"error": str(e)}, 400)
    except Exception as e:
        B.log.exception(f"admin api {name}: {e}")
        return J({"error": f"Ошибка сервера: {type(e).__name__}: {e}"}, 500)

async def _body(request):
    try: return await request.json()
    except Exception: return {}

# ------------------------------------------------------------------ me / system
_CH_CNT = [0, None]
async def channel_members():
    if time.time() - _CH_CNT[0] < 300 and _CH_CNT[1] is not None: return _CH_CNT[1]
    try:
        _CH_CNT[1] = await B.bot.get_chat_member_count(B.channel_id()); _CH_CNT[0] = time.time()
    except Exception:
        pass
    return _CH_CNT[1]

async def a_me(request, tg, name):
    c = B.db(); r = c.execute("SELECT username, first_name FROM users WHERE tg_id=?", (tg,)).fetchone(); c.close()
    return {"tg_id": tg, "name": uname(*(r or (None, None))) or str(tg), "bot": B.BOT_USERNAME,
            "owner": tg in B.SEED_ADMINS}

async def a_system(request, tg, name):
    nodes = await B._me_nodes()
    return {"nodes": nodes, "channel": {"id": B.channel_id(), "url": B.channel_url(), "members": await channel_members(),
                                        "required": B.gate_on()},
            "yk": {"ready": B.yk_ready(), "intro": B.intro_ready()},
            "intro": {"on": B.intro_ready(), "offer": B.LAVA_INTRO.get("id"), "name": B.LAVA_INTRO.get("name"),
                      "rub": B.PLANS["intro"]["rub"], "days": B.PLANS["intro"]["days"], "enabled": B.cfg_on("intro_enabled", True)},
            "broadcasts_running": len(B._BC_RUN)}

# ------------------------------------------------------------------ dashboard
def _mrr(c):
    t = now(); s = 0.0
    for pid, n in c.execute("SELECT plan, COUNT(*) FROM users WHERE expires_at>? AND COALESCE(revoked,0)=0 "
                            "AND COALESCE(pay_count,0)>0 GROUP BY plan", (t,)).fetchall():
        p = B.PLANS.get(pid or "")
        if p and p.get("rub") and p.get("days") and pid not in B.SPECIAL_PIDS:
            s += n * p["rub"] * 30.0 / p["days"]
        elif pid == "intro":
            pass
    return round(s)

def _series(c, sql, since, days, *args):
    d = {int(k): v for k, v in c.execute(sql, (since,) + args).fetchall()}
    k0 = dkey(since)
    return [round(d.get(k0 + i, 0) or 0, 2) for i in range(days)]

async def a_dash(request, tg, name):
    days = max(1, min(365, int(request.query.get("days", 30))))
    t = now(); t0 = day0(); since = day0() - (days - 1) * 86400
    c = B.db()
    k = {
        "users": q1(c, "SELECT COUNT(*) FROM users"),
        "new_today": q1(c, "SELECT COUNT(*) FROM users WHERE created_at>=?", t0),
        "new_period": q1(c, "SELECT COUNT(*) FROM users WHERE created_at>=?", since),
        "active": q1(c, "SELECT COUNT(*) FROM users WHERE expires_at>? AND COALESCE(revoked,0)=0", t),
        "active_paid": q1(c, "SELECT COUNT(*) FROM users WHERE expires_at>? AND COALESCE(revoked,0)=0 AND COALESCE(pay_count,0)>0 AND plan!='intro'", t),
        "active_trial": q1(c, "SELECT COUNT(*) FROM users WHERE expires_at>? AND COALESCE(revoked,0)=0 AND plan='trial'", t),
        "active_intro": q1(c, "SELECT COUNT(*) FROM users WHERE expires_at>? AND COALESCE(revoked,0)=0 AND plan='intro'", t),
        "autopay": q1(c, "SELECT COUNT(*) FROM autopay WHERE active=1"),
        "ch_sub": q1(c, "SELECT COUNT(*) FROM users WHERE ch_sub=1"),
        "blocked": q1(c, "SELECT COUNT(*) FROM users WHERE blocked=1"),
        "expiring3": q1(c, "SELECT COUNT(*) FROM users WHERE expires_at>? AND expires_at<=? AND COALESCE(revoked,0)=0", t, t + 3 * 86400),
        "rev_today": q1(c, "SELECT SUM(amount_rub) FROM payments WHERE ts>=?", t0),
        "rev_period": q1(c, "SELECT SUM(amount_rub) FROM payments WHERE ts>=?", since),
        "rev_all": q1(c, "SELECT SUM(amount_rub) FROM payments"),
        "pays_period": q1(c, "SELECT COUNT(*) FROM payments WHERE ts>=?", since),
        "pays_today": q1(c, "SELECT COUNT(*) FROM payments WHERE ts>=?", t0),
        "payers_period": q1(c, "SELECT COUNT(DISTINCT tg_id) FROM payments WHERE ts>=?", since),
        "first_period": q1(c, "SELECT COUNT(*) FROM users WHERE first_paid_at>=?", since),
        "paid_ever": q1(c, "SELECT COUNT(*) FROM users WHERE COALESCE(pay_count,0)>0"),
        "mrr": _mrr(c),
    }
    newp = k["new_period"]
    k["conv_period"] = round(100.0 * q1(c, "SELECT COUNT(*) FROM users WHERE created_at>=? AND first_paid_at IS NOT NULL", since) / newp, 1) if newp else 0
    k["avg_check"] = round(k["rev_period"] / k["pays_period"]) if k["pays_period"] else 0
    k["arppu"] = round(k["rev_period"] / k["payers_period"]) if k["payers_period"] else 0
    ser = {
        "labels": [dlabel(dkey(since) + i) for i in range(days)],
        "new": _series(c, "SELECT (created_at+10800)/86400, COUNT(*) FROM users WHERE created_at>=? GROUP BY 1", since, days),
        "rev": _series(c, "SELECT (ts+10800)/86400, SUM(amount_rub) FROM payments WHERE ts>=? GROUP BY 1", since, days),
        "pays": _series(c, "SELECT (ts+10800)/86400, COUNT(*) FROM payments WHERE ts>=? GROUP BY 1", since, days),
        "first": _series(c, "SELECT (first_paid_at+10800)/86400, COUNT(*) FROM users WHERE first_paid_at>=? GROUP BY 1", since, days),
        "sub": _series(c, "SELECT (ch_sub_at+10800)/86400, COUNT(*) FROM users WHERE ch_sub_at>=? GROUP BY 1", since, days),
    }
    recent_pay = [dict(ts=r[0], tg_id=r[1], name=uname(r[2], r[3]), plan=plan_title(r[4]), provider=r[5], amount=r[6], kind=r[7])
                  for r in c.execute("SELECT p.ts,p.tg_id,u.username,u.first_name,p.pid,p.provider,p.amount_rub,p.kind FROM payments p "
                                     "LEFT JOIN users u ON u.tg_id=p.tg_id ORDER BY p.ts DESC LIMIT 8").fetchall()]
    recent_users = [dict(ts=r[0], tg_id=r[1], name=uname(r[2], r[3]), src=r[4] or ("реферал" if r[5] else ""), ch=r[6])
                    for r in c.execute("SELECT created_at,tg_id,username,first_name,src,referrer,ch_sub FROM users "
                                       "ORDER BY created_at DESC LIMIT 8").fetchall()]
    c.close()
    return {"kpi": k, "series": ser, "recent_pay": recent_pay, "recent_users": recent_users, "days": days,
            "channel_members": await channel_members()}

# ------------------------------------------------------------------ funnel
async def a_funnel(request, tg, name):
    days = int(request.query.get("days", 30))
    t = now(); since = day0() - (days - 1) * 86400 if days > 0 else 0
    c = B.db()
    W = "created_at>=?"
    n = lambda extra="1=1", *a: q1(c, f"SELECT COUNT(*) FROM users WHERE {W} AND ({extra})", since, *a)
    N = n()
    stages = [
        {"k": "start", "t": "Запустили бота", "v": N},
        {"k": "ch", "t": "Подписались на канал", "v": n("ch_sub_at IS NOT NULL OR ch_sub=1")},
        {"k": "access", "t": "Получили доступ (пробный / старт / оплата)", "v": n("trial_used=1 OR intro_used=1 OR COALESCE(pay_count,0)>0")},
        {"k": "paid", "t": "Первая оплата", "v": n("first_paid_at IS NOT NULL")},
        {"k": "repeat", "t": "Оплатили повторно", "v": n("COALESCE(pay_count,0)>=2")},
        {"k": "active", "t": "Активны сейчас", "v": n("expires_at>? AND COALESCE(revoked,0)=0", t)},
    ]
    trial = n("trial_used=1"); trial_paid = n("trial_used=1 AND COALESCE(pay_count,0)>0")
    intro = n("intro_used=1"); intro_renew = n("intro_used=1 AND COALESCE(pay_count,0)>=2")
    ev = lambda kind: q1(c, "SELECT COUNT(DISTINCT tg_id) FROM events WHERE kind=? AND ts>=?", kind, since)
    ap_off_user = q1(c, "SELECT COUNT(DISTINCT tg_id) FROM events WHERE kind='autopay_off' AND meta='user' AND ts>=?", since)
    ap_fail = ev("autopay_fail"); iv, ic = ev("intro_view"), ev("intro_click")
    gaps = [r[0] for r in c.execute(f"SELECT first_paid_at-created_at FROM users WHERE {W} AND first_paid_at IS NOT NULL "
                                    "AND first_paid_at>=created_at ORDER BY 1", (since,)).fetchall()]
    median_h = round(gaps[len(gaps) // 2] / 3600, 1) if gaps else None
    agg = {}
    for s_, ch, paid, rev in c.execute(
            "SELECT COALESCE(NULLIF(src,''), CASE WHEN referrer IS NOT NULL THEN '👥 рефералы' ELSE '🌱 органика' END), "
            "CASE WHEN ch_sub_at IS NOT NULL OR ch_sub=1 THEN 1 ELSE 0 END, CASE WHEN first_paid_at IS NOT NULL THEN 1 ELSE 0 END, "
            "(SELECT COALESCE(SUM(p.amount_rub),0) FROM payments p WHERE p.tg_id=users.tg_id) "
            f"FROM users WHERE {W}", (since,)).fetchall():
        a_ = agg.setdefault(s_, {"src": s_, "n": 0, "ch": 0, "paid": 0, "rev": 0.0})
        a_["n"] += 1; a_["ch"] += ch; a_["paid"] += paid; a_["rev"] += rev or 0
    src = sorted(agg.values(), key=lambda x: -x["n"])[:30]
    for x in src: x["rev"] = round(x["rev"])
    cohorts = []
    w0 = day0() - ((datetime.fromtimestamp(day0() + MSK, timezone.utc).weekday()) * 86400)
    for i in range(8):
        a = w0 - i * 7 * 86400; b = a + 7 * 86400
        cn = q1(c, "SELECT COUNT(*) FROM users WHERE created_at>=? AND created_at<?", a, b)
        if not cn and i > 0:
            cohorts.append({"week": datetime.fromtimestamp(a + MSK, timezone.utc).strftime("%d.%m"), "n": 0, "ch": 0, "paid": 0, "active": 0}); continue
        cohorts.append({"week": datetime.fromtimestamp(a + MSK, timezone.utc).strftime("%d.%m"), "n": cn,
                        "ch": q1(c, "SELECT COUNT(*) FROM users WHERE created_at>=? AND created_at<? AND (ch_sub_at IS NOT NULL OR ch_sub=1)", a, b),
                        "paid": q1(c, "SELECT COUNT(*) FROM users WHERE created_at>=? AND created_at<? AND first_paid_at IS NOT NULL", a, b),
                        "active": q1(c, "SELECT COUNT(*) FROM users WHERE created_at>=? AND created_at<? AND expires_at>?", a, b, t)})
    c.close()
    return {"days": days, "stages": stages,
            "extra": {"trial": trial, "trial_paid": trial_paid, "intro": intro, "intro_renew": intro_renew,
                      "intro_view": iv, "intro_click": ic, "ap_off_user": ap_off_user,
                      "ap_fail": ap_fail, "median_h": median_h},
            "sources": src, "cohorts": cohorts}

# ------------------------------------------------------------------ payments
def _pay_where(request):
    days = int(request.query.get("days", 30)); prov = request.query.get("provider", ""); qq = request.query.get("q", "").strip()
    w, a = ["1=1"], []
    if days > 0: w.append("p.ts>=?"); a.append(day0() - (days - 1) * 86400)
    if prov: w.append("p.provider=?"); a.append(prov)
    if qq:
        if qq.lstrip("@").isdigit(): w.append("p.tg_id=?"); a.append(int(qq.lstrip("@")))
        else: w.append("(u.username LIKE ? OR u.first_name LIKE ?)"); a += [f"%{qq.lstrip('@')}%"] * 2
    return " AND ".join(w), a

async def a_payments(request, tg, name):
    w, a = _pay_where(request)
    off = max(0, int(request.query.get("offset", 0)))
    c = B.db()
    J_ = "FROM payments p LEFT JOIN users u ON u.tg_id=p.tg_id WHERE " + w
    rows = [dict(id=r[0], ts=r[1], tg_id=r[2], name=uname(r[3], r[4]), pid=r[5], plan=plan_title(r[5]), provider=r[6],
                 amount=r[7], currency=r[8], stars=r[9], kind=r[10])
            for r in c.execute(f"SELECT p.id,p.ts,p.tg_id,u.username,u.first_name,p.pid,p.provider,p.amount_rub,p.currency,p.stars,p.kind "
                               f"{J_} ORDER BY p.ts DESC LIMIT 50 OFFSET ?", a + [off]).fetchall()]
    tot = c.execute(f"SELECT COUNT(*), COALESCE(SUM(p.amount_rub),0), COUNT(DISTINCT p.tg_id) {J_}", a).fetchone()
    byp = [dict(k=r[0] or "—", n=r[1], s=round(r[2] or 0)) for r in c.execute(f"SELECT p.provider, COUNT(*), SUM(p.amount_rub) {J_} GROUP BY 1 ORDER BY 3 DESC", a).fetchall()]
    byplan = [dict(k=plan_title(r[0]), n=r[1], s=round(r[2] or 0)) for r in c.execute(f"SELECT p.pid, COUNT(*), SUM(p.amount_rub) {J_} GROUP BY 1 ORDER BY 3 DESC", a).fetchall()]
    bykind = {r[0] or "—": dict(n=r[1], s=round(r[2] or 0)) for r in c.execute(f"SELECT p.kind, COUNT(*), SUM(p.amount_rub) {J_} GROUP BY 1", a).fetchall()}
    provs = [r[0] for r in c.execute("SELECT DISTINCT provider FROM payments WHERE provider IS NOT NULL ORDER BY 1").fetchall()]
    c.close()
    return {"rows": rows, "total": {"n": tot[0], "sum": round(tot[1]), "payers": tot[2]}, "by_provider": byp,
            "by_plan": byplan, "by_kind": bykind, "providers": provs, "offset": off}

# ------------------------------------------------------------------ users
USER_SEGS = dict(B_SEGS=None)
def _seg_sql(seg):
    if seg == "blocked": return "COALESCE(blocked,0)=1"
    if seg not in B.SEGMENTS: return "1=1"
    return B.SEGMENTS[seg][1].replace(":now", str(now())).replace(":admins", ",".join(map(str, B.admin_ids())) or "0")

async def a_users(request, tg, name):
    seg = request.query.get("seg", "all"); qq = request.query.get("q", "").strip(); sort = request.query.get("sort", "new")
    off = max(0, int(request.query.get("offset", 0)))
    w, a = [_seg_sql(seg)], []
    if qq:
        s = qq.lstrip("@")
        if s.isdigit(): w.append("(tg_id=? OR CAST(tg_id AS TEXT) LIKE ?)"); a += [int(s), s + "%"]
        elif re.fullmatch(r"[0-9a-fA-F-]{8,40}", s): w.append("uuid LIKE ?"); a.append(s + "%")
        else: w.append("(username LIKE ? OR first_name LIKE ?)"); a += [f"%{s}%"] * 2
    order = {"new": "created_at DESC", "exp": "expires_at DESC", "paid": "total DESC", "seen": "last_seen DESC",
             "pays": "COALESCE(pay_count,0) DESC"}.get(sort, "created_at DESC")
    c = B.db(); ww = " AND ".join(w)
    total = q1(c, f"SELECT COUNT(*) FROM users WHERE {ww}", *a)
    rows = []
    for r in c.execute(f"SELECT tg_id,username,first_name,plan,expires_at,COALESCE(pay_count,0),created_at,ch_sub,blocked,referrer,"
                       f"(SELECT COALESCE(SUM(amount_rub),0) FROM payments p WHERE p.tg_id=users.tg_id) total,"
                       f"(SELECT active FROM autopay a WHERE a.tg_id=users.tg_id) ap, last_seen, revoked "
                       f"FROM users WHERE {ww} ORDER BY {order} LIMIT 50 OFFSET ?", a + [off]).fetchall():
        rows.append(dict(tg_id=r[0], name=uname(r[1], r[2]), plan=plan_title(r[3]), exp=r[4], pays=r[5], created=r[6],
                         ch=r[7], blocked=r[8], referrer=r[9], total=round(r[10] or 0), autopay=bool(r[11]), seen=r[12],
                         revoked=r[13]))
    c.close()
    return {"rows": rows, "total": total, "offset": off}

async def a_user(request, tg, name):
    uid = int(name.split("/")[1])
    c = B.db()
    cols = ["tg_id", "uuid", "plan", "expires_at", "created_at", "trial_used", "referrer", "revoked", "first_paid_at",
            "pay_count", "trial_at", "username", "first_name", "last_seen", "ch_sub", "ch_sub_at", "intro_used", "src", "blocked"]
    r = c.execute(f"SELECT {','.join(cols)} FROM users WHERE tg_id=?", (uid,)).fetchone()
    if not r: c.close(); raise ValueError("Пользователь не найден")
    u = dict(zip(cols, r))
    u["name"] = uname(u["username"], u["first_name"]); u["plan_title"] = plan_title(u["plan"])
    u["me_url"] = B.me_url(u["uuid"]) if u["uuid"] else None
    u["sub_url"] = B.sub_url(u["uuid"]) if u["uuid"] else None
    pays = [dict(ts=x[0], plan=plan_title(x[1]), provider=x[2], amount=x[3], currency=x[4], stars=x[5], kind=x[6])
            for x in c.execute("SELECT ts,pid,provider,amount_rub,currency,stars,kind FROM payments WHERE tg_id=? ORDER BY ts DESC LIMIT 100", (uid,)).fetchall()]
    inv = [dict(tg_id=x[0], name=uname(x[1], x[2]), created=x[3], days=x[4], paid=x[5])
           for x in c.execute("SELECT u.tg_id,u.username,u.first_name,u.created_at,rc.days,COALESCE(u.pay_count,0) FROM users u "
                              "LEFT JOIN ref_credits rc ON rc.invitee=u.tg_id WHERE u.referrer=? ORDER BY u.created_at DESC LIMIT 100", (uid,)).fetchall()]
    ev = [dict(ts=x[0], kind=x[1], meta=x[2]) for x in c.execute("SELECT ts,kind,meta FROM events WHERE tg_id=? ORDER BY ts DESC LIMIT 40", (uid,)).fetchall()]
    promos = [x[0] for x in c.execute("SELECT code FROM promo_redemptions WHERE tg_id=?", (uid,)).fetchall()]
    ref = None
    if u["referrer"]:
        rr = c.execute("SELECT username, first_name FROM users WHERE tg_id=?", (u["referrer"],)).fetchone()
        ref = {"tg_id": u["referrer"], "name": uname(*(rr or (None, None)))}
    c.close()
    ap = B.autopay_get(uid)
    if ap: ap.pop("method_id", None)
    u["total_paid"] = round(sum(p["amount"] or 0 for p in pays))
    u["is_admin"] = B.is_admin(uid)
    ov, pd = B.dev_limit_get(uid); u["dev_limit"] = ov; u["dev_limit_plan"] = pd
    return {"user": u, "payments": pays, "invited": inv, "events": ev, "promos": promos, "autopay": ap, "ref": ref,
            "plans": [{"k": k, "t": p["title"]} for k, p in B.PLANS.items()]}

async def a_user_action(request, tg, name):
    uid = int(name.split("/")[1]); b = await _body(request); act = b.get("action")
    u = B.get_user(uid)
    if not u: raise ValueError("Пользователь не найден")
    msg = "Готово"
    if act == "add_days":
        d = int(b.get("value") or 0)
        if not 1 <= d <= 3650: raise ValueError("Дней: от 1 до 3650")
        _, exp = await B.add_days(uid, d)
        msg = f"+{d} дн., до {datetime.fromtimestamp(exp).strftime('%d.%m.%Y')}"
        if b.get("notify"):
            try: await B.bot.send_message(uid, f"🎁 <b>Тебе начислено +{d} {B.days_word(d)} IngVertuVpn</b>\nПодписка активна до "
                                                f"{datetime.fromtimestamp(exp).strftime('%d.%m.%Y')}.", reply_markup=B.main_menu(uid))
            except Exception: pass
    elif act == "sub_days":
        d = int(b.get("value") or 0)
        if not 1 <= d <= 3650: raise ValueError("Дней: от 1 до 3650")
        exp = int((u["expires_at"] or now()) - d * 86400)
        B.db_set(uid, expires_at=exp); await asyncio.to_thread(B.rw_sync_now, uid)
        msg = f"−{d} дн., до {datetime.fromtimestamp(exp).strftime('%d.%m.%Y')}"
    elif act == "set_exp":
        try: d = datetime.strptime(str(b.get("value")), "%Y-%m-%d")
        except Exception: raise ValueError("Дата в формате ГГГГ-ММ-ДД")
        exp = int(d.replace(hour=23, minute=59, tzinfo=timezone(timedelta(hours=3))).timestamp())
        if not u["uuid"]: await B.add_days(uid, 0)
        B.db_set(uid, expires_at=exp, **({"revoked": 0, "rem3": 0, "rem1": 0, "rem3h": 0} if exp > now() else {}))
        await asyncio.to_thread(B.rw_sync_now, uid)
        msg = f"Доступ до {d.strftime('%d.%m.%Y')}"
    elif act == "set_plan":
        pid = b.get("value")
        if pid not in B.PLANS: raise ValueError("Неизвестный тариф")
        B.db_set(uid, plan=pid); msg = f"Тариф: {plan_title(pid)}"
    elif act == "revoke":
        B.db_set(uid, revoked=1); await asyncio.to_thread(B.rw_sync_now, uid); msg = "Доступ отключён"
    elif act == "restore":
        B.db_set(uid, revoked=0); await asyncio.to_thread(B.rw_sync_now, uid); msg = "Доступ восстановлен"
    elif act == "reset_trial":
        B.db_set(uid, trial_used=0); msg = "Пробный снова доступен"
    elif act == "reset_intro":
        B.db_set(uid, intro_used=0); msg = "«Старт» снова доступен (если нет оплат)"
    elif act == "autopay_off":
        B.autopay_off(uid, f"admin:{tg}"); msg = "Автопродление отключено"
    elif act == "message":
        text = (b.get("value") or "").strip()
        if not text: raise ValueError("Пустое сообщение")
        try: await B.bot.send_message(uid, text)
        except Exception as e: raise ValueError(f"Не доставлено: {e}")
        msg = "Сообщение отправлено"
    elif act == "set_devices":   # 05.11.2026: "" = по тарифу, 0 = без лимита, 1..20
        v = str(b.get("value") if b.get("value") is not None else "").strip()
        if v == "": nv = None
        else:
            nv = int(v)
            if not 0 <= nv <= 20: raise ValueError("Устройств: от 0 до 20 (0 = без лимита)")
        B.dev_limit_set(uid, nv); await asyncio.to_thread(B.rw_sync_now, uid)
        msg = "Лимит устройств: " + ("по тарифу" if nv is None else ("без лимита" if nv == 0 else str(nv)))
    elif act == "dev_del":
        hw = str(b.get("value") or "").strip()
        if not hw: raise ValueError("Не указано устройство")
        await asyncio.to_thread(B.rw_device_delete, uid, hw); msg = "Устройство удалено"
    elif act == "admin_link":
        if tg not in B.SEED_ADMINS and not B.is_admin(tg): raise ValueError("Нет прав")
        msg = "—"
    else:
        raise ValueError("Неизвестное действие")
    B.log_event(uid, f"admin:{act}", f"by {tg}: {str(b.get('value') or '')[:200]}")
    return {"ok": True, "msg": msg}

async def a_uvpn(request, tg, name):
    uid = int(name.split("/")[1])
    r = await B.user_vpn(uid)
    return {"vpn": r}

# ------------------------------------------------------------------ referrals
async def a_refs(request, tg, name):
    c = B.db()
    tot = {"invited": q1(c, "SELECT COUNT(*) FROM users WHERE referrer IS NOT NULL"),
           "credited": q1(c, "SELECT COUNT(*) FROM ref_credits WHERE days>0"),
           "days": q1(c, "SELECT SUM(days) FROM ref_credits WHERE days>0"),
           "capped": q1(c, "SELECT COUNT(*) FROM ref_credits WHERE days=0"),
           "invited_paid": q1(c, "SELECT COUNT(*) FROM users WHERE referrer IS NOT NULL AND COALESCE(pay_count,0)>0"),
           "rev": round(q1(c, "SELECT SUM(amount_rub) FROM payments WHERE tg_id IN (SELECT tg_id FROM users WHERE referrer IS NOT NULL)")),
           "referrers": q1(c, "SELECT COUNT(DISTINCT referrer) FROM users WHERE referrer IS NOT NULL")}
    top = [dict(tg_id=r[0], name=uname(r[1], r[2]), invited=r[3], credited=r[4] or 0, days=r[5] or 0, paid=r[6] or 0)
           for r in c.execute("SELECT u.referrer, r.username, r.first_name, COUNT(*), "
                              "(SELECT COUNT(*) FROM ref_credits rc WHERE rc.referrer=u.referrer AND rc.days>0), "
                              "(SELECT SUM(days) FROM ref_credits rc WHERE rc.referrer=u.referrer AND rc.days>0), "
                              "SUM(CASE WHEN COALESCE(u.pay_count,0)>0 THEN 1 ELSE 0 END) "
                              "FROM users u LEFT JOIN users r ON r.tg_id=u.referrer WHERE u.referrer IS NOT NULL "
                              "GROUP BY u.referrer ORDER BY 4 DESC LIMIT 50").fetchall()]
    c.close()
    return {"total": tot, "top": top, "cfg": {"days": B.ref_days(), "max": B.ref_max(), "mode": B.ref_mode()}}

# ------------------------------------------------------------------ autopay
async def a_autopay(request, tg, name):
    t = now(); c = B.db()
    rows = [dict(tg_id=r[0], name=uname(r[1], r[2]), card=r[3], amount=r[4], next_at=r[5], active=r[6], fails=r[7],
                 err=B.YK_FAIL_RU.get(r[8], r[8]) if r[8] else None, created=r[9], off_at=r[10], exp=r[11])
            for r in c.execute("SELECT a.tg_id,u.username,u.first_name,a.card,a.amount,a.next_at,a.active,a.fails,a.last_err,"
                               "a.created_at,a.off_at,u.expires_at FROM autopay a LEFT JOIN users u ON u.tg_id=a.tg_id "
                               "ORDER BY a.active DESC, a.next_at ASC LIMIT 300").fetchall()]
    tot = {"active": q1(c, "SELECT COUNT(*) FROM autopay WHERE active=1"),
           "off": q1(c, "SELECT COUNT(*) FROM autopay WHERE active=0"),
           "due24": q1(c, "SELECT SUM(amount) FROM autopay WHERE active=1 AND next_at<=?", t + 86400),
           "due7": q1(c, "SELECT SUM(amount) FROM autopay WHERE active=1 AND next_at<=?", t + 7 * 86400),
           "failing": q1(c, "SELECT COUNT(*) FROM autopay WHERE active=1 AND fails>0"),
           "renew_rev": round(q1(c, "SELECT SUM(amount_rub) FROM payments WHERE provider LIKE 'Автоплатёж%'"))}
    c.close()
    return {"rows": rows, "total": tot, "yk_ready": B.yk_ready()}

# ------------------------------------------------------------------ promos
async def a_promos(request, tg, name):
    c = B.db()
    rows = []
    for code, days, mx, used in c.execute("SELECT code, days, max_uses, used FROM promos ORDER BY rowid DESC").fetchall():
        paid = q1(c, "SELECT COUNT(*) FROM promo_redemptions pr JOIN users u ON u.tg_id=pr.tg_id WHERE pr.code=? AND COALESCE(u.pay_count,0)>0", code)
        rows.append(dict(code=code, days=days, max=mx, used=used, paid=paid))
    c.close()
    return {"rows": rows}

async def a_promo_save(request, tg, name):
    b = await _body(request); code = re.sub(r"[^0-9A-ZА-ЯЁ_-]", "", str(b.get("code", "")).strip().upper())
    if b.get("delete"):
        c = B.db(); c.execute("DELETE FROM promos WHERE code=?", (code,)); c.commit(); c.close(); return {"ok": True}
    days, mx = int(b.get("days") or 0), int(b.get("max") or 0)
    if not code: raise ValueError("Код: латиница/кириллица, цифры, _ и -")
    if not 1 <= days <= 3650 or not 1 <= mx <= 100000: raise ValueError("Дни: 1–3650, лимит: 1–100000")
    c = B.db()
    ex = c.execute("SELECT used FROM promos WHERE code=?", (code,)).fetchone()
    c.execute("INSERT OR REPLACE INTO promos(code,days,max_uses,used) VALUES(?,?,?,?)", (code, days, mx, ex[0] if ex else 0))
    c.commit(); c.close()
    return {"ok": True}

# ------------------------------------------------------------------ settings
def _settings_schema():
    plans = [(k, p["title"]) for k, p in B.PLANS.items() if k not in B.SPECIAL_PIDS]
    S = [
        {"g": "📢 Обязательная подписка", "items": [
            {"k": "ch_required", "t": "Требовать подписку на канал", "type": "bool", "v": B.gate_on()},
            {"k": "ch_id", "t": "ID канала", "type": "str", "v": str(B.channel_id())},
            {"k": "ch_url", "t": "Ссылка на канал", "type": "str", "v": B.channel_url()}]},
        {"g": "👥 Реферальная программа", "items": [
            {"k": "ref_mode", "t": "За что начислять", "type": "select", "v": B.ref_mode(),
             "opts": [["join", "Друг запустил бота и подписался на канал"], ["pay", "Друг оплатил подписку"]]},
            {"k": "ref_bonus_days", "t": "Дней за друга (режим «подписка»)", "type": "int", "v": B.ref_days()},
            {"k": "ref_max", "t": "Максимум засчитанных друзей", "type": "int", "v": B.ref_max()}]},
        {"g": "📣 Умные авто-рассылки", "items": [
            {"k": "push_on", "t": "Авто-рассылки включены (подарок, «не подключился», продление, возврат)", "type": "bool", "v": B.cfg_on("push_on", True)},
            {"k": "push_batch", "t": "Сообщений на сценарий за 10 минут", "type": "int", "v": B.cfg_int("push_batch", 40)}],
         "note": "Шлём только 10:00–21:00 МСК и не чаще 1 сообщения в 20 ч на человека. Отправлено за 7 дней: "
                 + (", ".join(f"{k} — {v}" for k, v in B.push_stats().items()) or "пока ничего")},
        {"g": "🔥 «Старт» и пробный", "items": [
            {"k": "intro_enabled", "t": "Предложение «Старт» включено", "type": "bool", "v": B.cfg_on("intro_enabled", True)},
            {"k": "intro_days", "t": "Дней доступа за «Старт»", "type": "int", "v": B.PLANS["intro"]["days"]},
            {"k": "intro_stars", "t": "Цена «Старта» в Stars, ⭐", "type": "int", "v": B.PLANS["intro"]["stars"]},
            {"k": "intro_offer", "t": "ID оффера Lava (пусто = найти автоматически)", "type": "str", "v": str(B.cfg("intro_offer", ""))},
            {"k": "intro_only_first", "t": "Первая покупка — только «Старт» (новым сразу открывается он)", "type": "bool", "v": B.cfg_on("intro_only_first", True)},
            {"k": "free_trial", "t": "Бесплатный пробный", "type": "select", "v": str(B.cfg("free_trial", "auto")),
             "opts": [["auto", "Авто: скрыт, когда работает «Старт»"], ["on", "Всегда показывать"], ["off", "Выключен"]]},
            {"k": "trial_days", "t": "Дней бесплатного пробного", "type": "int", "v": B.trial_days()}],
         "note": (f"Оплата «Старта» — разовая через Lava (СБП и карта) и Stars, без автосписаний. "
                  + (f"Оффер Lava: «{B.LAVA_INTRO.get('name')}» — {B.PLANS['intro']['rub']} ₽ (цена берётся из Lava)."
                     if B.LAVA_INTRO.get("id") else
                     "Оффер в Lava не найден: создайте в Lava товар «IngVertuVpn — 7 дней» за 50 ₽ — бот подхватит его сам за ≤5 минут."))},
        {"g": "💰 Цены тарифов", "items": sum([[{"k": f"{k}_rub", "t": f"{t} · ₽", "type": "int", "v": B.PLANS[k]["rub"]},
                                               {"k": f"{k}_stars", "t": f"{t} · ⭐", "type": "int", "v": B.PLANS[k]["stars"]}]
                                              for k, t in plans], [])},
    ]
    return S

async def a_settings(request, tg, name):
    return {"groups": _settings_schema()}

async def a_settings_save(request, tg, name):
    b = await _body(request)
    allowed = {it["k"]: it for g in _settings_schema() for it in g["items"]}
    changed = []
    for k, v in (b or {}).items():
        it = allowed.get(k)
        if not it: raise ValueError(f"Нельзя менять {k}")
        typ = it["type"]
        if typ == "bool": v = "1" if v in (True, 1, "1", "true", "on") else "0"
        elif typ == "int":
            try: v = int(v)
            except Exception: raise ValueError(f"«{it['t']}»: нужно число")
            if v < 0 or v > 1000000: raise ValueError(f"«{it['t']}»: недопустимое значение")
            if k in ("intro_days", "trial_days") and not 1 <= v <= 60: raise ValueError(f"«{it['t']}»: 1–60")
            if k == "intro_rub" and v < 1: raise ValueError("Цена первой оплаты — минимум 1 ₽")
        elif typ == "select":
            if v not in [o[0] for o in it["opts"]]: raise ValueError(f"«{it['t']}»: недопустимое значение")
        elif typ == "secret":
            v = str(v).strip()
            if not v or v.startswith("••••"): continue
        else:
            v = str(v).strip()[:300]
            if k == "ch_id" and not re.fullmatch(r"-?\d+", v): raise ValueError("ID канала — число вида -100…")
        B.cfg_set(k, v); changed.append(k)
    if changed: B.log_event(tg, "admin:settings", ",".join(changed))
    if "ch_id" in changed or "ch_required" in changed: B._SUB_CACHE.clear()
    if "intro_offer" in changed or "intro_enabled" in changed:
        await B.lava_find_intro(); B.apply_settings()
    return {"ok": True, "changed": changed, "groups": _settings_schema()}

async def a_yk_test(request, tg, name):
    if not B.yk_ready(): raise ValueError("Сначала укажите shopId и секретный ключ")
    try:
        me = await B.yk_req("GET", "/me")
    except Exception as e:
        raise ValueError(f"ЮKassa не ответила: {e}")
    return {"ok": True, "account": me.get("account_id"), "status": me.get("status"), "test": me.get("test"),
            "fiscalization": (me.get("fiscalization") or {}).get("enabled"),
            "methods": me.get("payment_methods")}

# ------------------------------------------------------------------ admins
async def a_admins(request, tg, name):
    c = B.db(); extra = [r[0] for r in c.execute("SELECT tg_id FROM admins").fetchall()]
    names = {r[0]: uname(r[1], r[2]) for r in c.execute("SELECT tg_id,username,first_name FROM users WHERE tg_id IN (%s)" %
                                                          ",".join(map(str, list(B.SEED_ADMINS) + extra or [0]))).fetchall()}
    sess = [dict(tg_id=r[0], created=r[1], ip=r[2], ua=r[3]) for r in
            c.execute("SELECT tg_id, created_at, ip, ua FROM admin_sessions WHERE expires_at>? ORDER BY created_at DESC LIMIT 20", (now(),)).fetchall()]
    c.close()
    return {"seed": [{"tg_id": a, "name": names.get(a, "")} for a in sorted(B.SEED_ADMINS)],
            "extra": [{"tg_id": a, "name": names.get(a, "")} for a in extra if a not in B.SEED_ADMINS], "sessions": sess}

async def a_admins_save(request, tg, name):
    b = await _body(request); tid = int(b.get("tg_id") or 0)
    if not tid: raise ValueError("Укажите Telegram ID")
    if b.get("remove"):
        if tid in B.SEED_ADMINS: raise ValueError("Владельца убрать нельзя")
        if tid == tg: raise ValueError("Нельзя убрать самого себя")
        B.rm_admin(tid)
        c = B.db(); c.execute("DELETE FROM admin_sessions WHERE tg_id=?", (tid,)); c.commit(); c.close()
    else:
        B.add_admin(tid)
        try: await B.bot.send_message(tid, "👑 Вам выдан доступ администратора IngVertuVpn.\nВеб-панель: /panel")
        except Exception: pass
    B.log_event(tg, "admin:admins", f"{'rm' if b.get('remove') else 'add'} {tid}")
    return {"ok": True}

async def a_sessions_reset(request, tg, name):
    c = B.db(); c.execute("DELETE FROM admin_sessions WHERE tg_id=? AND token!=?", (tg, _h(request.cookies.get(COOKIE, "")))); c.commit(); c.close()
    return {"ok": True}

# ------------------------------------------------------------------ broadcasts
async def a_bcs(request, tg, name):
    c = B.db()
    rows = [dict(id=r[0], ts=r[1], by=r[2], seg=B.SEGMENTS.get(r[3], (r[3],))[0], text=(r[4] or "")[:300], photo=bool(r[5]),
                 btn=r[6], total=r[7], pos=r[8], sent=r[9], failed=r[10], blocked=r[11], status=r[12], fin=r[13])
            for r in c.execute("SELECT id,ts,by_id,segment,text,photo,btn_text,total,pos,sent,failed,blocked,status,finished_at "
                               "FROM broadcasts ORDER BY id DESC LIMIT 30").fetchall()]
    c.close()
    return {"rows": rows, "segments": B.seg_counts()}

async def a_bc_send(request, tg, name):
    form = await request.post()
    text = str(form.get("text") or "").strip(); seg = str(form.get("segment") or "all")
    bt = str(form.get("btn_text") or "").strip()[:64]; bu = str(form.get("btn_url") or "").strip()[:500]
    test = str(form.get("test") or "") == "1"
    ids_raw = str(form.get("ids") or "").strip()
    photo = form.get("photo"); path = None
    if bu and not re.match(r"^(https?://|tg://)", bu): raise ValueError("Ссылка кнопки должна начинаться с https://")
    if bool(bt) != bool(bu): raise ValueError("Для кнопки нужны и текст, и ссылка")
    if photo is not None and getattr(photo, "file", None):
        data = photo.file.read()
        if len(data) > 10 * 1024 * 1024: raise ValueError("Картинка больше 10 МБ")
        if data:
            os.makedirs(B.BC_DIR, exist_ok=True)
            ext = ".png" if data[:4] == b"\x89PNG" else ".jpg"
            path = os.path.join(B.BC_DIR, f"img_{int(time.time()*1000)}{ext}")
            open(path, "wb").write(data)
    elif form.get("photo_path") and str(form.get("photo_path")).startswith(B.BC_DIR) and os.path.exists(str(form.get("photo_path"))):
        path = str(form.get("photo_path"))
    if not text and not path: raise ValueError("Пустая рассылка")
    if path and len(text) > 1024: raise ValueError(f"С картинкой текст до 1024 символов (сейчас {len(text)})")
    if len(text) > 4096: raise ValueError("Текст до 4096 символов")
    if test:
        try: await B.bc_test(tg, text, path, bt, bu)
        except Exception as e: raise ValueError(f"Telegram не принял сообщение: {e}")
        return {"ok": True, "test": True, "photo_path": path}
    ids = None
    if seg == "ids":
        ids = sorted({int(x) for x in re.findall(r"\d{5,15}", ids_raw)})
        if not ids: raise ValueError("Список ID пуст")
    elif seg not in B.SEGMENTS: raise ValueError("Неизвестный сегмент")
    try: await B.bc_test(tg, text, path, bt, bu)      # проверка разметки + копия себе
    except Exception as e: raise ValueError(f"Telegram не принял сообщение: {e}")
    bid, n = B.bc_create(tg, seg, text, path, bt, bu, ids=ids)
    B.log_event(tg, "admin:broadcast", f"#{bid} {seg} {n}")
    return {"ok": True, "id": bid, "total": n}

async def a_bc_cancel(request, tg, name):
    bid = int(name.split("/")[1]); B._BC_CANCEL.add(bid)
    if bid not in B._BC_RUN:
        c = B.db(); c.execute("UPDATE broadcasts SET status='cancelled' WHERE id=? AND status='running'", (bid,)); c.commit(); c.close()
    return {"ok": True}

# ------------------------------------------------------------------ export
async def a_export(request, tg, name):
    kind = name.split("/")[1] if "/" in name else "payments"
    buf = io.StringIO(); w = csv.writer(buf, delimiter=";")
    c = B.db()
    if kind == "users":
        w.writerow(["tg_id", "username", "имя", "тариф", "до", "оплат", "сумма ₽", "создан", "канал", "реферер", "источник"])
        for r in c.execute("SELECT tg_id,username,first_name,plan,expires_at,COALESCE(pay_count,0),"
                           "(SELECT COALESCE(SUM(amount_rub),0) FROM payments p WHERE p.tg_id=users.tg_id),created_at,ch_sub,referrer,src FROM users").fetchall():
            w.writerow([r[0], r[1] or "", r[2] or "", r[3] or "", _d(r[4]), r[5], round(r[6]), _d(r[7]), r[8] or 0, r[9] or "", r[10] or ""])
    else:
        w.writerow(["дата", "tg_id", "username", "тариф", "способ", "сумма ₽", "валюта", "звёзды", "тип", "order_id"])
        for r in c.execute("SELECT p.ts,p.tg_id,u.username,p.pid,p.provider,p.amount_rub,p.currency,p.stars,p.kind,p.order_id FROM payments p "
                           "LEFT JOIN users u ON u.tg_id=p.tg_id ORDER BY p.ts DESC").fetchall():
            w.writerow([_d(r[0], True), r[1], r[2] or "", plan_title(r[3]), r[4], r[5], r[6], r[7] or "", r[8] or "", r[9] or ""])
    c.close()
    B.log_event(tg, "admin:export", kind)
    return web.Response(body=("\ufeff" + buf.getvalue()).encode("utf-8"), content_type="text/csv", charset="utf-8",
                        headers=dict(_HDR, **{"Content-Disposition": f'attachment; filename="zpn_{kind}_{datetime.now().strftime("%Y%m%d")}.csv"'}))

def _d(ts, t=False):
    if not ts: return ""
    return datetime.fromtimestamp(int(ts) + MSK, timezone.utc).strftime("%d.%m.%Y %H:%M" if t else "%d.%m.%Y")

ROUTES = {
    ("GET", "me"): a_me, ("GET", "system"): a_system, ("GET", "dash"): a_dash, ("GET", "funnel"): a_funnel,
    ("GET", "payments"): a_payments, ("GET", "users"): a_users, ("GET", "user"): a_user,
    ("POST", "user"): a_user_action, ("GET", "uvpn"): a_uvpn, ("GET", "refs"): a_refs, ("GET", "autopay"): a_autopay,
    ("GET", "promos"): a_promos, ("POST", "promos"): a_promo_save, ("GET", "settings"): a_settings,
    ("POST", "settings"): a_settings_save, ("POST", "yk_test"): a_yk_test, ("GET", "admins"): a_admins,
    ("POST", "admins"): a_admins_save, ("POST", "sessions_reset"): a_sessions_reset, ("GET", "bc"): a_bcs,
    ("POST", "bc"): a_bc_send, ("POST", "bc_cancel"): a_bc_cancel, ("GET", "export"): a_export,
}
