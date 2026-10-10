"""Carteira K: kcoin + cristais, com histórico (ledger) atômico. Moeda só virtual."""
from types import SimpleNamespace

from flask import Blueprint, flash, redirect, render_template, request, url_for

bp = Blueprint("wallet", __name__)
core = SimpleNamespace()

CRYSTAL_RATE = 1000          # 1 cristal = 1000 kcoin
PLATFORM_USER = "eozffprivacy"
PLATFORM_START = (400_000, 50_000)   # kcoin, cristais


def migrate():
    for sql in (
        """CREATE TABLE IF NOT EXISTS wallets (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            kcoin BIGINT NOT NULL DEFAULT 0 CHECK (kcoin >= 0),
            crystals BIGINT NOT NULL DEFAULT 0 CHECK (crystals >= 0)
        )""",
        """CREATE TABLE IF NOT EXISTS wallet_ledger (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            kcoin_delta BIGINT NOT NULL DEFAULT 0,
            crystal_delta BIGINT NOT NULL DEFAULT 0,
            note TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT (NOW() AT TIME ZONE 'utc')
        )""",
        "CREATE INDEX IF NOT EXISTS wallet_ledger_user_idx ON wallet_ledger (user_id, id DESC)",
    ):
        core.execute(sql)
    # Saldo inicial da plataforma (só uma vez).
    row = core.query_one("SELECT id FROM users WHERE username = %s", (PLATFORM_USER,))
    if row and not core.query_one(
        "SELECT 1 FROM wallet_ledger WHERE user_id = %s AND kind = 'genesis'", (row["id"],)
    ):
        ensure(row["id"], bonus=False)
        change(row["id"], "genesis", PLATFORM_START[0], PLATFORM_START[1], "Saldo inicial da plataforma")


WELCOME = (300, 1)   # kcoin, cristais — uma vez por conta (existente ou nova)


def ensure(user_id, bonus=True):
    created = core.execute(
        "INSERT INTO wallets (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING RETURNING user_id",
        (user_id,),
    )
    if created and bonus:
        change(user_id, "welcome", WELCOME[0], WELCOME[1], "Bônus de boas-vindas")


def get_wallet(user_id):
    ensure(user_id)
    return core.query_one("SELECT kcoin, crystals FROM wallets WHERE user_id = %s", (user_id,))


def apply(cur, user_id, kind, kcoin=0, crystals=0, note=None):
    """Variação dentro de uma transação aberta. False se o saldo ficaria negativo."""
    cur.execute("INSERT INTO wallets (user_id) VALUES (%s) ON CONFLICT (user_id) DO NOTHING", (user_id,))
    cur.execute(
        """UPDATE wallets SET kcoin = kcoin + %s, crystals = crystals + %s
           WHERE user_id = %s AND kcoin + %s >= 0 AND crystals + %s >= 0
           RETURNING user_id""",
        (kcoin, crystals, user_id, kcoin, crystals),
    )
    if not cur.fetchone():
        return False
    cur.execute(
        """INSERT INTO wallet_ledger (user_id, kind, kcoin_delta, crystal_delta, note)
           VALUES (%s, %s, %s, %s, %s)""",
        (user_id, kind, kcoin, crystals, note),
    )
    return True


def change(user_id, kind, kcoin=0, crystals=0, note=None):
    """Aplica variação atômica. Retorna True, ou False se o saldo ficaria negativo."""
    with core.get_db() as db:
        with db.cursor() as cur:
            return apply(cur, user_id, kind, kcoin, crystals, note)


KIND_LABEL = {
    "genesis": "Saldo inicial", "welcome": "Bônus de boas-vindas", "bet_stake": "Aposta", "bet_free": "Rodada grátis", "convert_in": "Troca por cristais", "convert_out": "Troca por kcoin",
    "task": "Tarefa", "bet_win": "Ganho no jogo", "bet_loss": "Aposta perdida",
    "house": "Receita da plataforma", "mod": "Repasse moderação",
}


@bp.route("/carteira", methods=["GET", "POST"])
def carteira():
    user = core.get_current_user()
    if not user:
        return redirect(url_for("login"))

    if request.method == "POST":
        action = request.form.get("action")
        try:
            qty = max(1, min(int(request.form.get("qty", "1")), 1_000_000))
        except ValueError:
            qty = 1
        ensure(user["id"])
        if action == "buy":
            ok = change(user["id"], "convert_in", -qty * CRYSTAL_RATE, qty, f"{qty} cristal(is)")
        elif action == "sell":
            ok = change(user["id"], "convert_out", qty * CRYSTAL_RATE, -qty, f"{qty} cristal(is)")
        else:
            ok = False
        flash("Troca feita." if ok else "Saldo insuficiente.")
        return redirect(url_for("wallet.carteira"))

    w = get_wallet(user["id"])
    rows = core.query_all(
        "SELECT * FROM wallet_ledger WHERE user_id = %s ORDER BY id DESC LIMIT 30", (user["id"],)
    )
    return render_template("wallet.html", w=w, rows=rows, labels=KIND_LABEL, rate=CRYSTAL_RATE)


def install(app, namespace):
    for name in ("get_db", "query_one", "query_all", "execute", "get_current_user"):
        setattr(core, name, namespace[name])
    app.register_blueprint(bp)

    @app.context_processor
    def _wallet_ctx():
        u = core.get_current_user()
        if not u:
            return {}
        try:
            return {"wallet_me": get_wallet(u["id"])}
        except Exception:
            return {}
