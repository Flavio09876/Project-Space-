"""Loja de cosméticos: molduras de avatar e nicks animados."""
import time
from types import SimpleNamespace

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

import wallet

bp = Blueprint("shop", __name__)
core = SimpleNamespace()

PLATFORM = "eozffprivacy"
# (chave, nome, descrição, preço em kcoin)
FRAMES = [
    ("1", "Brasa", "Anel de fogo girando com brilho", 900),
    ("2", "Neon", "Anel vermelho que pulsa", 700),
    ("3", "Rubi", "Joia escura com reflexo que passa", 1500),
    ("4", "Glitch", "Ruído RGB tremendo", 1200),
    ("5", "Aura", "Anel em degradê com estrela orbitando", 2500),
]
NICKS = [
    ("neon", "Neon", "Brilho vermelho pulsando", 600),
    ("fluxo", "Fluxo", "Degradê vermelho e dourado correndo", 800),
    ("glitch", "Glitch", "Letras com ruído RGB", 900),
    ("brasa", "Brasa", "Chama tremulando", 900),
    ("ouro", "Ouro", "Reflexo dourado que passa", 1500),
    ("prisma", "Prisma", "Cores mudando suavemente", 2000),
]
CATALOG = {"frame": {k: (n, d, p) for k, n, d, p in FRAMES}, "nick": {k: (n, d, p) for k, n, d, p in NICKS}}
COLUMN = {"frame": "frame", "nick": "name_fx"}

_cache = {"t": 0, "v": {}}


def migrate():
    core.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS frame TEXT")
    core.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS name_fx TEXT")
    core.execute("""CREATE TABLE IF NOT EXISTS cosmetics_owned (
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, item TEXT NOT NULL,
        acquired_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc'),
        PRIMARY KEY (user_id, kind, item)
    )""")


def _equipped():
    if time.time() - _cache["t"] > 60:
        rows = core.query_all("SELECT username, frame, name_fx FROM users WHERE frame IS NOT NULL OR name_fx IS NOT NULL")
        _cache["v"] = {r["username"]: r for r in rows}
        _cache["t"] = time.time()
    return _cache["v"]


def fx_for(username):
    r = _equipped().get(username)
    return f"nfx nfx-{r['name_fx']}" if r and r["name_fx"] else ""


def frame_for(username):
    r = _equipped().get(username)
    return f"k-frame k-frame--{r['frame']}" if r and r["frame"] else ""


def _is_admin(user):
    import extras
    return extras.is_admin(user)


def _owned(user_id):
    rows = core.query_all("SELECT kind, item FROM cosmetics_owned WHERE user_id=%s", (user_id,))
    return {(r["kind"], r["item"]) for r in rows}


@bp.route("/loja")
def loja():
    user = core.get_current_user()
    if not user:
        return redirect(url_for("login"))
    cat = request.args.get("cat", "molduras")
    cat = cat if cat in ("molduras", "nicks") else "molduras"
    admin = _is_admin(user)
    return render_template(
        "shop.html", cat=cat, items=FRAMES if cat == "molduras" else NICKS,
        kind="frame" if cat == "molduras" else "nick",
        owned=_owned(user["id"]), admin=admin, wallet=wallet.get_wallet(user["id"]),
        equipped={"frame": user.get("frame"), "nick": user.get("name_fx")},
    )


@bp.route("/loja/comprar", methods=["POST"])
def comprar():
    user = core.get_current_user()
    if not user:
        return redirect(url_for("login"))
    kind, item = request.form.get("kind", ""), request.form.get("item", "")
    back = redirect(url_for("shop.loja", cat="molduras" if kind == "frame" else "nicks"))
    if item not in CATALOG.get(kind, {}):
        abort(400)
    price = CATALOG[kind][item][2]
    pid = core.query_one("SELECT id FROM users WHERE username=%s", (PLATFORM,))
    with core.get_db() as db:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO cosmetics_owned (user_id, kind, item) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING RETURNING item",
                (user["id"], kind, item))
            if not cur.fetchone():
                flash("Você já tem esse item.")
                return back
            if not wallet.apply(cur, user["id"], "shop", -price, 0, f"{kind}:{item}"):
                db.rollback()
                flash("Saldo insuficiente.")
                return back
            if pid and pid["id"] != user["id"]:
                wallet.apply(cur, pid["id"], "house", price, 0, f"loja {kind}:{item}")
    flash("Comprado! Agora é só equipar.")
    return back


@bp.route("/loja/equipar", methods=["POST"])
def equipar():
    user = core.get_current_user()
    if not user:
        return redirect(url_for("login"))
    kind, item = request.form.get("kind", ""), request.form.get("item", "")
    back = redirect(url_for("shop.loja", cat="molduras" if kind == "frame" else "nicks"))
    if kind not in COLUMN:
        abort(400)
    if item:
        if item not in CATALOG[kind]:
            abort(400)
        if not _is_admin(user) and (kind, item) not in _owned(user["id"]):
            flash("Compre o item antes de equipar.")
            return back
    core.execute(f"UPDATE users SET {COLUMN[kind]} = %s WHERE id = %s", (item or None, user["id"]))
    _cache["t"] = 0
    flash("Atualizado." if item else "Removido.")
    return back


def install(app, namespace):
    for name in ("get_db", "query_one", "query_all", "execute", "get_current_user"):
        setattr(core, name, namespace[name])
    app.register_blueprint(bp)
    app.jinja_env.globals["fx_for"] = fx_for
    app.jinja_env.globals["frame_for"] = frame_for
