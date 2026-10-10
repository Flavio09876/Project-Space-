"""Jogo do gráfico (crash) com kcoin virtual. Resultado sorteado e resolvido só no servidor."""
import math
import secrets
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

import wallet

bp = Blueprint("casino", __name__)
core = SimpleNamespace()

MIN_BET, MAX_BET = 10, 1000
FREE_PER_DAY = 4
FREE_MAX_BET = 50          # rodada grátis: a casa paga o prêmio, então o valor é limitado
RATE = 0.12                # multiplicador = e^(RATE * segundos)
MAX_MULT = 20.0
EDGE = 0.96
MIN_AGE = 18
PLATFORM, MODERATOR = "eozffprivacy", "staffmoderator"
HOUSE_SHARE = 90           # % da aposta perdida para a plataforma; resto vai ao moderador


def migrate():
    core.execute("""CREATE TABLE IF NOT EXISTS casino_rounds (
        id SERIAL PRIMARY KEY,
        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
        stake BIGINT NOT NULL,
        free BOOLEAN NOT NULL DEFAULT FALSE,
        crash_point DOUBLE PRECISION NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        cashed_mult DOUBLE PRECISION,
        payout BIGINT NOT NULL DEFAULT 0,
        started_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
    )""")
    core.execute("CREATE INDEX IF NOT EXISTS casino_rounds_user_idx ON casino_rounds (user_id, id DESC)")


def _now():
    return datetime.utcnow()


def _mult_at(seconds):
    return math.exp(RATE * max(seconds, 0))


def _draw_crash():
    u = secrets.randbelow(10**9) / 1e9
    return min(MAX_MULT, max(1.0, math.floor(100 * EDGE / (1 - u)) / 100))


def _uid(username):
    row = core.query_one("SELECT id FROM users WHERE username = %s", (username,))
    return row["id"] if row else None


def _is_adult(user):
    try:
        born = date.fromisoformat(str(user["birth_date"])[:10])
    except Exception:
        return False
    t = date.today()
    return t.year - born.year - ((t.month, t.day) < (born.month, born.day)) >= MIN_AGE


def _free_used(user_id):
    brt = _now() - timedelta(hours=3)
    start = datetime(brt.year, brt.month, brt.day) + timedelta(hours=3)
    row = core.query_one(
        "SELECT COUNT(*) AS n FROM casino_rounds WHERE user_id=%s AND free AND started_at>=%s",
        (user_id, start),
    )
    return row["n"]


def _settle(rnd, won, mult=None):
    """Fecha a rodada uma única vez e move o dinheiro na mesma transação."""
    platform, mod = _uid(PLATFORM), _uid(MODERATOR)
    with core.get_db() as db:
        with db.cursor() as cur:
            new = "cashed" if won else "lost"
            payout = int(math.floor(rnd["stake"] * mult)) if won else 0
            cur.execute(
                """UPDATE casino_rounds SET status=%s, cashed_mult=%s, payout=%s
                   WHERE id=%s AND status='open' RETURNING id""",
                (new, mult, payout, rnd["id"]),
            )
            if not cur.fetchone():
                return None  # já resolvida
            uid, stake, free = rnd["user_id"], rnd["stake"], rnd["free"]
            if won:
                if not wallet.apply(cur, uid, "bet_win", payout, 0, f"x{mult:.2f}"):
                    raise RuntimeError("falha ao pagar")
                if platform and platform != uid:
                    diff = (0 if free else stake) - payout   # custo líquido da casa
                    if not wallet.apply(cur, platform, "house", diff, 0, f"pagou rodada {rnd['id']}"):
                        # plataforma sem saldo: paga só o que o stake cobriu
                        pass
            elif not free and platform:
                to_mod = stake * (100 - HOUSE_SHARE) // 100 if mod else 0
                wallet.apply(cur, platform, "house", stake - to_mod, 0, f"perda rodada {rnd['id']}")
                if to_mod:
                    wallet.apply(cur, mod, "mod", to_mod, 0, f"perda rodada {rnd['id']}")
            return payout


def _get_open(user_id):
    return core.query_one(
        "SELECT * FROM casino_rounds WHERE user_id=%s AND status='open' ORDER BY id DESC LIMIT 1",
        (user_id,),
    )


def _resolve_if_crashed(rnd):
    elapsed = (_now() - rnd["started_at"]).total_seconds()
    t_crash = math.log(rnd["crash_point"]) / RATE
    if elapsed >= t_crash:
        _settle(rnd, False)
        return True, elapsed
    return False, elapsed


@bp.route("/casino")
def page():
    user = core.get_current_user()
    if not user:
        return redirect(url_for("login"))
    w = wallet.get_wallet(user["id"])
    rows = core.query_all(
        "SELECT stake, free, status, cashed_mult, crash_point, payout, started_at FROM casino_rounds "
        "WHERE user_id=%s AND status<>'open' ORDER BY id DESC LIMIT 10", (user["id"],))
    return render_template(
        "casino.html", w=w, rows=rows, adult=_is_adult(user),
        free_left=max(0, FREE_PER_DAY - _free_used(user["id"])),
        cfg=dict(min=MIN_BET, max=MAX_BET, free_max=FREE_MAX_BET, rate=RATE, free_total=FREE_PER_DAY),
    )


@bp.route("/casino/start", methods=["POST"])
def start():
    user = core.get_current_user()
    if not user:
        return jsonify(error="Faça login."), 401
    if not _is_adult(user):
        return jsonify(error="O jogo é apenas para maiores de 18 anos."), 403
    wallet.get_wallet(user["id"])

    open_round = _get_open(user["id"])
    if open_round:
        crashed, _ = _resolve_if_crashed(open_round)
        if not crashed:
            return jsonify(error="Você já tem uma rodada em andamento."), 409

    data = request.get_json(silent=True) or request.form
    try:
        stake = int(data.get("stake"))
    except (TypeError, ValueError):
        return jsonify(error="Valor inválido."), 400
    free = str(data.get("free", "")).lower() in ("1", "true", "on")

    if free:
        if _free_used(user["id"]) >= FREE_PER_DAY:
            return jsonify(error="Suas tentativas grátis de hoje acabaram."), 403
        if not MIN_BET <= stake <= FREE_MAX_BET:
            return jsonify(error=f"Na rodada grátis a aposta vai de {MIN_BET} a {FREE_MAX_BET}."), 400
    elif not MIN_BET <= stake <= MAX_BET:
        return jsonify(error=f"A aposta vai de {MIN_BET} a {MAX_BET} kcoin."), 400

    crash = _draw_crash()
    with core.get_db() as db:
        with db.cursor() as cur:
            if not free and not wallet.apply(cur, user["id"], "bet_stake", -stake, 0, "Aposta no gráfico"):
                return jsonify(error="Saldo insuficiente."), 400
            cur.execute(
                """INSERT INTO casino_rounds (user_id, stake, free, crash_point, started_at)
                   VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                (user["id"], stake, free, crash, _now()),
            )
            rid = cur.fetchone()["id"]
    return jsonify(ok=True, id=rid, rate=RATE)


@bp.route("/casino/tick")
def tick():
    user = core.get_current_user()
    if not user:
        return jsonify(error="Faça login."), 401
    rnd = _get_open(user["id"])
    if not rnd:
        last = core.query_one(
            "SELECT * FROM casino_rounds WHERE user_id=%s ORDER BY id DESC LIMIT 1", (user["id"],))
        if last and last["status"] == "lost":
            return jsonify(status="crashed", crash=last["crash_point"])
        return jsonify(status="idle")
    crashed, elapsed = _resolve_if_crashed(rnd)
    if crashed:
        return jsonify(status="crashed", crash=rnd["crash_point"])
    return jsonify(status="running", mult=round(_mult_at(elapsed), 2), elapsed=elapsed)


@bp.route("/casino/cashout", methods=["POST"])
def cashout():
    user = core.get_current_user()
    if not user:
        return jsonify(error="Faça login."), 401
    rnd = _get_open(user["id"])
    if not rnd:
        return jsonify(error="Nenhuma rodada em andamento."), 409
    crashed, elapsed = _resolve_if_crashed(rnd)
    if crashed:
        return jsonify(status="crashed", crash=rnd["crash_point"])
    mult = math.floor(_mult_at(elapsed) * 100) / 100
    payout = _settle(rnd, True, mult)
    w = wallet.get_wallet(user["id"])
    return jsonify(status="cashed", mult=mult, payout=payout, kcoin=w["kcoin"])


def install(app, namespace):
    for name in ("get_db", "query_one", "query_all", "execute", "get_current_user"):
        setattr(core, name, namespace[name])
    app.register_blueprint(bp)
