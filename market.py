"""Bolsa K: empresas fictícias criadas por jogadores, investimentos e apostas (moeda virtual)."""
import math
import random
import secrets
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from flask import Blueprint, abort, flash, jsonify, redirect, render_template, request, url_for

import wallet

bp = Blueprint("market", __name__)
core = SimpleNamespace()

N = 48                      # pontos do gráfico
SIG, MU, P_BANKRUPT = 0.07, -0.003, 0.08
MIN_STAKE = 150
CRYSTAL_MIN_STAKE = 1000    # cristal só em valores a partir daqui
MAX_CRYSTALS, CRYSTAL_BONUS = 5, 0.02   # +2% sobre o ganho por cristal
FOUNDER_FEE, FOUNDER_CRYSTAL = 500, 1
CUT_KCOIN, CUT_CRYSTAL = 0.02, 0.04     # % do volume que o fundador recebe
ENTRY_WINDOW = 0.20         # entradas só no 1º quinto do prazo
DEBT_EXTRA = 0.5            # falência: além de tudo, deve +50% do investido
DURATIONS = [("1h", 3600), ("6h", 6 * 3600), ("24h", 86400), ("3 dias", 3 * 86400), ("7 dias", 7 * 86400)]
BET_MULT = {"up": 2.3, "down": 1.55, "bankrupt": 11.0}
PEAK_MULT = [2.9, 6.9, 8.2, 8.5, 7.8, 4.1]    # por faixa de tempo (6 faixas)
PLATFORM, MODERATOR = "eozffprivacy", "staffmoderator"
KIND_LABEL = {"invest": "Investimento", "up": "Aposta: alta", "down": "Aposta: queda",
              "bankrupt": "Aposta: falência", "peak": "Aposta: pico"}


def migrate():
    core.execute("""CREATE TABLE IF NOT EXISTS markets (
        id SERIAL PRIMARY KEY,
        founder_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        name TEXT NOT NULL, logo_url TEXT, seed BIGINT NOT NULL,
        duration_s INTEGER NOT NULL,
        started_at TIMESTAMP NOT NULL, ends_at TIMESTAMP NOT NULL, entry_until TIMESTAMP NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        final_r DOUBLE PRECISION, bankrupt BOOLEAN, peak_slot INTEGER,
        volume BIGINT NOT NULL DEFAULT 0, paid_crystal BOOLEAN NOT NULL DEFAULT FALSE
    )""")
    core.execute("""CREATE TABLE IF NOT EXISTS market_positions (
        id SERIAL PRIMARY KEY,
        market_id INTEGER NOT NULL REFERENCES markets(id) ON DELETE CASCADE,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        kind TEXT NOT NULL, slot INTEGER, amount BIGINT NOT NULL, crystals INTEGER NOT NULL DEFAULT 0,
        payout BIGINT, result TEXT,
        created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )""")
    core.execute("CREATE INDEX IF NOT EXISTS markets_status_idx ON markets (status, ends_at)")
    core.execute("CREATE INDEX IF NOT EXISTS mpos_market_idx ON market_positions (market_id)")
    core.execute("CREATE INDEX IF NOT EXISTS mpos_user_idx ON market_positions (user_id, id DESC)")


# ---------- simulação (determinística pela semente, que nunca sai do servidor) ----------
def make_path(seed):
    r = random.Random(seed)
    bank = r.randint(int(N * .3), N - 2) if r.random() < P_BANKRUPT else None
    p = [100.0]
    for i in range(1, N + 1):
        if bank is not None and i >= bank:
            p.append(p[-1] * 0.3 if i == bank else p[-1] * 0.2 if i == bank + 1 else 0.0)
        else:
            p.append(p[-1] * math.exp(MU + SIG * r.gauss(0, 1)))
    peak = max(range(len(p)), key=lambda i: p[i])
    return p, bank is not None, min(5, int(peak / ((N + 1) / 6)))


def _now():
    return datetime.utcnow()


def _uid(username):
    row = core.query_one("SELECT id FROM users WHERE username=%s", (username,))
    return row["id"] if row else None


def _split_loss(loser, amount, platform, mod):
    """80% plataforma / 20% moderador. Se o perdedor é um deles, 80% vai ao outro e 20% some."""
    if amount <= 0:
        return []
    big, small = int(amount * 80 // 100), amount - int(amount * 80 // 100)
    if loser == platform:
        return [(mod, big)]
    if loser == mod:
        return [(platform, big)]
    return [(platform, big), (mod, small)]


def close_due():
    due = core.query_all("SELECT * FROM markets WHERE status='open' AND ends_at <= %s ORDER BY id LIMIT 5", (_now(),))
    for m in due:
        try:
            _settle_market(m)
        except Exception:
            pass


def _settle_market(m):
    path, bankrupt, peak_slot = make_path(m["seed"])
    final_r = path[-1] / 100
    platform, mod = _uid(PLATFORM), _uid(MODERATOR)
    with core.get_db() as db:
        with db.cursor() as cur:
            cur.execute(
                """UPDATE markets SET status='closed', final_r=%s, bankrupt=%s, peak_slot=%s
                   WHERE id=%s AND status='open' RETURNING id""",
                (final_r, bankrupt, peak_slot, m["id"]))
            if not cur.fetchone():
                return
            cur.execute("SELECT * FROM market_positions WHERE market_id=%s", (m["id"],))
            for pos in cur.fetchall():
                uid, amt, kind = pos["user_id"], pos["amount"], pos["kind"]
                payout, loss = 0, 0
                if kind == "invest":
                    bonus = 1 + CRYSTAL_BONUS * pos["crystals"] if final_r > 1 else 1
                    payout = int(amt * final_r * bonus)
                    loss = max(0, amt - payout)
                    if bankrupt:   # falência: ainda fica devendo
                        extra = int(amt * DEBT_EXTRA)
                        wallet.apply(cur, uid, "market_loss", -extra, 0, f"falência {m['name']}", allow_negative=True)
                        loss += extra
                    result = "win" if payout > amt else "lose"
                else:
                    won = (kind == "up" and final_r > 1) or (kind == "down" and final_r < 1) \
                        or (kind == "bankrupt" and bankrupt) or (kind == "peak" and pos["slot"] == peak_slot)
                    mult = PEAK_MULT[pos["slot"]] if kind == "peak" else BET_MULT[kind]
                    payout = int(amt * mult) if won else 0
                    loss = 0 if won else amt
                    result = "win" if won else "lose"
                if payout:
                    wallet.apply(cur, uid, "market_win", payout, 0, f"{m['name']}")
                # a casa cobre o prêmio (o valor entrou na plataforma como caução)
                if platform and payout > amt:
                    wallet.apply(cur, platform, "house", amt - payout, 0, f"pagou {m['name']}", allow_negative=True)
                for to, share in _split_loss(uid, loss, platform, mod):
                    if to:
                        wallet.apply(cur, to, "house" if to == platform else "mod", share, 0, f"perda {m['name']}")
                cur.execute("UPDATE market_positions SET payout=%s, result=%s WHERE id=%s", (payout, result, pos["id"]))
            # fundador recebe parte do volume
            cut = int(m["volume"] * (CUT_CRYSTAL if m["paid_crystal"] else CUT_KCOIN))
            if cut:
                wallet.apply(cur, m["founder_id"], "founder", cut, 0, f"comissão {m['name']}")
                if platform:
                    wallet.apply(cur, platform, "house", -cut, 0, f"comissão {m['name']}", allow_negative=True)


# ---------- páginas ----------
def _me():
    u = core.get_current_user()
    if not u:
        abort(401)
    return u


@bp.app_errorhandler(401)
def _login_needed(e):
    return redirect(url_for("login"))


def _price_now(m):
    path, _, _ = make_path(m["seed"])
    step = m["duration_s"] / N
    idx = min(N, int((_now() - m["started_at"]).total_seconds() / step))
    return path[idx]


def _fmt_left(m):
    s = int((m["ends_at"] - _now()).total_seconds())
    if s <= 0:
        return "encerrando"
    d, r = divmod(s, 86400)
    h, r = divmod(r, 3600)
    return (f"{d}d " if d else "") + (f"{h}h " if h else "") + f"{r // 60}min"


@bp.route("/bolsa")
def lista():
    user = _me()
    close_due()
    w = wallet.get_wallet(user["id"])
    open_m = core.query_all(
        """SELECT m.*, u.username, u.name FROM markets m JOIN users u ON u.id=m.founder_id
           WHERE m.status='open' ORDER BY m.id DESC LIMIT 30""")
    for m in open_m:
        m["price"] = round(_price_now(m), 2)
        m["left"] = _fmt_left(m)
        m["entries_open"] = _now() < m["entry_until"]
    closed = core.query_all(
        """SELECT m.*, u.username FROM markets m JOIN users u ON u.id=m.founder_id
           WHERE m.status='closed' ORDER BY m.id DESC LIMIT 8""")
    mine = core.query_all(
        """SELECT p.*, m.name, m.status FROM market_positions p JOIN markets m ON m.id=p.market_id
           WHERE p.user_id=%s ORDER BY p.id DESC LIMIT 8""", (user["id"],))
    return render_template("market_list.html", w=w, open_m=open_m, closed=closed, mine=mine,
                           durations=DURATIONS, fee=FOUNDER_FEE, labels=KIND_LABEL)


@bp.route("/bolsa/criar", methods=["POST"])
def criar():
    user = _me()
    name = request.form.get("name", "").strip()[:30]
    pay = request.form.get("pay", "kcoin")
    try:
        dur = int(request.form.get("duration", "86400"))
    except ValueError:
        dur = 0
    if len(name) < 3 or dur not in {d[1] for d in DURATIONS}:
        flash("Dê um nome (mín. 3 letras) e escolha um prazo.")
        return redirect(url_for("market.lista"))
    if wallet.get_wallet(user["id"])["kcoin"] < 0:
        flash("Você está com dívida.")
        return redirect(url_for("market.lista"))
    logo = core.save_upload(request.files.get("logo"), "misc")
    if not logo:
        flash("Envie uma imagem (logo) válida para a empresa.")
        return redirect(url_for("market.lista"))
    now = _now()
    platform = _uid(PLATFORM)
    with core.get_db() as db:
        with db.cursor() as cur:
            ok = wallet.apply(cur, user["id"], "founder", -FOUNDER_FEE, 0, f"abrir {name}") if pay != "crystal" \
                else wallet.apply(cur, user["id"], "founder", 0, -FOUNDER_CRYSTAL, f"abrir {name}")
            if not ok:
                flash("Saldo insuficiente.")
                return redirect(url_for("market.lista"))
            if platform and platform != user["id"]:
                wallet.apply(cur, platform, "house", FOUNDER_FEE if pay != "crystal" else 0,
                             FOUNDER_CRYSTAL if pay == "crystal" else 0, f"taxa {name}")
            cur.execute(
                """INSERT INTO markets (founder_id,name,logo_url,seed,duration_s,started_at,ends_at,entry_until,paid_crystal)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (user["id"], name, logo, secrets.randbits(62), dur, now, now + timedelta(seconds=dur),
                 now + timedelta(seconds=dur * ENTRY_WINDOW), pay == "crystal"))
            mid = cur.fetchone()["id"]
    return redirect(url_for("market.detalhe", market_id=mid))


@bp.route("/bolsa/<int:market_id>")
def detalhe(market_id):
    user = _me()
    close_due()
    m = core.query_one("SELECT m.*, u.username, u.name AS founder_name FROM markets m JOIN users u ON u.id=m.founder_id WHERE m.id=%s", (market_id,))
    if not m:
        abort(404)
    mine = core.query_all("SELECT * FROM market_positions WHERE market_id=%s AND user_id=%s ORDER BY id DESC", (market_id, user["id"]))
    vol = core.query_one("SELECT COUNT(*) AS n FROM market_positions WHERE market_id=%s", (market_id,))["n"]
    return render_template(
        "market_detail.html", m=m, mine=mine, w=wallet.get_wallet(user["id"]), labels=KIND_LABEL,
        entries_open=m["status"] == "open" and _now() < m["entry_until"], left=_fmt_left(m), n_pos=vol,
        is_founder=m["founder_id"] == user["id"], bet_mult=BET_MULT, peak_mult=PEAK_MULT,
        min_stake=MIN_STAKE, crystal_min=CRYSTAL_MIN_STAKE, max_crystals=MAX_CRYSTALS,
        n_slots=6, slot_len=m["duration_s"] / 6)


@bp.route("/bolsa/<int:market_id>/serie")
def serie(market_id):
    _me()
    m = core.query_one("SELECT * FROM markets WHERE id=%s", (market_id,))
    if not m:
        abort(404)
    path, bankrupt, peak = make_path(m["seed"])
    if m["status"] == "closed":
        return jsonify(points=[round(x, 2) for x in path], closed=True, bankrupt=bankrupt, peak_slot=peak)
    step = m["duration_s"] / N
    idx = min(N, int((_now() - m["started_at"]).total_seconds() / step))
    return jsonify(points=[round(x, 2) for x in path[: idx + 1]], total=N, closed=False)


@bp.route("/bolsa/<int:market_id>/entrar", methods=["POST"])
def entrar(market_id):
    user = _me()
    close_due()
    m = core.query_one("SELECT * FROM markets WHERE id=%s", (market_id,))
    back = redirect(url_for("market.detalhe", market_id=market_id))
    if not m or m["status"] != "open" or _now() >= m["entry_until"]:
        flash("As entradas desta empresa estão encerradas.")
        return back
    if m["founder_id"] == user["id"]:
        flash("O fundador não pode investir nem apostar na própria empresa.")
        return back
    kind = request.form.get("kind", "")
    if kind not in KIND_LABEL:
        flash("Escolha um tipo.")
        return back
    try:
        amount = int(request.form.get("amount", "0"))
        crystals = int(request.form.get("crystals", "0") or 0)
        slot = int(request.form.get("slot", "0") or 0)
    except ValueError:
        flash("Valor inválido.")
        return back
    if amount < MIN_STAKE:
        flash(f"O mínimo é {MIN_STAKE} kcoin.")
        return back
    if kind != "invest":
        crystals = 0
    if crystals and (amount < CRYSTAL_MIN_STAKE or not 0 < crystals <= MAX_CRYSTALS):
        flash(f"Cristais só valem em valores a partir de {CRYSTAL_MIN_STAKE} kcoin (máx. {MAX_CRYSTALS}).")
        return back
    if kind == "peak" and not 0 <= slot < 6:
        flash("Escolha a faixa do pico.")
        return back
    if wallet.get_wallet(user["id"])["kcoin"] < 0:
        flash("Você está com dívida. Pague com missões e presente diário.")
        return back
    platform = _uid(PLATFORM)
    with core.get_db() as db:
        with db.cursor() as cur:
            if not wallet.apply(cur, user["id"], "market", -amount, -crystals, f"{KIND_LABEL[kind]} · {m['name']}"):
                flash("Saldo insuficiente.")
                return back
            cur.execute(
                "INSERT INTO market_positions (market_id,user_id,kind,slot,amount,crystals) VALUES (%s,%s,%s,%s,%s,%s)",
                (market_id, user["id"], kind, slot if kind == "peak" else None, amount, crystals))
            cur.execute("UPDATE markets SET volume = volume + %s WHERE id=%s", (amount if kind == "invest" else 0, market_id))
            if crystals and platform and platform != user["id"]:
                wallet.apply(cur, platform, "house", 0, crystals, "cristais de investimento")
    flash("Entrada registrada!")
    return back


def install(app, namespace):
    for name in ("get_db", "query_one", "query_all", "execute", "get_current_user", "save_upload"):
        setattr(core, name, namespace[name])
    app.register_blueprint(bp)
