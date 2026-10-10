"""
K — recursos extras da plataforma.

Mantido em arquivo separado para não inflar o app.py. Reúne:
  - migrações do banco (só ADD COLUMN / CREATE TABLE IF NOT EXISTS; nada é apagado)
  - configurações da conta (senha, e-mail, bloqueados, excluir conta)
  - editar / apagar publicações, comentários e mensagens
  - bloquear e denunciar
  - painel administrativo (apenas contas em ADMIN_USERNAMES; padrão: eozffprivacy)
  - confirmação de e-mail para contas NOVAS (contas antigas continuam valendo)
  - proteções básicas (origem das requisições, limite de tentativas de login, cabeçalhos)

Ligado ao app no final do app.py com:  extras.install(app, globals())
"""

import hashlib
import json
import logging
import os
import re
import secrets
import time
import urllib.request
from datetime import date
from functools import wraps
from types import SimpleNamespace
from urllib.parse import urlparse

from flask import (
    Blueprint,
    current_app,
    abort,
    flash,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

log = logging.getLogger("k.extras")
bp = Blueprint("extras", __name__)

# Preenchido por install() com as funções do app.py (query_one, execute, ...).
core = SimpleNamespace()

PASSWORD_MIN = 8
MEDIA_PREFIX = "[[PZ_MEDIA]]"
MEDIA_SPLIT = "[[PZ_TEXT]]"

REPORT_REASONS = [
    ("spam", "Spam ou propaganda"),
    ("harassment", "Assédio ou bullying"),
    ("hate", "Ódio ou violência"),
    ("sexual", "Conteúdo sexual"),
    ("scam", "Golpe ou fraude"),
    ("fake", "Perfil falso ou se passando por outra pessoa"),
    ("minor", "Envolve menores de idade"),
    ("other", "Outro motivo"),
]

RESERVED_USERNAMES = {
    "admin", "administrador", "administrator", "root", "suporte", "support",
    "moderador", "moderator", "staff", "oficial", "official", "k", "projectz",
    "sistema", "system",
}

DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com", "tempmail.com",
    "temp-mail.org", "yopmail.com", "trashmail.com", "sharklasers.com",
    "throwawaymail.com", "getnada.com", "maildrop.cc", "dispostable.com",
    "fakeinbox.com", "mintemail.com", "emailondeck.com", "moakt.com",
}

EMAIL_RE = re.compile(
    r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}$"
)

# Ações que exigem e-mail confirmado (só quando há serviço de e-mail configurado).
VERIFIED_ONLY = {
    "create_post",
    "post_detail",
    "send_message",
    "extras.edit_post",
    "extras.edit_comment",
}


# ============================================================
# MIGRAÇÕES
# ============================================================

def migrate():
    """Cria o que falta. Seguro para rodar a cada inicialização."""
    statements = [
        # Contas antigas entram como e-mail confirmado (DEFAULT TRUE).
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN NOT NULL DEFAULT TRUE",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_token_hash TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_token_at TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS pending_email TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS banned_at TIMESTAMP",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS ban_reason TEXT",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS signup_ip_hash TEXT",
        "CREATE INDEX IF NOT EXISTS users_signup_ip_idx ON users (signup_ip_hash, created_at)",
        "ALTER TABLE posts ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP",
        "ALTER TABLE comments ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP",
        "ALTER TABLE messages ADD COLUMN IF NOT EXISTS edited_at TIMESTAMP",
        """
        CREATE TABLE IF NOT EXISTS blocks (
            blocker_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            blocked_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (blocker_id, blocked_id),
            CHECK (blocker_id <> blocked_id)
        )
        """,
        "CREATE INDEX IF NOT EXISTS blocks_blocked_idx ON blocks (blocked_id)",
        """
        CREATE TABLE IF NOT EXISTS reports (
            id BIGSERIAL PRIMARY KEY,
            reporter_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            target_type TEXT NOT NULL,
            target_id BIGINT NOT NULL,
            target_user_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            reason TEXT NOT NULL,
            details TEXT NOT NULL DEFAULT '',
            snapshot TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            resolution TEXT,
            resolved_by BIGINT,
            resolved_at TIMESTAMP,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE UNIQUE INDEX IF NOT EXISTS reports_open_unique
        ON reports (reporter_id, target_type, target_id)
        WHERE status = 'open'
        """,
        "CREATE INDEX IF NOT EXISTS reports_status_idx ON reports (status, id DESC)",
        """
        CREATE TABLE IF NOT EXISTS admin_log (
            id BIGSERIAL PRIMARY KEY,
            admin_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
            action TEXT NOT NULL,
            target_type TEXT,
            target_id BIGINT,
            details TEXT NOT NULL DEFAULT '',
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS comments_post_idx ON comments (post_id)",
    ]
    with core.get_db() as db:
        with db.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        db.commit()


# ============================================================
# AJUDANTES
# ============================================================

def admin_usernames():
    raw = os.environ.get("ADMIN_USERNAMES", "eozffprivacy")
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def is_admin(user):
    return bool(user) and str(user["username"]).lower() in admin_usernames()


def mail_enabled():
    """Confirmação de e-mail está desligada. Para religar: EMAIL_VERIFICATION=1 + serviço de e-mail."""
    return bool(
        os.environ.get("EMAIL_VERIFICATION") == "1"
        and os.environ.get("MAIL_FROM")
        and (os.environ.get("BREVO_API_KEY") or os.environ.get("RESEND_API_KEY"))
    )


def _me():
    if not hasattr(g, "_k_me"):
        g._k_me = core.get_current_user()
    return g._k_me


def login_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        if not core.get_current_user():
            return redirect(url_for("login"))
        return function(*args, **kwargs)
    return wrapper


def admin_required(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        user = core.get_current_user()
        if not user:
            return redirect(url_for("login"))
        if not is_admin(user):
            abort(404)
        return function(*args, **kwargs)
    return wrapper


def safe_next(default_endpoint="home", **values):
    target = request.form.get("next") or request.args.get("next") or ""
    if target.startswith("/") and not target.startswith("//") and "\\" not in target:
        return target
    return url_for(default_endpoint, **values)


def log_admin(admin_id, action, target_type=None, target_id=None, details=""):
    core.execute(
        """
        INSERT INTO admin_log (admin_id, action, target_type, target_id, details)
        VALUES (%s, %s, %s, %s, %s)
        """,
        (admin_id, action, target_type, target_id, (details or "")[:500]),
    )


def media_preview(content):
    text = (content or "").strip()
    if text.startswith(MEDIA_PREFIX):
        parts = text[len(MEDIA_PREFIX):].split(MEDIA_SPLIT, 1)
        caption = parts[1].strip() if len(parts) > 1 else ""
        return "[imagem] " + caption if caption else "[imagem]"
    return text


def block_state(me_id, other_id):
    """'by_me' (eu bloqueei), 'by_them' (me bloquearam) ou None."""
    row = core.query_one(
        """
        SELECT blocker_id FROM blocks
        WHERE (blocker_id = %s AND blocked_id = %s)
           OR (blocker_id = %s AND blocked_id = %s)
        ORDER BY (blocker_id = %s) DESC
        LIMIT 1
        """,
        (me_id, other_id, other_id, me_id, me_id),
    )
    if not row:
        return None
    return "by_me" if row["blocker_id"] == me_id else "by_them"


def _sha(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _abs_url(endpoint, **values):
    path = url_for(endpoint, **values)
    base = os.environ.get("APP_BASE_URL") or request.url_root.rstrip("/")
    if os.environ.get("RENDER") and base.startswith("http://"):
        base = "https://" + base[len("http://"):]
    return base.rstrip("/") + path


# ============================================================
# E-MAIL (via API HTTPS: Brevo ou Resend — o Render bloqueia SMTP no plano grátis)
# ============================================================

def send_mail(to_address, subject, html, text):
    sender = os.environ.get("MAIL_FROM", "")
    name = os.environ.get("MAIL_FROM_NAME", "K")
    headers = {"Content-Type": "application/json", "User-Agent": "K-Platform/1.0"}

    try:
        if os.environ.get("BREVO_API_KEY"):
            url = "https://api.brevo.com/v3/smtp/email"
            headers["api-key"] = os.environ["BREVO_API_KEY"]
            body = {
                "sender": {"name": name, "email": sender},
                "to": [{"email": to_address}],
                "subject": subject,
                "htmlContent": html,
                "textContent": text,
            }
        elif os.environ.get("RESEND_API_KEY"):
            url = "https://api.resend.com/emails"
            headers["Authorization"] = "Bearer " + os.environ["RESEND_API_KEY"]
            body = {
                "from": f"{name} <{sender}>",
                "to": [to_address],
                "subject": subject,
                "html": html,
                "text": text,
            }
        else:
            return False

        request_obj = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request_obj, timeout=10) as response:
            return 200 <= response.status < 300
    except Exception:
        log.exception("Falha ao enviar e-mail")
        return False


def _verification_mail(link, changing):
    title = "Confirme seu novo e-mail" if changing else "Confirme seu e-mail"
    html = (
        '<div style="font-family:Arial,sans-serif;background:#050506;padding:32px;color:#f5f5f6">'
        '<div style="max-width:460px;margin:auto;background:#0e0e10;border:1px solid #232327;'
        'border-radius:18px;padding:28px">'
        '<div style="width:44px;height:44px;border-radius:12px;background:#e11d2e;color:#fff;'
        'font-weight:900;font-size:24px;text-align:center;line-height:44px">K</div>'
        f'<h2 style="margin:18px 0 8px">{title}</h2>'
        '<p style="color:#b5b5bd;line-height:1.55">Clique no botão para confirmar. '
        'O link vale por 48 horas.</p>'
        f'<p><a href="{link}" style="display:inline-block;background:#e11d2e;color:#fff;'
        'text-decoration:none;padding:12px 22px;border-radius:999px;font-weight:700">'
        'Confirmar e-mail</a></p>'
        '<p style="color:#7d7d86;font-size:12px">Se não foi você, ignore esta mensagem.</p>'
        '</div></div>'
    )
    text = f"{title}: {link}\n\nO link vale por 48 horas. Se não foi você, ignore."
    return title + " — K", html, text


def issue_verification(user_id, target_email, changing=False):
    """Gera o token e envia o e-mail. Retorna True se enviou."""
    token = secrets.token_urlsafe(32)
    core.execute(
        """
        UPDATE users
        SET email_token_hash = %s,
            email_token_at = CURRENT_TIMESTAMP,
            pending_email = %s
        WHERE id = %s
        """,
        (_sha(token), target_email if changing else None, user_id),
    )
    link = _abs_url("extras.verify_email", token=token)
    subject, html, text = _verification_mail(link, changing)
    return send_mail(target_email, subject, html, text)


# ============================================================
# CADASTRO (chamado pelo register() do app.py)
# ============================================================

def ip_hash():
    """IP do visitante, guardado só como hash (não dá para recuperar o IP original)."""
    ip = _client_ip()
    return hashlib.sha256((current_app.config["SECRET_KEY"] + "|" + ip).encode("utf-8")).hexdigest()


def signup_limit_reached():
    try:
        limit = int(os.environ.get("MAX_ACCOUNTS_PER_IP", "3"))
        hours = int(os.environ.get("SIGNUP_WINDOW_HOURS", "24"))
    except ValueError:
        limit, hours = 3, 24
    if limit <= 0:
        return False
    row = core.query_one(
        """
        SELECT COUNT(*) AS n FROM users
        WHERE signup_ip_hash = %s
          AND created_at > CURRENT_TIMESTAMP - make_interval(hours => %s)
        """,
        (ip_hash(), hours),
    )
    return bool(row) and row["n"] >= limit


def validate_registration(username, email, birth_date):
    """Retorna uma mensagem de erro ou None."""
    if signup_limit_reached():
        return "Já foram criadas contas demais a partir da sua rede. Tente novamente mais tarde."

    if username in RESERVED_USERNAMES:
        return "Esse nome de usuário é reservado. Escolha outro."

    if len(username) > 30:
        return "O nome de usuário pode ter no máximo 30 caracteres."

    if len(email) > 254 or not EMAIL_RE.match(email):
        return "Digite um e-mail válido."

    try:
        born = date.fromisoformat(birth_date)
    except ValueError:
        return "Data de nascimento inválida."

    today = date.today()
    age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    if born > today or age > 120:
        return "Data de nascimento inválida."

    if age < 13:
        return "Você precisa ter pelo menos 13 anos para criar uma conta."

    return None


def after_register(user_id, email):
    """Guarda o hash do IP do cadastro e, se a confirmação estiver ligada, envia o e-mail."""
    core.execute(
        "UPDATE users SET signup_ip_hash = %s, email_verified = %s WHERE id = %s",
        (ip_hash(), not mail_enabled(), user_id),
    )

    if mail_enabled():
        if issue_verification(user_id, email):
            flash("Enviamos um link de confirmação para o seu e-mail.", "success")
        else:
            flash("Não foi possível enviar o e-mail agora. Use “Reenviar” no aviso do topo.", "error")


# ============================================================
# PROTEÇÕES GERAIS
# ============================================================

_FAILS = {}
_FAIL_LIMIT = 10
_FAIL_WINDOW = 600


def _client_ip():
    for header in ("CF-Connecting-IP", "True-Client-IP"):
        value = request.headers.get(header, "").strip()
        if value:
            return value
    forwarded = request.headers.get("X-Forwarded-For", "")
    return (forwarded.split(",")[0].strip() or request.remote_addr or "?")


def _recent_failures():
    now = time.time()
    attempts = [t for t in _FAILS.get(_client_ip(), []) if now - t < _FAIL_WINDOW]
    _FAILS[_client_ip()] = attempts
    return len(attempts)


def _guards():
    # 1) Requisições que alteram dados precisam vir do próprio site.
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        origin = request.headers.get("Origin")
        if origin and origin != "null" and urlparse(origin).netloc != request.host:
            abort(403)

    endpoint = request.endpoint
    if not endpoint or endpoint == "static":
        return None

    # 2) Limite de tentativas de login.
    if endpoint == "login" and request.method == "POST" and _recent_failures() >= _FAIL_LIMIT:
        flash("Muitas tentativas. Aguarde alguns minutos e tente de novo.", "error")
        return render_template("login.html"), 429

    user = None

    # 3) Contas novas precisam confirmar o e-mail (somente com serviço de e-mail ativo).
    if request.method == "POST" and endpoint in VERIFIED_ONLY and mail_enabled():
        user = core.get_current_user()
        if user and not user.get("email_verified", True):
            message = "Confirme seu e-mail para publicar, comentar e enviar mensagens."
            if endpoint == "send_message":
                return jsonify({"ok": False, "error": message}), 403
            flash(message, "error")
            return redirect(request.referrer or url_for("home"))

    # 4) Bloqueio impede troca de mensagens nos dois sentidos.
    if endpoint == "send_message":
        user = user or core.get_current_user()
        conversation_id = (request.view_args or {}).get("conversation_id")
        if user and conversation_id:
            other = core.query_one(
                """
                SELECT user_id FROM conversation_members
                WHERE conversation_id = %s AND user_id <> %s
                LIMIT 1
                """,
                (conversation_id, user["id"]),
            )
            if other and block_state(user["id"], other["user_id"]):
                return jsonify({
                    "ok": False,
                    "error": "Você não pode enviar mensagens para este usuário.",
                }), 403

    # 5) Quem te bloqueou não aparece para você.
    if endpoint == "profile":
        user = user or core.get_current_user()
        target = core.query_one(
            "SELECT id FROM users WHERE username = %s",
            (core.username_clean((request.view_args or {}).get("username", "")),),
        )
        if user and target and target["id"] != user["id"]:
            if block_state(user["id"], target["id"]) == "by_them":
                abort(404)

    return None


def _after(response):
    if (
        request.endpoint == "login"
        and request.method == "POST"
        and response.status_code == 200
    ):
        _FAILS.setdefault(_client_ip(), []).append(time.time())

    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response


def _context():
    values = {
        "is_admin": False,
        "email_unverified": False,
        "mail_on": mail_enabled(),
        "report_reasons": REPORT_REASONS,
    }

    user = _me()
    if not user:
        return values

    values["is_admin"] = is_admin(user)
    values["email_unverified"] = mail_enabled() and not user.get("email_verified", True)
    endpoint = request.endpoint
    args = request.view_args or {}

    if values["is_admin"]:
        row = core.query_one("SELECT COUNT(*) AS n FROM reports WHERE status = 'open'")
        values["open_reports"] = row["n"] if row else 0

    if endpoint == "chat":
        rows = core.query_all(
            """
            SELECT m.sender_id, COUNT(*) AS n
            FROM messages m
            JOIN conversation_members cm
              ON cm.conversation_id = m.conversation_id AND cm.user_id = %s
            WHERE m.sender_id <> %s AND m.read_at IS NULL
            GROUP BY m.sender_id
            """,
            (user["id"], user["id"]),
        )
        values["unread_map"] = {row["sender_id"]: row["n"] for row in rows}

    if endpoint in ("conversation", "profile"):
        target = core.query_one(
            "SELECT id FROM users WHERE username = %s",
            (core.username_clean(args.get("username", "")),),
        )
        if target and target["id"] != user["id"]:
            state = block_state(user["id"], target["id"])
            values["block_state"] = state
            values["i_blocked"] = state == "by_me"

    return values


def install(app, namespace):
    for name in (
        "get_db", "query_one", "query_all", "execute",
        "get_current_user", "username_clean",
    ):
        setattr(core, name, namespace[name])

    app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
    app.config["SESSION_COOKIE_HTTPONLY"] = True
    if os.environ.get("RENDER"):
        app.config["SESSION_COOKIE_SECURE"] = True

    app.register_blueprint(bp)
    app.before_request(_guards)
    app.after_request(_after)
    app.context_processor(_context)
    app.register_error_handler(403, _forbidden)
    app.register_error_handler(429, _forbidden)
    app.register_error_handler(Exception, _unexpected_error)


_FALLBACK_ERROR_PAGE = (
    '<!doctype html><html lang="pt-BR"><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width,initial-scale=1"><title>K — erro</title>'
    '<body style="margin:0;min-height:100vh;display:grid;place-items:center;background:#050506;'
    'color:#f5f5f6;font-family:system-ui,sans-serif;text-align:center;padding:24px">'
    '<div><div style="width:64px;height:64px;margin:0 auto 18px;border-radius:18px;'
    'background:linear-gradient(145deg,#ff2d40,#8f0a16);display:grid;place-items:center;'
    'font-size:36px;font-weight:900">K</div><h1 style="font-size:22px;margin:0 0 8px">Algo deu errado</h1>'
    '<p style="color:#8a8a93;margin:0 0 22px">Tente de novo em instantes. Código: {ref}</p>'
    '<a href="/" style="background:#e11d2e;color:#fff;border-radius:999px;padding:12px 24px;'
    'text-decoration:none;font-weight:700">Voltar ao início</a></div></body></html>'
)


def _unexpected_error(error):
    """Qualquer erro inesperado vira uma tela amigável (e o erro real vai para o log)."""
    from werkzeug.exceptions import HTTPException

    if isinstance(error, HTTPException):
        return error

    ref = secrets.token_hex(3)
    log.error("Erro interno [%s] em %s %s", ref, request.method, request.path, exc_info=error)

    if request.path.startswith("/api/"):
        return jsonify({
            "ok": False,
            "error": "Erro interno. Tente novamente. (código " + ref + ")",
        }), 500

    try:
        return render_template(
            "error.html",
            title="Algo deu errado",
            message="Tivemos um problema do nosso lado e já registramos o erro. Tente de novo em instantes.",
            ref=ref,
            retry=True,
        ), 500
    except Exception:
        log.exception("Falha ao montar a tela de erro [%s]", ref)
        return _FALLBACK_ERROR_PAGE.replace("{ref}", ref), 500


def _forbidden(error):
    return render_template(
        "error.html",
        title="Sem permissão",
        message="Você não tem permissão para fazer isso.",
    ), 403


# ============================================================
# E-MAIL: CONFIRMAR / REENVIAR
# ============================================================

@bp.get("/verify-email/<token>")
def verify_email(token):
    row = core.query_one(
        """
        SELECT * FROM users
        WHERE email_token_hash = %s
          AND email_token_at > CURRENT_TIMESTAMP - INTERVAL '48 hours'
        """,
        (_sha(token),),
    )

    if not row:
        flash("Link inválido ou expirado. Peça um novo no aviso do topo.", "error")
        return redirect(url_for("home") if core.get_current_user() else url_for("login"))

    try:
        if row.get("pending_email"):
            core.execute(
                """
                UPDATE users
                SET email = pending_email, pending_email = NULL,
                    email_verified = TRUE, email_token_hash = NULL
                WHERE id = %s
                """,
                (row["id"],),
            )
        else:
            core.execute(
                """
                UPDATE users
                SET email_verified = TRUE, email_token_hash = NULL
                WHERE id = %s
                """,
                (row["id"],),
            )
    except Exception:
        log.exception("Falha ao confirmar e-mail")
        flash("Esse e-mail já está em uso por outra conta.", "error")
        return redirect(url_for("extras.settings"))

    flash("E-mail confirmado! Tudo liberado.", "success")
    return redirect(url_for("home") if core.get_current_user() else url_for("login"))


@bp.post("/resend-verification")
@login_required
def resend_verification():
    user = core.get_current_user()

    if not mail_enabled() or (user.get("email_verified", True) and not user.get("pending_email")):
        return redirect(request.referrer or url_for("home"))

    recent = core.query_one(
        """
        SELECT 1 FROM users
        WHERE id = %s AND email_token_at > CURRENT_TIMESTAMP - INTERVAL '60 seconds'
        """,
        (user["id"],),
    )
    if recent:
        flash("Aguarde um minuto antes de pedir outro e-mail.", "error")
        return redirect(request.referrer or url_for("home"))

    target = user.get("pending_email") or user["email"]
    ok = issue_verification(user["id"], target, changing=bool(user.get("pending_email")))
    flash(
        "Enviamos o link para " + target + "." if ok else "Não foi possível enviar agora. Tente mais tarde.",
        "success" if ok else "error",
    )
    return redirect(request.referrer or url_for("home"))


# ============================================================
# CONFIGURAÇÕES
# ============================================================

@bp.get("/settings")
@login_required
def settings():
    user = core.get_current_user()
    blocked = core.query_all(
        """
        SELECT u.id, u.name, u.username, u.profile_photo
        FROM blocks b
        JOIN users u ON u.id = b.blocked_id
        WHERE b.blocker_id = %s
        ORDER BY b.created_at DESC
        """,
        (user["id"],),
    )
    return render_template("settings.html", blocked=blocked, account=user)


@bp.post("/settings/password")
@login_required
def change_password():
    user = core.get_current_user()
    current = request.form.get("current_password", "")
    new = request.form.get("new_password", "")
    confirm = request.form.get("confirm_password", "")

    if not check_password_hash(user["password_hash"], current):
        flash("A senha atual está incorreta.", "error")
    elif len(new) < PASSWORD_MIN:
        flash(f"A nova senha precisa ter pelo menos {PASSWORD_MIN} caracteres.", "error")
    elif new != confirm:
        flash("A confirmação não confere com a nova senha.", "error")
    elif new == current:
        flash("A nova senha precisa ser diferente da atual.", "error")
    else:
        core.execute(
            "UPDATE users SET password_hash = %s WHERE id = %s",
            (generate_password_hash(new), user["id"]),
        )
        flash("Senha alterada com sucesso.", "success")

    return redirect(url_for("extras.settings") + "#conta")


@bp.post("/settings/email")
@login_required
def change_email():
    user = core.get_current_user()
    password = request.form.get("password", "")
    email = request.form.get("email", "").strip().lower()

    if not check_password_hash(user["password_hash"], password):
        flash("Senha incorreta.", "error")
    elif len(email) > 254 or not EMAIL_RE.match(email):
        flash("Digite um e-mail válido.", "error")
    elif email == user["email"]:
        flash("Esse já é o seu e-mail atual.", "error")
    elif core.query_one("SELECT 1 FROM users WHERE email = %s", (email,)):
        flash("Esse e-mail já está em uso.", "error")
    elif mail_enabled():
        if issue_verification(user["id"], email, changing=True):
            flash("Enviamos um link para " + email + ". O e-mail só muda depois de confirmar.", "success")
        else:
            flash("Não foi possível enviar o e-mail agora. Tente mais tarde.", "error")
    else:
        core.execute("UPDATE users SET email = %s WHERE id = %s", (email, user["id"]))
        flash("E-mail atualizado.", "success")

    return redirect(url_for("extras.settings") + "#conta")


def delete_user_account(user_id):
    core.execute("DELETE FROM users WHERE id = %s", (user_id,))
    core.execute(
        """
        DELETE FROM conversations c
        WHERE NOT EXISTS (
            SELECT 1 FROM conversation_members m WHERE m.conversation_id = c.id
        )
        """
    )


@bp.post("/settings/delete")
@login_required
def delete_account():
    user = core.get_current_user()

    if is_admin(user):
        flash("A conta administradora não pode ser excluída por aqui.", "error")
        return redirect(url_for("extras.settings") + "#perigo")

    if not check_password_hash(user["password_hash"], request.form.get("password", "")):
        flash("Senha incorreta.", "error")
        return redirect(url_for("extras.settings") + "#perigo")

    if request.form.get("confirm_username", "").strip().lstrip("@").lower() != user["username"].lower():
        flash("Digite seu @ exatamente para confirmar.", "error")
        return redirect(url_for("extras.settings") + "#perigo")

    delete_user_account(user["id"])
    session.clear()
    flash("Sua conta foi excluída. Sentiremos sua falta.", "success")
    return redirect(url_for("welcome"))


# ============================================================
# BLOQUEAR
# ============================================================

@bp.post("/block/<username>")
@login_required
def block_user(username):
    user = core.get_current_user()
    target = core.query_one(
        "SELECT id, username FROM users WHERE username = %s",
        (core.username_clean(username),),
    )

    if not target or target["id"] == user["id"]:
        abort(404)

    if request.form.get("action") == "unblock":
        core.execute(
            "DELETE FROM blocks WHERE blocker_id = %s AND blocked_id = %s",
            (user["id"], target["id"]),
        )
        flash("@" + target["username"] + " foi desbloqueado.", "success")
        return redirect(safe_next("extras.settings"))

    core.execute(
        """
        INSERT INTO blocks (blocker_id, blocked_id) VALUES (%s, %s)
        ON CONFLICT DO NOTHING
        """,
        (user["id"], target["id"]),
    )
    core.execute(
        """
        DELETE FROM follows
        WHERE (follower_id = %s AND following_id = %s)
           OR (follower_id = %s AND following_id = %s)
        """,
        (user["id"], target["id"], target["id"], user["id"]),
    )
    flash("@" + target["username"] + " foi bloqueado. Vocês não se veem mais.", "success")
    return redirect(url_for("home"))


# ============================================================
# DENUNCIAR
# ============================================================

def _report_target(kind, target_id, user_id):
    """Retorna (id_do_autor, texto_para_o_admin) ou None."""
    if kind == "post":
        row = core.query_one("SELECT author_id AS owner, content FROM posts WHERE id = %s", (target_id,))
        return (row["owner"], row["content"]) if row else None

    if kind == "comment":
        row = core.query_one("SELECT user_id AS owner, content FROM comments WHERE id = %s", (target_id,))
        return (row["owner"], row["content"]) if row else None

    if kind == "user":
        row = core.query_one("SELECT id AS owner, username FROM users WHERE id = %s", (target_id,))
        return (row["owner"], "@" + row["username"]) if row else None

    if kind == "message":
        row = core.query_one(
            """
            SELECT m.sender_id AS owner, m.content
            FROM messages m
            JOIN conversation_members cm
              ON cm.conversation_id = m.conversation_id AND cm.user_id = %s
            WHERE m.id = %s
            """,
            (user_id, target_id),
        )
        return (row["owner"], media_preview(row["content"])) if row else None

    return None


@bp.route("/report/<kind>/<int:target_id>", methods=["GET", "POST"])
@login_required
def report(kind, target_id):
    user = core.get_current_user()
    found = _report_target(kind, target_id, user["id"])

    if not found or found[0] == user["id"]:
        abort(404)

    owner_id, snapshot = found

    if request.method == "POST":
        reason = request.form.get("reason", "")
        if reason not in dict(REPORT_REASONS):
            flash("Escolha um motivo.", "error")
        else:
            core.execute(
                """
                INSERT INTO reports
                    (reporter_id, target_type, target_id, target_user_id, reason, details, snapshot)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (reporter_id, target_type, target_id) WHERE status = 'open'
                DO NOTHING
                """,
                (
                    user["id"], kind, target_id, owner_id, reason,
                    request.form.get("details", "").strip()[:500],
                    (snapshot or "")[:600],
                ),
            )
            flash("Denúncia enviada. Obrigado por ajudar a manter o K seguro.", "success")
            return redirect(safe_next("home"))

    return render_template(
        "report.html",
        kind=kind,
        target_id=target_id,
        snapshot=(snapshot or "")[:300],
        next_url=request.args.get("next") or request.form.get("next") or "",
    )


# ============================================================
# EDITAR / APAGAR — PUBLICAÇÕES
# ============================================================

@bp.route("/post/<int:post_id>/edit", methods=["GET", "POST"])
@login_required
def edit_post(post_id):
    user = core.get_current_user()
    post = core.query_one("SELECT * FROM posts WHERE id = %s", (post_id,))

    if not post:
        abort(404)
    if post["author_id"] != user["id"]:
        abort(403)

    if request.method == "POST":
        content = request.form.get("content", "").strip()[:5000]
        if not content and not post["media_filename"]:
            flash("Escreva algo ou mantenha a imagem.", "error")
        else:
            core.execute(
                "UPDATE posts SET content = %s, edited_at = CURRENT_TIMESTAMP WHERE id = %s",
                (content, post_id),
            )
            flash("Publicação atualizada.", "success")
            return redirect(url_for("post_detail", post_id=post_id))

    return render_template(
        "edit_content.html",
        title="Editar publicação",
        content=post["content"],
        max_length=5000,
        back=url_for("post_detail", post_id=post_id),
    )


def _close_reports(target_type, target_id, admin_id, resolution):
    core.execute(
        """
        UPDATE reports
        SET status = 'resolved', resolution = %s, resolved_by = %s, resolved_at = CURRENT_TIMESTAMP
        WHERE target_type = %s AND target_id = %s AND status = 'open'
        """,
        (resolution, admin_id, target_type, target_id),
    )


@bp.post("/post/<int:post_id>/delete")
@login_required
def delete_post(post_id):
    user = core.get_current_user()
    post = core.query_one("SELECT id, author_id, content FROM posts WHERE id = %s", (post_id,))

    if not post:
        abort(404)

    by_admin = is_admin(user) and post["author_id"] != user["id"]
    if post["author_id"] != user["id"] and not by_admin:
        abort(403)

    core.execute("DELETE FROM posts WHERE id = %s", (post_id,))

    if by_admin:
        log_admin(user["id"], "apagou publicação", "post", post_id, (post["content"] or "")[:200])
        _close_reports("post", post_id, user["id"], "conteúdo removido")

    flash("Publicação apagada.", "success")
    destination = safe_next("home")
    if f"/post/{post_id}" in destination:
        destination = url_for("home")
    return redirect(destination)


# ============================================================
# EDITAR / APAGAR — COMENTÁRIOS
# ============================================================

@bp.route("/comment/<int:comment_id>/edit", methods=["GET", "POST"])
@login_required
def edit_comment(comment_id):
    user = core.get_current_user()
    comment = core.query_one("SELECT * FROM comments WHERE id = %s", (comment_id,))

    if not comment:
        abort(404)
    if comment["user_id"] != user["id"]:
        abort(403)

    back = url_for("post_detail", post_id=comment["post_id"])

    if request.method == "POST":
        content = request.form.get("content", "").strip()[:2000]
        if not content:
            flash("O comentário não pode ficar vazio.", "error")
        else:
            core.execute(
                "UPDATE comments SET content = %s, edited_at = CURRENT_TIMESTAMP WHERE id = %s",
                (content, comment_id),
            )
            flash("Comentário atualizado.", "success")
            return redirect(back)

    return render_template(
        "edit_content.html",
        title="Editar comentário",
        content=comment["content"],
        max_length=2000,
        back=back,
    )


@bp.post("/comment/<int:comment_id>/delete")
@login_required
def delete_comment(comment_id):
    user = core.get_current_user()
    comment = core.query_one(
        """
        SELECT c.id, c.user_id, c.post_id, c.content, p.author_id AS post_author
        FROM comments c JOIN posts p ON p.id = c.post_id
        WHERE c.id = %s
        """,
        (comment_id,),
    )

    if not comment:
        abort(404)

    admin = is_admin(user)
    allowed = comment["user_id"] == user["id"] or comment["post_author"] == user["id"] or admin
    if not allowed:
        abort(403)

    core.execute("DELETE FROM comments WHERE id = %s", (comment_id,))

    if admin and comment["user_id"] != user["id"]:
        log_admin(user["id"], "apagou comentário", "comment", comment_id, (comment["content"] or "")[:200])
        _close_reports("comment", comment_id, user["id"], "conteúdo removido")

    flash("Comentário apagado.", "success")
    return redirect(url_for("post_detail", post_id=comment["post_id"]))


# ============================================================
# EDITAR / APAGAR — MENSAGENS (JSON)
# ============================================================

@bp.post("/api/messages/<int:message_id>/edit")
@login_required
def edit_message(message_id):
    user = core.get_current_user()
    payload = request.get_json(silent=True) or {}
    content = str(payload.get("content", "")).strip()

    if not content or len(content) > 5000:
        return jsonify({"ok": False, "error": "Mensagem vazia ou grande demais."}), 400

    message = core.query_one(
        "SELECT id, sender_id, content FROM messages WHERE id = %s", (message_id,)
    )
    if not message or message["sender_id"] != user["id"]:
        return jsonify({"ok": False, "error": "Sem permissão."}), 403
    if message["content"].startswith(MEDIA_PREFIX):
        return jsonify({"ok": False, "error": "Mensagens com imagem não podem ser editadas."}), 400

    core.execute(
        "UPDATE messages SET content = %s, edited_at = CURRENT_TIMESTAMP WHERE id = %s",
        (content, message_id),
    )
    return jsonify({"ok": True, "content": content})


@bp.post("/api/messages/<int:message_id>/delete")
@login_required
def delete_message(message_id):
    user = core.get_current_user()
    message = core.query_one(
        "SELECT id, sender_id FROM messages WHERE id = %s", (message_id,)
    )
    if not message or message["sender_id"] != user["id"]:
        return jsonify({"ok": False, "error": "Sem permissão."}), 403

    core.execute("DELETE FROM messages WHERE id = %s", (message_id,))
    return jsonify({"ok": True})


# ============================================================
# PAINEL ADMINISTRATIVO
# ============================================================

PAGE_SIZE = 30


def _like(value):
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + escaped + "%"


@bp.get("/admin")
@admin_required
def admin():
    tab = request.args.get("tab", "overview")
    if tab not in ("overview", "reports", "users", "posts", "log"):
        tab = "overview"

    query = (request.args.get("q") or "").strip()[:60]
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    offset = (page - 1) * PAGE_SIZE
    pattern = _like(query) if query else "%"

    data = {}

    if tab == "overview":
        data["stats"] = core.query_one(
            """
            SELECT
              (SELECT COUNT(*) FROM users) AS users,
              (SELECT COUNT(*) FROM users WHERE created_at > CURRENT_TIMESTAMP - INTERVAL '7 days') AS users_7d,
              (SELECT COUNT(*) FROM users WHERE banned_at IS NOT NULL) AS banned,
              (SELECT COUNT(*) FROM users WHERE NOT email_verified) AS unverified,
              (SELECT COUNT(*) FROM posts) AS posts,
              (SELECT COUNT(*) FROM comments) AS comments,
              (SELECT COUNT(*) FROM messages) AS messages,
              (SELECT COUNT(*) FROM reports WHERE status = 'open') AS open_reports
            """
        )
        data["recent_users"] = core.query_all(
            "SELECT id, name, username, profile_photo, created_at FROM users ORDER BY id DESC LIMIT 6"
        )

    elif tab == "reports":
        closed = request.args.get("status") == "closed"
        condition = "r.status <> 'open'" if closed else "r.status = 'open'"
        data["closed"] = closed
        data["reports"] = core.query_all(
            f"""
            SELECT r.*, rep.username AS reporter_username, tu.username AS target_username
            FROM reports r
            JOIN users rep ON rep.id = r.reporter_id
            LEFT JOIN users tu ON tu.id = r.target_user_id
            WHERE {condition}
            ORDER BY r.id DESC
            LIMIT 50
            """
        )

    elif tab == "users":
        data["users"] = core.query_all(
            """
            SELECT u.id, u.name, u.username, u.email, u.profile_photo, u.verified,
                   u.email_verified, u.banned_at, u.created_at,
                   (SELECT COUNT(*) FROM posts p WHERE p.author_id = u.id) AS posts
            FROM users u
            WHERE u.username ILIKE %s OR u.name ILIKE %s OR u.email ILIKE %s
            ORDER BY u.id DESC
            LIMIT %s OFFSET %s
            """,
            (pattern, pattern, pattern, PAGE_SIZE + 1, offset),
        )
        data["admins"] = admin_usernames()

    elif tab == "posts":
        data["posts"] = core.query_all(
            """
            SELECT p.id, p.content, p.media_filename, p.created_at, u.username, u.name
            FROM posts p JOIN users u ON u.id = p.author_id
            WHERE p.content ILIKE %s OR u.username ILIKE %s
            ORDER BY p.id DESC
            LIMIT %s OFFSET %s
            """,
            (pattern, pattern, PAGE_SIZE + 1, offset),
        )

    elif tab == "log":
        data["log"] = core.query_all(
            """
            SELECT l.*, a.username AS admin_username
            FROM admin_log l LEFT JOIN users a ON a.id = l.admin_id
            ORDER BY l.id DESC LIMIT 100
            """
        )

    for key in ("users", "posts"):
        if key in data:
            data["has_next"] = len(data[key]) > PAGE_SIZE
            data[key] = data[key][:PAGE_SIZE]

    return render_template("admin.html", tab=tab, q=query, page=page, **data)


def _target_user(user_id):
    target = core.query_one("SELECT id, username FROM users WHERE id = %s", (user_id,))
    if not target:
        abort(404)
    if target["username"].lower() in admin_usernames():
        flash("Contas administradoras não podem ser punidas.", "error")
        return None
    return target


def _ban(admin_user, target, reason):
    core.execute(
        "UPDATE users SET banned_at = CURRENT_TIMESTAMP, ban_reason = %s WHERE id = %s",
        ((reason or "")[:200], target["id"]),
    )
    log_admin(admin_user["id"], "baniu conta", "user", target["id"], "@" + target["username"] + " — " + (reason or ""))


@bp.post("/admin/user/<int:user_id>/ban")
@admin_required
def admin_ban(user_id):
    admin_user = core.get_current_user()
    target = _target_user(user_id)
    if target:
        _ban(admin_user, target, request.form.get("reason", ""))
        flash("@" + target["username"] + " foi banido.", "success")
    return redirect(safe_next("extras.admin", tab="users"))


@bp.post("/admin/user/<int:user_id>/unban")
@admin_required
def admin_unban(user_id):
    admin_user = core.get_current_user()
    target = core.query_one("SELECT id, username FROM users WHERE id = %s", (user_id,))
    if not target:
        abort(404)
    core.execute("UPDATE users SET banned_at = NULL, ban_reason = NULL WHERE id = %s", (user_id,))
    log_admin(admin_user["id"], "desbaniu conta", "user", user_id, "@" + target["username"])
    flash("@" + target["username"] + " foi desbanido.", "success")
    return redirect(safe_next("extras.admin", tab="users"))


@bp.post("/admin/user/<int:user_id>/verify")
@admin_required
def admin_verify(user_id):
    admin_user = core.get_current_user()
    target = core.query_one("SELECT id, username, verified FROM users WHERE id = %s", (user_id,))
    if not target:
        abort(404)
    new_value = 0 if target["verified"] else 1
    core.execute("UPDATE users SET verified = %s WHERE id = %s", (new_value, user_id))
    log_admin(admin_user["id"], "deu selo" if new_value else "tirou selo", "user", user_id, "@" + target["username"])
    flash(("Selo concedido a @" if new_value else "Selo removido de @") + target["username"] + ".", "success")
    return redirect(safe_next("extras.admin", tab="users"))


@bp.post("/admin/user/<int:user_id>/delete")
@admin_required
def admin_delete_user(user_id):
    admin_user = core.get_current_user()
    target = _target_user(user_id)
    if target:
        log_admin(admin_user["id"], "excluiu conta", "user", user_id, "@" + target["username"])
        delete_user_account(user_id)
        flash("Conta @" + target["username"] + " excluída.", "success")
    return redirect(safe_next("extras.admin", tab="users"))


@bp.post("/admin/report/<int:report_id>/resolve")
@admin_required
def admin_resolve_report(report_id):
    admin_user = core.get_current_user()
    rep = core.query_one("SELECT * FROM reports WHERE id = %s AND status = 'open'", (report_id,))
    if not rep:
        flash("Essa denúncia já foi resolvida.", "error")
        return redirect(url_for("extras.admin", tab="reports"))

    action = request.form.get("action", "dismiss")
    resolution = "dispensada"

    if action == "remove" and rep["target_type"] in ("post", "comment", "message"):
        table = {"post": "posts", "comment": "comments", "message": "messages"}[rep["target_type"]]
        core.execute(f"DELETE FROM {table} WHERE id = %s", (rep["target_id"],))
        log_admin(admin_user["id"], "removeu conteúdo denunciado", rep["target_type"], rep["target_id"], rep["snapshot"][:200])
        resolution = "conteúdo removido"

    elif action == "ban" and rep["target_user_id"]:
        target = _target_user(rep["target_user_id"])
        if target:
            _ban(admin_user, target, "Denúncia #" + str(report_id))
            resolution = "autor banido"
        else:
            return redirect(url_for("extras.admin", tab="reports"))

    core.execute(
        """
        UPDATE reports
        SET status = %s, resolution = %s, resolved_by = %s, resolved_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        ("dismissed" if resolution == "dispensada" else "resolved", resolution, admin_user["id"], report_id),
    )
    flash("Denúncia resolvida: " + resolution + ".", "success")
    return redirect(url_for("extras.admin", tab="reports"))


# ============================================================
# PÁGINAS LEGAIS
# ============================================================

@bp.get("/termos")
def terms():
    return render_template("legal.html", page="termos")


@bp.get("/privacidade")
def privacy():
    return render_template("legal.html", page="privacidade")
