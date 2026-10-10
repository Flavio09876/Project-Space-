from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    flash,
    jsonify,
    abort,
    send_from_directory,
)
from werkzeug.security import (
    generate_password_hash,
    check_password_hash,
)
from werkzeug.utils import secure_filename

from pathlib import Path
from functools import wraps
import os
import psycopg
from psycopg.rows import dict_row
import secrets
import uuid
import cloudinary
import cloudinary.uploader


# ============================================================
# PROJECT Z
# ============================================================

BASE_DIR = Path(__file__).resolve().parent

DATABASE = BASE_DIR / "projectz.db"

UPLOAD_DIR = BASE_DIR / "uploads"
AVATAR_DIR = UPLOAD_DIR / "avatars"
HEADER_DIR = UPLOAD_DIR / "headers"
POST_DIR = UPLOAD_DIR / "posts"

for directory in (
    UPLOAD_DIR,
    AVATAR_DIR,
    HEADER_DIR,
    POST_DIR,
):
    directory.mkdir(parents=True, exist_ok=True)


app = Flask(__name__)

app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY") or (
    "project-z-local-" + secrets.token_hex(16)
)

cloudinary.config(
    cloudinary_url=os.environ.get("CLOUDINARY_URL"),
    secure=True,
)

app.config["MAX_CONTENT_LENGTH"] = 15 * 1024 * 1024


ALLOWED_IMAGES = {
    "png",
    "jpg",
    "jpeg",
    "webp",
    "gif",
}


# ============================================================
# DATABASE
# ============================================================


def get_db():
    """Abre uma conexão PostgreSQL com o Supabase."""
    database_url = os.environ.get("DATABASE_URL")

    if database_url:
        return psycopg.connect(database_url, row_factory=dict_row)

    password = os.environ.get("SUPABASE_DB_PASSWORD")
    if not password:
        raise RuntimeError(
            "Defina DATABASE_URL ou SUPABASE_DB_PASSWORD."
        )

    return psycopg.connect(
        host="aws-0-us-east-1.pooler.supabase.com",
        port=5432,
        dbname="postgres",
        user="postgres.daulnliocuulnfitqqsv",
        password=password,
        row_factory=dict_row,
    )


def query_one(sql, parameters=()):
    with get_db() as db:
        with db.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchone()


def query_all(sql, parameters=()):
    with get_db() as db:
        with db.cursor() as cursor:
            cursor.execute(sql, parameters)
            return cursor.fetchall()


def execute(sql, parameters=()):
    """Executa SQL e retorna o primeiro valor quando houver RETURNING."""
    with get_db() as db:
        with db.cursor() as cursor:
            cursor.execute(sql, parameters)
            result = None
            if cursor.description:
                row = cursor.fetchone()
                if row:
                    result = next(iter(row.values()))
        db.commit()
        return result


# ============================================================
# DATABASE INITIALIZATION + MIGRATIONS
# ============================================================


def init_database():
    """Cria a estrutura PostgreSQL necessária pelo Project Z."""
    statements = [
        """
        CREATE TABLE IF NOT EXISTS users (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            username TEXT NOT NULL UNIQUE,
            birth_date TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            password_hash TEXT NOT NULL,
            profile_photo TEXT,
            profile_header TEXT,
            profile_header_type TEXT NOT NULL DEFAULT 'color',
            profile_header_value TEXT DEFAULT '#111111',
            bio TEXT DEFAULT '',
            status TEXT DEFAULT '',
            verified INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS follows (
            follower_id BIGINT NOT NULL,
            following_id BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (follower_id, following_id),
            FOREIGN KEY (follower_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (following_id) REFERENCES users(id) ON DELETE CASCADE,
            CHECK (follower_id <> following_id)
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS profile_visits (
            visitor_id BIGINT NOT NULL,
            profile_id BIGINT NOT NULL,
            first_visit_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (visitor_id, profile_id),
            FOREIGN KEY (visitor_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (profile_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS posts (
            id BIGSERIAL PRIMARY KEY,
            author_id BIGINT NOT NULL,
            content TEXT NOT NULL DEFAULT '',
            media_filename TEXT,
            media_type TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (author_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS post_likes (
            user_id BIGINT NOT NULL,
            post_id BIGINT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, post_id),
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS comments (
            id BIGSERIAL PRIMARY KEY,
            post_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (post_id) REFERENCES posts(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS conversations (
            id BIGSERIAL PRIMARY KEY,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS conversation_members (
            conversation_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (conversation_id, user_id),
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS messages (
            id BIGSERIAL PRIMARY KEY,
            conversation_id BIGINT NOT NULL,
            sender_id BIGINT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            read_at TIMESTAMP,
            FOREIGN KEY (conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
            FOREIGN KEY (sender_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS notifications (
            id BIGSERIAL PRIMARY KEY,
            user_id BIGINT NOT NULL,
            actor_id BIGINT,
            type TEXT NOT NULL,
            reference_id BIGINT,
            is_read INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
            FOREIGN KEY (actor_id) REFERENCES users(id) ON DELETE SET NULL
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS communities (
            id BIGSERIAL PRIMARY KEY,
            name TEXT NOT NULL UNIQUE,
            description TEXT DEFAULT '',
            icon TEXT,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """,
        """
        CREATE TABLE IF NOT EXISTS community_members (
            community_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (community_id, user_id),
            FOREIGN KEY (community_id) REFERENCES communities(id) ON DELETE CASCADE,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """,
    ]

    with get_db() as db:
        with db.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)

            # Garante que o único selo permitido continue sendo eozffprivacy.
            cursor.execute("""
                UPDATE users
                SET verified = 1
                WHERE username = 'eozffprivacy'
            """)

            cursor.execute("""
                INSERT INTO communities (name, description, icon)
                VALUES (%s, %s, %s)
                ON CONFLICT (name) DO NOTHING
            """, (
                "Project Z",
                "A comunidade principal da plataforma.",
                "Z",
            ))
        db.commit()

    # Como os IDs existentes foram migrados explicitamente, alinhar as sequences.
    serial_tables = [
        "users", "posts", "comments", "conversations",
        "messages", "notifications", "communities"
    ]

    with get_db() as db:
        with db.cursor() as cursor:
            for table in serial_tables:
                cursor.execute(f"""
                    SELECT setval(
                        pg_get_serial_sequence('{table}', 'id'),
                        COALESCE((SELECT MAX(id) FROM {table}), 1),
                        true
                    )
                """)
        db.commit()


# AUTHENTICATION
# ============================================================

def get_current_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    row = query_one(
        """
        SELECT *
        FROM users
        WHERE id = %s
        """,
        (user_id,),
    )

    if row and row.get("banned_at"):
        session.clear()
        return None

    return row


def login_required(function):

    @wraps(function)
    def wrapper(*args, **kwargs):

        if not get_current_user():

            return redirect(
                url_for("login")
            )

        return function(
            *args,
            **kwargs
        )

    return wrapper


# ============================================================
# FILTROS DE TEMPLATE (datas, prévia de mensagem, avatar)
# ============================================================

from datetime import datetime, timedelta, timezone as _tz

# Fuso usado para exibir horários (Belém/Brasília = UTC-3, sem horário de verão).
# O banco guarda em UTC. Para mudar, altere o número de horas abaixo.
_PZ_TZ = _tz(timedelta(hours=-3))
_PZ_MONTHS = ["jan", "fev", "mar", "abr", "mai", "jun",
              "jul", "ago", "set", "out", "nov", "dez"]


def _pz_to_local(value):
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        moment = value
    else:
        text = str(value).strip()
        try:
            moment = datetime.fromisoformat(text)
        except ValueError:
            try:
                moment = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None

    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=_tz.utc)

    return moment.astimezone(_PZ_TZ)


@app.template_filter("pz_time")
def pz_time(value, mode="ago"):
    """mode: 'ago' (há 5 min), 'short' (11:04 / ontem / 09 out), 'full' (9 out 2026 · 11:04)."""

    moment = _pz_to_local(value)

    if moment is None:
        return ""

    now = datetime.now(_PZ_TZ)
    seconds = (now - moment).total_seconds()
    days = (now.date() - moment.date()).days
    clock = moment.strftime("%H:%M")
    day_month = f"{moment.day} {_PZ_MONTHS[moment.month - 1]}"

    if mode == "full":
        return f"{day_month} {moment.year} · {clock}"

    if mode == "clock":
        return clock

    if mode == "short":
        if days == 0:
            return clock
        if days == 1:
            return "ontem"
        if moment.year == now.year:
            return day_month
        return f"{day_month} {moment.year}"

    if seconds < 60:
        return "agora"
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 86400 and days == 0:
        return f"{int(seconds // 3600)} h"
    if days == 1:
        return "ontem"
    if moment.year == now.year:
        return day_month
    return f"{day_month} {moment.year}"


@app.template_filter("pz_preview")
def pz_preview(content):
    """Troca a marcação interna de mídia por algo legível na lista de conversas."""

    text = (content or "").strip()

    if text.startswith("[[PZ_MEDIA]]"):
        parts = text[len("[[PZ_MEDIA]]"):].split("[[PZ_TEXT]]", 1)
        caption = parts[1].strip() if len(parts) > 1 else ""
        return f"📷 {caption}" if caption else "📷 Foto"

    return text


@app.template_filter("pz_avatar")
def pz_avatar(photo):
    if not photo:
        return ""

    if str(photo).startswith("http"):
        return photo

    return url_for("avatar_file", filename=photo)


@app.context_processor
def inject_global_data():

    user = get_current_user()

    unread_notifications = 0

    unread_messages = 0

    if user:

        notification = query_one(
            """
            SELECT COUNT(*) AS total

            FROM notifications

            WHERE user_id = %s

              AND is_read = 0
            """,
            (user["id"],),
        )

        if notification:

            unread_notifications = (
                notification["total"]
            )


        message = query_one(
            """
            SELECT COUNT(*) AS total

            FROM messages m

            JOIN conversation_members cm
              ON cm.conversation_id =
                 m.conversation_id

            WHERE cm.user_id = %s

              AND m.sender_id != %s

              AND m.read_at IS NULL
            """,
            (
                user["id"],
                user["id"],
            ),
        )

        if message:

            unread_messages = (
                message["total"]
            )


    return {
        "current_user": user,

        "unread_notifications":
            unread_notifications,

        "unread_messages":
            unread_messages,
    }


# ============================================================
# HELPERS
# ============================================================

def username_clean(value):

    value = (
        value or ""
    ).strip().lower()

    return value.lstrip("@").replace(
        " ",
        "",
    )


def valid_image(filename):

    if not filename:
        return False

    extension = (
        Path(filename)
        .suffix
        .lower()
        .lstrip(".")
    )

    return extension in ALLOWED_IMAGES


def save_upload(file, directory):

    if not file or not file.filename:
        return None

    if not valid_image(file.filename):
        return None

    extension = (
        Path(
            secure_filename(file.filename)
        )
        .suffix
        .lower()
    )

    public_id = uuid.uuid4().hex

    if directory == AVATAR_DIR:
        folder = "project-z/avatars"
    elif directory == HEADER_DIR:
        folder = "project-z/headers"
    elif directory == POST_DIR:
        folder = "project-z/posts"
    else:
        folder = "project-z/misc"

    result = cloudinary.uploader.upload(
        file,
        folder=folder,
        public_id=public_id,
        resource_type="image",
    )

    return result["secure_url"]

def create_notification(
    user_id,
    actor_id,
    notification_type,
    reference_id=None,
):

    if (
        user_id is None
        or actor_id == user_id
    ):
        return

    execute(
        """
        INSERT INTO notifications
        (
            user_id,
            actor_id,
            type,
            reference_id
        )
        VALUES (%s, %s, %s, %s)
        """,
        (
            user_id,
            actor_id,
            notification_type,
            reference_id,
        ),
    )


def follower_count(user_id):

    row = query_one(
        """
        SELECT COUNT(*) AS total

        FROM follows

        WHERE following_id = %s
        """,
        (user_id,),
    )

    return row["total"]


def following_count(user_id):

    row = query_one(
        """
        SELECT COUNT(*) AS total

        FROM follows

        WHERE follower_id = %s
        """,
        (user_id,),
    )

    return row["total"]


def visit_count(user_id):

    row = query_one(
        """
        SELECT COUNT(*) AS total

        FROM profile_visits

        WHERE profile_id = %s
        """,
        (user_id,),
    )

    return row["total"]


def is_following(
    follower_id,
    following_id,
):

    row = query_one(
        """
        SELECT 1

        FROM follows

        WHERE follower_id = %s

          AND following_id = %s
        """,
        (
            follower_id,
            following_id,
        ),
    )

    return row is not None


# ============================================================
# WELCOME
# ============================================================

@app.route("/")
def welcome():

    if get_current_user():

        return redirect(
            url_for("home")
        )

    return render_template(
        "welcome.html"
    )


# ============================================================
# REGISTER
# ============================================================

@app.route(
    "/register",
    methods=["GET", "POST"],
)
def register():

    if get_current_user():

        return redirect(
            url_for("home")
        )


    if request.method == "POST":

        name = (
            request.form
            .get("name", "")
            .strip()
        )

        username = username_clean(
            request.form.get(
                "username",
                "",
            )
        )

        birth_date = (
            request.form
            .get("birth_date", "")
            .strip()
        )

        email = (
            request.form
            .get("email", "")
            .strip()
            .lower()
        )

        password = request.form.get(
            "password",
            "",
        )


        if not all(
            (
                name,
                username,
                birth_date,
                email,
                password,
            )
        ):

            flash(
                "Preencha todos os campos.",
                "error",
            )

            return render_template(
                "register.html"
            )


        if len(username) < 3:

            flash(
                "O nome de usuário precisa ter pelo menos 3 caracteres.",
                "error",
            )

            return render_template(
                "register.html"
            )


        if len(password) < 8:

            flash(
                "A senha precisa ter pelo menos 8 caracteres.",
                "error",
            )

            return render_template(
                "register.html"
            )


        if not all(
            character.isalnum()
            or character == "_"
            for character in username
        ):

            flash(
                "O @ pode conter apenas letras, números e _.",
                "error",
            )

            return render_template(
                "register.html"
            )


        problem = extras.validate_registration(username, email, birth_date)
        if problem:
            flash(problem, "error")
            return render_template("register.html")

        exists = query_one(
            """
            SELECT id

            FROM users

            WHERE username = %s

               OR email = %s
            """,
            (
                username,
                email,
            ),
        )


        if exists:

            flash(
                "Esse usuário ou e-mail já está cadastrado.",
                "error",
            )

            return render_template(
                "register.html"
            )


        verified = (
            1
            if username == "eozffprivacy"
            else 0
        )


        user_id = execute(
            """
            INSERT INTO users
            (
                name,
                username,
                birth_date,
                email,
                password_hash,
                verified
            )

            VALUES (%s, %s, %s, %s, %s, %s)
            RETURNING id
            """,
            (
                name,
                username,
                birth_date,
                email,
                generate_password_hash(
                    password
                ),
                verified,
            ),
        )


        extras.after_register(user_id, email)

        session.clear()

        session["user_id"] = user_id


        community = query_one(
            """
            SELECT id

            FROM communities

            WHERE name = 'Project Z'
            """
        )


        if community:

            execute(
                """
                INSERT INTO
                community_members
                (
                    community_id,
                    user_id
                )
                VALUES (%s, %s)
                ON CONFLICT (community_id, user_id) DO NOTHING
                """,
                (
                    community["id"],
                    user_id,
                ),
            )


        return redirect(
            url_for("home")
        )


    return render_template(
        "register.html"
    )


# ============================================================
# LOGIN
# ============================================================

@app.route(
    "/login",
    methods=["GET", "POST"],
)
def login():

    if get_current_user():

        return redirect(
            url_for("home")
        )


    if request.method == "POST":

        login_value = (
            request.form
            .get("login", "")
            .strip()
            .lower()
        )

        login_value = (
            login_value.lstrip("@")
        )

        password = request.form.get(
            "password",
            "",
        )


        user = query_one(
            """
            SELECT *

            FROM users

            WHERE username = %s

               OR email = %s
            """,
            (
                login_value,
                login_value,
            ),
        )


        if (
            not user
            or not check_password_hash(
                user["password_hash"],
                password,
            )
        ):

            flash(
                "Usuário/e-mail ou senha inválidos.",
                "error",
            )

            return render_template(
                "login.html"
            )


        if user.get("banned_at"):
            flash("Esta conta foi suspensa.", "error")
            return render_template("login.html")

        session.clear()

        session["user_id"] = user["id"]


        return redirect(
            url_for("home")
        )


    return render_template(
        "login.html"
    )


# ============================================================
# LOGOUT
# ============================================================

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("welcome")
    )


# ============================================================
# HOME
# ============================================================

@app.route("/home")
@login_required
def home():

    user = get_current_user()


    posts = query_all(
        """
        SELECT
            p.*,

            u.name,
            u.username,
            u.profile_photo,
            u.verified,

            (
                SELECT COUNT(*)

                FROM post_likes pl

                WHERE pl.post_id = p.id
            ) AS like_count,

            (
                SELECT COUNT(*)

                FROM comments c

                WHERE c.post_id = p.id
            ) AS comment_count,

            EXISTS (
                SELECT 1

                FROM post_likes pl2

                WHERE pl2.post_id = p.id

                  AND pl2.user_id = %s
            ) AS liked

        FROM posts p

        JOIN users u
          ON u.id = p.author_id

        WHERE
            (
                p.author_id = %s
                OR p.author_id IN (
                    SELECT following_id
                    FROM follows
                    WHERE follower_id = %s
                )
            )
            AND p.author_id NOT IN (
                SELECT blocked_id FROM blocks WHERE blocker_id = %s
            )
            AND p.author_id NOT IN (
                SELECT blocker_id FROM blocks WHERE blocked_id = %s
            )
        ORDER BY
            p.id DESC
        LIMIT 50
        """,
        (
            user["id"],
            user["id"],
            user["id"],
            user["id"],
            user["id"],
        ),
    )


    return render_template(
        "home.html",
        user=user,
        posts=posts,
    )


# ============================================================
# DISCOVER
# ============================================================
@app.route("/discover")
@login_required
def discover():

    search = (
        request.args
        .get("q", "")
        .strip()
    )

    if search:
        term = (
            "%"
            + search.lstrip("@")
            + "%"
        )

        users = query_all(
            """
            SELECT
                u.*,

                EXISTS (
                    SELECT 1
                    FROM follows f
                    WHERE f.follower_id = %s
                      AND f.following_id = u.id
                ) AS following

            FROM users u

            WHERE u.username LIKE %s
               OR u.name LIKE %s

            ORDER BY
                u.verified DESC,
                u.username ASC

            LIMIT 50
            """,
            (
                get_current_user()["id"],
                term,
                term,
            ),
        )

    else:
        users = query_all(
            """
            SELECT
                u.*,

                EXISTS (
                    SELECT 1
                    FROM follows f
                    WHERE f.follower_id = %s
                      AND f.following_id = u.id
                ) AS following

            FROM users u

            WHERE u.id != %s

            ORDER BY
                u.verified DESC,
                u.created_at DESC

            LIMIT 50
            """,
            (
                get_current_user()["id"],
                get_current_user()["id"],
            ),
        )

    communities = query_all(
        """
        SELECT
            c.*,

            (
                SELECT COUNT(*)
                FROM community_members cm
                WHERE cm.community_id = c.id
            ) AS member_count

        FROM communities c

        ORDER BY c.id DESC

        LIMIT 30
        """
    )

    return render_template(
        "discover.html",
        users=users,
        communities=communities,
        search=search,
    )


# ============================================================
# CREATE POST
# ============================================================

@app.route(
    "/create",
    methods=["GET", "POST"],
)
@login_required
def create_post():

    user = get_current_user()


    if request.method == "POST":

        content = (
            request.form
            .get("content", "")
            .strip()
        )

        media = request.files.get(
            "media"
        )


        if not content and not media:

            flash(
                "A publicação precisa ter texto ou mídia.",
                "error",
            )

            return render_template(
                "create_post.html"
            )


        filename = None

        media_type = None


        if media and media.filename:

            filename = save_upload(
                media,
                POST_DIR,
            )


            if not filename:

                flash(
                    "Imagem inválida.",
                    "error",
                )

                return render_template(
                    "create_post.html"
                )


            media_type = "image"


        execute(
            """
            INSERT INTO posts
            (
                author_id,
                content,
                media_filename,
                media_type
            )

            VALUES (%s, %s, %s, %s)
            """,
            (
                user["id"],
                content,
                filename,
                media_type,
            ),
        )


        return redirect(
            url_for("home")
        )


    return render_template(
        "create_post.html"
    )



# ============================================================
# DETALHES DA PUBLICAÇÃO E COMENTÁRIOS
# ============================================================
@app.route("/post/<int:post_id>", methods=["GET", "POST"])
@login_required
def post_detail(post_id):
    user = get_current_user()

    post = query_one("""
        SELECT p.*, u.name, u.username, u.profile_photo, u.verified,
               (SELECT COUNT(*) FROM post_likes pl
                WHERE pl.post_id = p.id) AS like_count,
               EXISTS (
                   SELECT 1 FROM post_likes pl2
                   WHERE pl2.post_id = p.id AND pl2.user_id = %s
               ) AS liked
        FROM posts p
        JOIN users u ON u.id = p.author_id
        WHERE p.id = %s
    """, (user["id"], post_id))

    if not post:
        return "Publicação não encontrada.", 404

    if request.method == "POST":
        content = request.form.get("content", "").strip()[:2000]
        if content:
            execute(
                "INSERT INTO comments (post_id, user_id, content) VALUES (%s, %s, %s)",
                (post_id, user["id"], content)
            )
            create_notification(post["author_id"], user["id"], "comment", post_id)
        return redirect(url_for("post_detail", post_id=post_id))

    comments = query_all("""
        SELECT c.*, u.name, u.username, u.profile_photo, u.verified
        FROM comments c
        JOIN users u ON u.id = c.user_id
        WHERE c.post_id = %s
          AND c.user_id NOT IN (SELECT blocked_id FROM blocks WHERE blocker_id = %s)
          AND c.user_id NOT IN (SELECT blocker_id FROM blocks WHERE blocked_id = %s)
        ORDER BY c.id ASC
    """, (post_id, user["id"], user["id"]))

    return render_template(
        "post_detail.html",
        user=user,
        post=post,
        comments=comments
    )


# ============================================================
# LIKE
# ============================================================

@app.post(
    "/api/posts/<int:post_id>/like"
)
@login_required
def toggle_like(post_id):

    user = get_current_user()


    post = query_one(
        """
        SELECT *

        FROM posts

        WHERE id = %s
        """,
        (post_id,),
    )


    if not post:

        abort(404)


    existing = query_one(
        """
        SELECT 1

        FROM post_likes

        WHERE user_id = %s

          AND post_id = %s
        """,
        (
            user["id"],
            post_id,
        ),
    )


    if existing:

        execute(
            """
            DELETE FROM post_likes

            WHERE user_id = %s

              AND post_id = %s
            """,
            (
                user["id"],
                post_id,
            ),
        )

        liked = False


    else:

        execute(
            """
            INSERT INTO post_likes
            (
                user_id,
                post_id
            )

            VALUES (%s, %s)
            """,
            (
                user["id"],
                post_id,
            ),
        )

        liked = True


        create_notification(
            post["author_id"],
            user["id"],
            "like",
            post_id,
        )


    count = query_one(
        """
        SELECT COUNT(*) AS total

        FROM post_likes

        WHERE post_id = %s
        """,
        (post_id,),
    )


    return jsonify(
        {
            "ok": True,
            "liked": liked,
            "count": count["total"],
        }
    )


# ============================================================
# FOLLOW / UNFOLLOW
# ============================================================

@app.post(
    "/api/users/<username>/follow"
)
@login_required
def toggle_follow(username):

    user = get_current_user()


    target = query_one(
        """
        SELECT *

        FROM users

        WHERE username = %s
        """,
        (
            username_clean(username),
        ),
    )


    if not target:

        abort(404)


    if target["id"] == user["id"]:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Não é possível seguir a própria conta.",
            }
        ), 400


    existing = is_following(
        user["id"],
        target["id"],
    )


    if existing:

        execute(
            """
            DELETE FROM follows

            WHERE follower_id = %s

              AND following_id = %s
            """,
            (
                user["id"],
                target["id"],
            ),
        )

        following = False


    else:

        execute(
            """
            INSERT INTO follows
            (
                follower_id,
                following_id
            )

            VALUES (%s, %s)
            """,
            (
                user["id"],
                target["id"],
            ),
        )

        following = True


        create_notification(
            target["id"],
            user["id"],
            "follow",
        )


    return jsonify(
        {
            "ok": True,
            "following": following,
            "followers":
                follower_count(
                    target["id"]
                ),
        }
    )


# ============================================================
# PROFILE
# ============================================================

@app.route("/u/<username>")
@login_required
def profile(username):

    current = get_current_user()


    user = query_one(
        """
        SELECT *

        FROM users

        WHERE username = %s
        """,
        (
            username_clean(username),
        ),
    )


    if not user:

        abort(404)


    # --------------------------------------------------------
    # VISITA ÚNICA E IRREVERSÍVEL PELA INTERFACE.
    #
    # A chave primária:
    #
    # visitor_id + profile_id
    #
    # impede que a mesma conta conte novamente.
    # --------------------------------------------------------

    if current["id"] != user["id"]:

        execute(
            """
            INSERT INTO profile_visits
            (
                visitor_id,
                profile_id
            )

            VALUES (%s, %s)
            ON CONFLICT (visitor_id, profile_id) DO NOTHING
            """,
            (
                current["id"],
                user["id"],
            ),
        )


    posts = query_all(
        """
        SELECT
            p.*,

            u.name,
            u.username,
            u.profile_photo,
            u.verified,

            (
                SELECT COUNT(*)

                FROM post_likes pl

                WHERE pl.post_id = p.id
            ) AS like_count,

            (
                SELECT COUNT(*)

                FROM comments c

                WHERE c.post_id = p.id
            ) AS comment_count,

            EXISTS (
                SELECT 1

                FROM post_likes pl2

                WHERE pl2.post_id = p.id

                  AND pl2.user_id = %s
            ) AS liked

        FROM posts p

        JOIN users u
          ON u.id = p.author_id

        WHERE p.author_id = %s

        ORDER BY p.id DESC

        LIMIT 50
        """,
        (
            current["id"],
            user["id"],
        ),
    )


    post_total = query_one(
        "SELECT COUNT(*) AS total FROM posts WHERE author_id = %s",
        (user["id"],),
    )

    return render_template(
        "profile.html",
        user=user,
        profile=user,
        posts=posts,
        posts_count=post_total["total"],
        followers_count=follower_count(user["id"]),
        following_count=following_count(user["id"]),
        visits_count=visit_count(user["id"]),
        is_following=is_following(current["id"], user["id"]),
    )



# ============================================================
# PROFILE CONNECTION LISTS
# ============================================================

@app.get("/u/<username>/<list_type>")
@login_required
def profile_connections(username, list_type):

    current = get_current_user()

    target = query_one(
        "SELECT * FROM users WHERE username = %s",
        (username_clean(username),),
    )

    if not target:
        abort(404)

    titles = {
        "followers": "Seguidores",
        "following": "Seguindo",
        "visits": "Visitas ao perfil",
    }

    if list_type not in titles:
        abort(404)

    if list_type == "followers":
        people = query_all(
            """
            SELECT
                u.id,
                u.name,
                u.username,
                u.profile_photo,
                u.verified,
                u.bio,
                f.created_at AS activity_at
            FROM follows f
            JOIN users u ON u.id = f.follower_id
            WHERE f.following_id = %s
            ORDER BY f.created_at DESC, u.username ASC
            """,
            (target["id"],),
        )

    elif list_type == "following":
        people = query_all(
            """
            SELECT
                u.id,
                u.name,
                u.username,
                u.profile_photo,
                u.verified,
                u.bio,
                f.created_at AS activity_at
            FROM follows f
            JOIN users u ON u.id = f.following_id
            WHERE f.follower_id = %s
            ORDER BY f.created_at DESC, u.username ASC
            """,
            (target["id"],),
        )

    else:
        # Somente o dono do perfil pode ver quem o visitou.
        if current["id"] != target["id"]:
            abort(403)

        people = query_all(
            """
            SELECT
                u.id,
                u.name,
                u.username,
                u.profile_photo,
                u.verified,
                u.bio,
                v.first_visit_at AS activity_at
            FROM profile_visits v
            JOIN users u ON u.id = v.visitor_id
            WHERE v.profile_id = %s
            ORDER BY v.first_visit_at DESC, u.username ASC
            """,
            (target["id"],),
        )

    return render_template(
        "user_list.html",
        profile=target,
        people=people,
        list_type=list_type,
        list_title=titles[list_type],
    )


# ============================================================
# EDIT PROFILE
# ============================================================

@app.route(
    "/settings/profile",
    methods=["GET", "POST"],
)
@login_required
def edit_profile():

    user = get_current_user()


    if request.method == "POST":

        name = (
            request.form
            .get("name", "")
            .strip()
        )

        status = (
            request.form
            .get("status", "")
            .strip()
        )

        bio = (
            request.form
            .get("bio", "")
            .strip()
        )

        header_type = (
            request.form
            .get(
                "header_type",
                "color",
            )
        )

        header_value = (
            request.form
            .get(
                "header_value",
                "#111111",
            )
            .strip()
        )


        if not name:

            flash(
                "O nome não pode ficar vazio.",
                "error",
            )

            return redirect(
                url_for(
                    "edit_profile"
                )
            )


        avatar = request.files.get(
            "profile_photo"
        )

        header = (
            request.files.get("header_image")
            or request.files.get("profile_header")
        )


        avatar_filename = (
            user["profile_photo"]
        )

        header_filename = (
            user["profile_header"]
        )


        if avatar and avatar.filename:

            saved = save_upload(
                avatar,
                AVATAR_DIR,
            )


            if not saved:

                flash(
                    "A foto de perfil não é válida.",
                    "error",
                )

                return redirect(
                    url_for(
                        "edit_profile"
                    )
                )


            avatar_filename = saved


        if header and header.filename:

            saved = save_upload(
                header,
                HEADER_DIR,
            )


            if not saved:

                flash(
                    "A imagem de capa não é válida.",
                    "error",
                )

                return redirect(
                    url_for(
                        "edit_profile"
                    )
                )


            header_filename = saved

            header_type = "image"


        if header_type not in (
            "color",
            "gradient",
            "image",
        ):

            header_type = "color"

        header_type, header_value = extras.clean_header(
            header_type,
            request.form.get("header_color"),
            request.form.get("header_gradient"),
            header_filename if (header and header.filename) else None,
            user["profile_header_type"],
            user["profile_header_value"],
        )


        execute(
            """
            UPDATE users

            SET
                name = %s,
                status = %s,
                bio = %s,
                profile_photo = %s,
                profile_header = %s,
                profile_header_type = %s,
                profile_header_value = %s

            WHERE id = %s
            """,
            (
                name,
                status[:120],
                bio[:1000],
                avatar_filename,
                header_filename,
                header_type,
                header_value,
                user["id"],
            ),
        )


        # Somente eozffprivacy possui verificação.

        execute(
            """
            UPDATE users SET verified = 1 WHERE username = 'eozffprivacy'
            """
        )


        flash(
            "Perfil atualizado.",
            "success",
        )


        return redirect(
            url_for(
                "profile",
                username=user["username"],
            )
        )


    return render_template(
        "edit_profile.html",
        user=user,
    )


# ============================================================
# NOTIFICATIONS
# ============================================================

@app.route("/notifications")
@login_required
def notifications():

    user = get_current_user()


    notifications = query_all(
        """
        SELECT
            n.*,

            u.name,
            u.username,
            u.profile_photo,
            u.verified

        FROM notifications n

        LEFT JOIN users u
          ON u.id = n.actor_id

        WHERE n.user_id = %s

        ORDER BY n.id DESC

        LIMIT 100
        """,
        (user["id"],),
    )


    execute(
        """
        UPDATE notifications

        SET is_read = 1

        WHERE user_id = %s
        """,
        (user["id"],),
    )


    return render_template(
        "notifications.html",
        notifications=notifications,
    )


# ============================================================
# CHAT HELPERS
# ============================================================

def get_or_create_conversation(
    user_a,
    user_b,
):

    existing = query_one(
        """
        SELECT c.id

        FROM conversations c

        JOIN conversation_members a
          ON a.conversation_id = c.id

        JOIN conversation_members b
          ON b.conversation_id = c.id

        WHERE a.user_id = %s

          AND b.user_id = %s

        GROUP BY c.id

        HAVING (
            SELECT COUNT(*)
            FROM conversation_members x
            WHERE x.conversation_id = c.id
        ) = 2

        ORDER BY (
            SELECT MAX(m.created_at)
            FROM messages m
            WHERE m.conversation_id = c.id
        ) DESC NULLS LAST, c.id DESC

        LIMIT 1
        """,
        (
            user_a,
            user_b,
        ),
    )


    if existing:

        return existing["id"]


    conversation_id = execute(
        """
        INSERT INTO conversations

        DEFAULT VALUES
        RETURNING id
        """
    )


    execute(
        """
        INSERT INTO conversation_members
        (
            conversation_id,
            user_id
        )

        VALUES (%s, %s)
        """,
        (
            conversation_id,
            user_a,
        ),
    )


    execute(
        """
        INSERT INTO conversation_members
        (
            conversation_id,
            user_id
        )

        VALUES (%s, %s)
        """,
        (
            conversation_id,
            user_b,
        ),
    )


    return conversation_id


# ============================================================
# CHAT LIST
# ============================================================

@app.route("/chat")
@login_required
def chat():

    user = get_current_user()


    conversations = query_all(
        """
        WITH conversation_rows AS (
            SELECT
                c.id,
                other.id AS other_id,
                other.name AS name,
                other.username AS username,
                other.profile_photo AS profile_photo,
                other.verified AS verified,
                (
                    SELECT m.content
                    FROM messages m
                    WHERE m.conversation_id = c.id
                    ORDER BY m.id DESC
                    LIMIT 1
                ) AS last_message,
                (
                    SELECT m.created_at
                    FROM messages m
                    WHERE m.conversation_id = c.id
                    ORDER BY m.id DESC
                    LIMIT 1
                ) AS last_message_at
            FROM conversations c
            JOIN conversation_members mine
              ON mine.conversation_id = c.id
            JOIN conversation_members other_member
              ON other_member.conversation_id = c.id
            JOIN users other
              ON other.id = other_member.user_id
            WHERE mine.user_id = %s
              AND other_member.user_id != %s
        ),
        unique_conversations AS (
            SELECT DISTINCT ON (other_id) *
            FROM conversation_rows
            WHERE last_message IS NOT NULL
            ORDER BY other_id, last_message_at DESC NULLS LAST, id DESC
        )
        SELECT *
        FROM unique_conversations
        ORDER BY last_message_at DESC NULLS LAST, id DESC
        """,
        (user["id"], user["id"]),
    )


    return render_template(
        "chat.html",
        conversations=conversations,
    )


# ============================================================
# CONVERSATION
# ============================================================

# ============================================================
# PWA (instalável no celular / base para gerar APK)
# ============================================================

@app.route("/sw.js")
def service_worker():
    response = send_from_directory(
        os.path.join(app.root_path, "static"),
        "sw.js",
        mimetype="application/javascript",
    )
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Service-Worker-Allowed"] = "/"
    return response


@app.route("/manifest.webmanifest")
def web_manifest():
    return send_from_directory(
        os.path.join(app.root_path, "static"),
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.route("/chat/<username>")
@login_required
def conversation(username):

    user = get_current_user()


    target = query_one(
        """
        SELECT *

        FROM users

        WHERE username = %s
        """,
        (
            username_clean(username),
        ),
    )


    if not target:

        abort(404)


    if target["id"] == user["id"]:

        return redirect(
            url_for("chat")
        )


    conversation_id = (
        get_or_create_conversation(
            user["id"],
            target["id"],
        )
    )


    execute(
        """
        UPDATE messages

        SET read_at =
            CURRENT_TIMESTAMP

        WHERE conversation_id = %s

          AND sender_id != %s

          AND read_at IS NULL
        """,
        (
            conversation_id,
            user["id"],
        ),
    )


    messages = query_all(
        """
        SELECT
            m.*,
            u.name,
            u.username,
            u.profile_photo,
            u.verified
        FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.conversation_id IN (
            SELECT mine.conversation_id
            FROM conversation_members mine
            JOIN conversation_members other_member
              ON other_member.conversation_id = mine.conversation_id
            WHERE mine.user_id = %s
              AND other_member.user_id = %s
            GROUP BY mine.conversation_id
            HAVING (
                SELECT COUNT(*)
                FROM conversation_members x
                WHERE x.conversation_id = mine.conversation_id
            ) = 2
        )
        ORDER BY m.id DESC
        LIMIT 200
        """,
        (user["id"], target["id"]),
    )
    messages = list(reversed(messages))


    return render_template(
        "conversation.html",
        other_user=target,
        conversation={"id": conversation_id},
        messages=messages,
        current_user=user,
    )


# ============================================================
# SEND MESSAGE
# ============================================================

@app.post(
    "/api/chat/<int:conversation_id>/send"
)
@login_required
def send_message(conversation_id):
    user = get_current_user()
    member = query_one(
        """
        SELECT 1 FROM conversation_members
        WHERE conversation_id = %s AND user_id = %s
        """,
        (conversation_id, user["id"]),
    )

    if not member:
        return jsonify({"ok": False, "error": "Acesso negado."}), 403

    payload = request.get_json(silent=True) if request.is_json else {}
    if not isinstance(payload, dict):
        payload = {}

    content = payload.get("content", request.form.get("content", ""))
    if not isinstance(content, str):
        content = ""
    content = content.strip()

    attachment = request.files.get("attachment")
    has_attachment = bool(attachment and attachment.filename)

    if not content and not has_attachment:
        return jsonify({
            "ok": False,
            "error": "Digite uma mensagem ou selecione uma imagem.",
        }), 400

    if len(content) > 5000:
        return jsonify({
            "ok": False,
            "error": "Mensagem muito grande. Limite: 5.000 caracteres.",
        }), 400

    if has_attachment:
        if not valid_image(attachment.filename):
            return jsonify({
                "ok": False,
                "error": "Formato inválido. Use JPG, PNG, WebP ou GIF.",
            }), 400
        try:
            image_url = save_upload(attachment, POST_DIR)
        except Exception:
            app.logger.exception("Erro no upload de imagem do chat")
            return jsonify({
                "ok": False,
                "error": "Falha ao enviar imagem. Tente novamente.",
            }), 502

        if not image_url:
            return jsonify({
                "ok": False,
                "error": "Não foi possível processar a imagem.",
            }), 400

        # Formato: [[PZ_MEDIA]]URL[[PZ_TEXT]]texto
        content = "[[PZ_MEDIA]]" + image_url + "[[PZ_TEXT]]" + content

    message_id = execute(
        """
        INSERT INTO messages (conversation_id, sender_id, content)
        VALUES (%s, %s, %s)
        RETURNING id
        """,
        (conversation_id, user["id"], content),
    )

    target = query_one(
        """
        SELECT user_id FROM conversation_members
        WHERE conversation_id = %s AND user_id != %s
        LIMIT 1
        """,
        (conversation_id, user["id"]),
    )

    if target:
        create_notification(
            target["user_id"], user["id"], "message", conversation_id
        )

    message = query_one(
        """
        SELECT id, sender_id, content, created_at
        FROM messages WHERE id = %s
        """,
        (message_id,),
    )

    if not message:
        return jsonify({
            "ok": False,
            "error": "A mensagem foi salva, mas não pôde ser carregada.",
        }), 500

    message = dict(message)
    message["created_at"] = str(message["created_at"])
    return jsonify({"ok": True, "message": message})


# GET MESSAGES
# ============================================================

@app.get(
    "/api/chat/<int:conversation_id>/messages"
)
@login_required
def api_messages(conversation_id):
    user = get_current_user()

    member = query_one(
        """
        SELECT 1
        FROM conversation_members
        WHERE conversation_id = %s AND user_id = %s
        """,
        (conversation_id, user["id"]),
    )

    if not member:
        return jsonify({"ok": False}), 403

    # Descobre o outro participante da conversa aberta.
    target = query_one(
        """
        SELECT user_id
        FROM conversation_members
        WHERE conversation_id = %s AND user_id != %s
        LIMIT 1
        """,
        (conversation_id, user["id"]),
    )

    if not target:
        return jsonify({"ok": True, "messages": []})

    after = request.args.get("after", default=0, type=int)
    if after is None:
        after = 0

    messages = query_all(
        """
        SELECT
            m.id,
            m.sender_id,
            m.content,
            m.created_at,
            u.username,
            u.name,
            u.profile_photo,
            u.verified
        FROM messages m
        JOIN users u ON u.id = m.sender_id
        WHERE m.id > %s
          AND m.conversation_id IN (
              SELECT mine.conversation_id
              FROM conversation_members mine
              JOIN conversation_members other_member
                ON other_member.conversation_id = mine.conversation_id
              WHERE mine.user_id = %s
                AND other_member.user_id = %s
              GROUP BY mine.conversation_id
              HAVING (
                  SELECT COUNT(*)
                  FROM conversation_members x
                  WHERE x.conversation_id = mine.conversation_id
              ) = 2
          )
        ORDER BY m.id ASC
        LIMIT 100
        """,
        (after, user["id"], target["user_id"]),
    )

    return jsonify({
        "ok": True,
        "messages": [
            {
                **dict(message),
                "created_at": str(message["created_at"]),
            }
            for message in messages
        ],
    })


# ============================================================
# COMMUNITIES
# ============================================================

@app.route(
    "/community/<int:community_id>"
)
@login_required
def community(community_id):

    community_data = query_one(
        """
        SELECT
            c.*,

            (
                SELECT COUNT(*)

                FROM community_members cm

                WHERE cm.community_id = c.id

            ) AS member_count

        FROM communities c

        WHERE c.id = %s
        """,
        (community_id,),
    )


    if not community_data:

        abort(404)


    user = get_current_user()


    member = query_one(
        """
        SELECT 1

        FROM community_members

        WHERE community_id = %s

          AND user_id = %s
        """,
        (
            community_id,
            user["id"],
        ),
    )


    return render_template(
        "community.html",
        community=community_data,
        is_member=bool(member),
    )


# ============================================================
# JOIN / LEAVE COMMUNITY
# ============================================================

@app.post(
    "/api/community/<int:community_id>/join"
)
@login_required
def toggle_community(
    community_id,
):

    user = get_current_user()


    community_data = query_one(
        """
        SELECT *

        FROM communities

        WHERE id = %s
        """,
        (community_id,),
    )


    if not community_data:

        abort(404)


    member = query_one(
        """
        SELECT 1

        FROM community_members

        WHERE community_id = %s

          AND user_id = %s
        """,
        (
            community_id,
            user["id"],
        ),
    )


    if member:

        execute(
            """
            DELETE FROM community_members

            WHERE community_id = %s

              AND user_id = %s
            """,
            (
                community_id,
                user["id"],
            ),
        )

        joined = False


    else:

        execute(
            """
            INSERT INTO community_members
            (
                community_id,
                user_id
            )

            VALUES (%s, %s)
            """,
            (
                community_id,
                user["id"],
            ),
        )

        joined = True


    return jsonify(
        {
            "ok": True,
            "joined": joined,
        }
    )


# ============================================================
# UPLOADS
# ============================================================

@app.route("/uploads/avatars/<path:filename>")
def avatar_file(filename):

    if filename.startswith(("http://", "https://")):
        return redirect(filename)

    return send_from_directory(
        AVATAR_DIR,
        filename,
    )
@app.route("/uploads/headers/<path:filename>")
def header_file(filename):

    if filename.startswith(("http://", "https://")):
        return redirect(filename)

    return send_from_directory(
        HEADER_DIR,
        filename,
    )
@app.route("/uploads/posts/<path:filename>")
def post_file(filename):

    if filename.startswith(("http://", "https://")):
        return redirect(filename)

    return send_from_directory(
        POST_DIR,
        filename,
    )
@app.get("/api/status")
def api_status():

    return jsonify(
        {
            "project": "Project Z",
            "status": "online",
            "version": "1.0.0",

            "database": "postgresql",

            "features": [
                "accounts",
                "profiles",
                "posts",
                "likes",
                "followers",
                "following",
                "unique_profile_visits",
                "notifications",
                "private_chat",
                "communities",
                "uploads",
            ],
        }
    )


# ============================================================
# ERROR HANDLERS
# ============================================================

@app.errorhandler(404)
def not_found(error):

    return render_template(
        "404.html"
    ), 404


@app.errorhandler(413)
def too_large(error):

    flash(
        "O arquivo enviado é muito grande.",
        "error",
    )

    return redirect(
        request.referrer
        or url_for("home")
    )


# ============================================================
# INITIALIZE
# ============================================================

init_database()

import extras

extras.install(app, globals())
extras.migrate()
import wallet
wallet.install(app, globals())
wallet.migrate()
import casino
casino.install(app, globals())
casino.migrate()


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8080,
        debug=True,
    )