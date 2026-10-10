"""Conteúdo extra: hashtags/menções, busca, tendências e tarefas de kcoin."""
import re
from collections import Counter
from datetime import datetime, timedelta
from types import SimpleNamespace

from flask import Blueprint, flash, redirect, render_template, request, url_for
from markupsafe import Markup, escape

import wallet

bp = Blueprint("content", __name__)
core = SimpleNamespace()

TAG_RE = re.compile(r"(?<![\w&;])([#@])([A-Za-z0-9_À-ÿ]{2,30})")


def rich(text):
    """Escapa o texto e transforma #tag e @usuario em links."""
    safe = str(escape(text or ""))

    def sub(m):
        sign, word = m.group(1), m.group(2)
        if sign == "#":
            href = url_for("content.buscar", q="#" + word)
        else:
            href = url_for("profile", username=word.lower())
        return f'<a class="k-tag" href="{href}">{sign}{word}</a>'

    return Markup(TAG_RE.sub(sub, safe))


def _brt_today():
    return (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d")


def _n(sql, uid):
    row = core.query_one(sql, (uid,))
    return int(row["n"]) if row else 0


# (chave, título, descrição, recompensa, contador(user), meta)
TASKS = [
    ("profile", "Completar o perfil", "Foto, bio e capa.", 100,
     lambda u: sum(bool(x) for x in (u.get("profile_photo"), (u.get("bio") or "").strip(), u.get("profile_header_value"))), 3),
    ("first_post", "Primeira publicação", "Publique algo no K.", 50,
     lambda u: _n("SELECT COUNT(*) AS n FROM posts WHERE author_id=%s", u["id"]), 1),
    ("follow3", "Seguir 3 pessoas", "Encontre gente em Descobrir.", 50,
     lambda u: _n("SELECT COUNT(*) AS n FROM follows WHERE follower_id=%s", u["id"]), 3),
    ("first_comment", "Primeiro comentário", "Comente em uma publicação.", 30,
     lambda u: _n("SELECT COUNT(*) AS n FROM comments WHERE user_id=%s", u["id"]), 1),
    ("first_message", "Primeira mensagem", "Converse com alguém no chat.", 30,
     lambda u: _n("SELECT COUNT(*) AS n FROM messages WHERE sender_id=%s", u["id"]), 1),
    ("posts5", "Criador", "Publique 5 posts.", 100,
     lambda u: _n("SELECT COUNT(*) AS n FROM posts WHERE author_id=%s", u["id"]), 5),
    ("likes10", "Popular", "Receba 10 curtidas.", 100,
     lambda u: _n("SELECT COUNT(*) AS n FROM post_likes l JOIN posts p ON p.id=l.post_id WHERE p.author_id=%s", u["id"]), 10),
    ("followers10", "Influente", "Tenha 10 seguidores.", 150,
     lambda u: _n("SELECT COUNT(*) AS n FROM follows WHERE following_id=%s", u["id"]), 10),
    ("play3", "Jogador", "Jogue 3 rodadas no Gráfico K.", 40,
     lambda u: _n("SELECT COUNT(*) AS n FROM casino_rounds WHERE user_id=%s", u["id"]), 3),
]
DAILY = 20


def migrate():
    core.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS name_fx TEXT")
    core.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS wallet_task_once ON wallet_ledger (user_id, note) WHERE kind = 'task'"
    )
    for sql in (
        "CREATE INDEX IF NOT EXISTS posts_created_idx ON posts (created_at DESC)",
        "CREATE INDEX IF NOT EXISTS posts_author_idx ON posts (author_id, id DESC)",
        "CREATE INDEX IF NOT EXISTS notif_user_idx ON notifications (user_id, is_read)",
        "CREATE INDEX IF NOT EXISTS msgs_conv_idx ON messages (conversation_id, id DESC)",
        "CREATE INDEX IF NOT EXISTS msgs_sender_idx ON messages (sender_id)",
        "CREATE INDEX IF NOT EXISTS likes_post_idx ON post_likes (post_id)",
        "CREATE INDEX IF NOT EXISTS follows_following_idx ON follows (following_id)",
        "CREATE INDEX IF NOT EXISTS members_user_idx ON conversation_members (user_id)",
    ):
        core.execute(sql)


def _claimed(uid):
    rows = core.query_all("SELECT note FROM wallet_ledger WHERE user_id=%s AND kind='task'", (uid,))
    return {r["note"] for r in rows}


def _claim(uid, key, amount):
    import psycopg
    try:
        with core.get_db() as db:
            with db.cursor() as cur:
                return wallet.apply(cur, uid, "task", amount, 0, key)
    except psycopg.errors.UniqueViolation:
        return False


@bp.route("/tarefas", methods=["GET", "POST"])
def tarefas():
    user = core.get_current_user()
    if not user:
        return redirect(url_for("login"))
    wallet.get_wallet(user["id"])
    done = _claimed(user["id"])
    today_key = f"daily:{_brt_today()}"

    if request.method == "POST":
        key = request.form.get("task", "")
        if key == "daily":
            ok = _claim(user["id"], today_key, DAILY)
            flash(f"+{DAILY} kcoin! Volte amanhã." if ok else "Você já pegou a recompensa de hoje.")
        else:
            task = next((t for t in TASKS if t[0] == key), None)
            if not task:
                flash("Tarefa inválida.")
            elif key in done:
                flash("Você já recebeu essa recompensa.")
            elif task[4](user) < task[5]:
                flash("Ainda não cumpriu essa tarefa.")
            else:
                ok = _claim(user["id"], key, task[3])
                flash(f"+{task[3]} kcoin!" if ok else "Você já recebeu essa recompensa.")
        return redirect(url_for("content.tarefas"))

    items = []
    for key, title, desc, reward, count, goal in TASKS:
        is_done = key in done
        cur = min(count(user), goal)
        items.append(dict(key=key, title=title, desc=desc, reward=reward, cur=cur, goal=goal,
                          done=is_done, ready=(not is_done) and cur >= goal))
    items.insert(0, dict(key="daily", title="Presente diário", desc="Entre todo dia e pegue.",
                         reward=DAILY, cur=0 if today_key not in done else 1, goal=1,
                         done=today_key in done, ready=today_key not in done))
    return render_template("tasks.html", items=items)


_trend_cache = {"t": 0, "v": []}


def trending(limit=8):
    import time
    if time.time() - _trend_cache["t"] < 120:
        return _trend_cache["v"][:limit]
    _trend_cache["v"] = _compute_trending(20)
    _trend_cache["t"] = time.time()
    return _trend_cache["v"][:limit]


def _compute_trending(limit=8):
    since = datetime.utcnow() - timedelta(days=7)
    rows = core.query_all("SELECT content FROM posts WHERE created_at >= %s AND content IS NOT NULL", (since,))
    c = Counter()
    for r in rows:
        for tag in {m.group(2).lower() for m in TAG_RE.finditer(r["content"]) if m.group(1) == "#"}:
            c[tag] += 1
    return c.most_common(limit)


@bp.route("/buscar")
def buscar():
    user = core.get_current_user()
    if not user:
        return redirect(url_for("login"))
    q = request.args.get("q", "").strip()[:80]
    users, posts = [], []
    if q:
        like = "%" + q.lstrip("@").replace("%", "").replace("_", "") + "%"
        users = core.query_all(
            """SELECT id, name, username, profile_photo, verified FROM users
               WHERE (username ILIKE %s OR name ILIKE %s) AND banned_at IS NULL
               ORDER BY verified DESC, username LIMIT 12""", (like, like))
        plike = "%" + q.replace("%", "").replace("_", "") + "%"
        posts = core.query_all(
            """SELECT p.*, u.name, u.username, u.profile_photo, u.verified,
                      (SELECT COUNT(*) FROM post_likes pl WHERE pl.post_id=p.id) AS like_count,
                      (SELECT COUNT(*) FROM comments c WHERE c.post_id=p.id) AS comment_count,
                      EXISTS (SELECT 1 FROM post_likes pl2 WHERE pl2.post_id=p.id AND pl2.user_id=%s) AS liked
               FROM posts p JOIN users u ON u.id=p.author_id
               WHERE p.content ILIKE %s
                 AND p.author_id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id=%s)
                 AND p.author_id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id=%s)
               ORDER BY p.id DESC LIMIT 30""",
            (user["id"], plike, user["id"], user["id"]))
    return render_template("search.html", q=q, users=users, posts=posts, tags=trending())


NAME_FX = [
    ("fluxo", "Fluxo", "Degradê vermelho e dourado correndo"),
    ("neon", "Neon", "Brilho vermelho pulsando"),
    ("glitch", "Glitch", "Letras com ruído RGB"),
    ("brasa", "Brasa", "Chama tremulando"),
    ("ouro", "Ouro", "Reflexo dourado que passa"),
    ("prisma", "Prisma", "Cores mudando suavemente"),
]
_fx_cache = {"t": 0, "v": {}}


def fx_for(username):
    """Classe CSS do nick animado de um usuário (cache de 60s, 1 consulta)."""
    import time
    if time.time() - _fx_cache["t"] > 60:
        rows = core.query_all("SELECT username, name_fx FROM users WHERE name_fx IS NOT NULL")
        _fx_cache["v"] = {r["username"]: r["name_fx"] for r in rows}
        _fx_cache["t"] = time.time()
    fx = _fx_cache["v"].get(username)
    return f"nfx nfx-{fx}" if fx else ""


@bp.route("/nicks", methods=["GET", "POST"])
def nicks():
    """Prévia/seleção de nicks animados — por enquanto só administradores."""
    import extras
    user = core.get_current_user()
    if not user or not extras.is_admin(user):
        from flask import abort
        abort(404)
    if request.method == "POST":
        key = request.form.get("fx", "")
        valid = {k for k, _, _ in NAME_FX}
        core.execute("UPDATE users SET name_fx = %s WHERE id = %s", (key if key in valid else None, user["id"]))
        _fx_cache["t"] = 0
        flash("Nick atualizado.")
        return redirect(url_for("content.nicks"))
    return render_template("nicks.html", fx_list=NAME_FX, current=user.get("name_fx"))


FRAMES = [
    (1, "Brasa", "Anel de fogo girando com brilho"),
    (2, "Neon", "Anel vermelho que pulsa"),
    (3, "Rubi", "Joia escura com reflexo que passa"),
    (4, "Glitch", "Ruído RGB tremendo"),
    (5, "Aura", "Anel em degradê com estrela orbitando"),
]


@bp.route("/molduras")
def molduras():
    """Prévia das molduras animadas — por enquanto só para administradores."""
    import extras
    user = core.get_current_user()
    if not user or not extras.is_admin(user):
        from flask import abort
        abort(404)
    return render_template("frames.html", frames=FRAMES)


def install(app, namespace):
    for name in ("get_db", "query_one", "query_all", "execute", "get_current_user"):
        setattr(core, name, namespace[name])
    app.register_blueprint(bp)
    app.add_template_filter(rich, "rich")
    app.jinja_env.globals["fx_for"] = fx_for

    @app.context_processor
    def _trend_ctx():
        return {"k_trending": trending}
