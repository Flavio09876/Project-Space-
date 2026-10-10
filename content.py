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


# chave: (título, descrição, recompensa, função de verificação)
def _has(sql, uid):
    row = core.query_one(sql, (uid,))
    return bool(row and row["n"])


TASKS = [
    ("profile", "Completar o perfil", "Tenha foto, bio e capa.", 100,
     lambda u: bool(u.get("profile_photo")) and bool((u.get("bio") or "").strip())
     and bool(u.get("profile_header_value"))),
    ("first_post", "Primeira publicação", "Publique algo no K.", 50,
     lambda u: _has("SELECT COUNT(*) AS n FROM posts WHERE author_id=%s", u["id"])),
    ("follow3", "Seguir 3 pessoas", "Encontre gente em Descobrir.", 50,
     lambda u: core.query_one("SELECT COUNT(*) AS n FROM follows WHERE follower_id=%s", (u["id"],))["n"] >= 3),
    ("first_comment", "Primeiro comentário", "Comente em uma publicação.", 30,
     lambda u: _has("SELECT COUNT(*) AS n FROM comments WHERE user_id=%s", u["id"])),
    ("first_message", "Primeira mensagem", "Converse com alguém no chat.", 30,
     lambda u: _has("SELECT COUNT(*) AS n FROM messages WHERE sender_id=%s", u["id"])),
]
DAILY = 20


def migrate():
    core.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS wallet_task_once ON wallet_ledger (user_id, note) WHERE kind = 'task'"
    )
    core.execute("CREATE INDEX IF NOT EXISTS posts_created_idx ON posts (created_at DESC)")


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
            elif not task[4](user):
                flash("Ainda não cumpriu essa tarefa.")
            else:
                ok = _claim(user["id"], key, task[3])
                flash(f"+{task[3]} kcoin!" if ok else "Você já recebeu essa recompensa.")
        return redirect(url_for("content.tarefas"))

    items = []
    for key, title, desc, reward, check in TASKS:
        is_done = key in done
        items.append(dict(key=key, title=title, desc=desc, reward=reward,
                          done=is_done, ready=(not is_done) and bool(check(user))))
    items.insert(0, dict(key="daily", title="Presente diário", desc="Entre todo dia e pegue.",
                         reward=DAILY, done=today_key in done, ready=today_key not in done))
    return render_template("tasks.html", items=items)


def trending(limit=8):
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


def install(app, namespace):
    for name in ("get_db", "query_one", "query_all", "execute", "get_current_user"):
        setattr(core, name, namespace[name])
    app.register_blueprint(bp)
    app.add_template_filter(rich, "rich")

    @app.context_processor
    def _trend_ctx():
        return {"k_trending": trending}
