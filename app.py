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
import sqlite3
import secrets
import uuid


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

app.config["SECRET_KEY"] = (
    "project-z-local-" + secrets.token_hex(16)
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

    connection = sqlite3.connect(
        DATABASE,
        timeout=30,
    )

    connection.row_factory = sqlite3.Row

    connection.execute(
        "PRAGMA foreign_keys = ON"
    )

    connection.execute(
        "PRAGMA journal_mode = WAL"
    )

    return connection


def query_one(sql, parameters=()):

    db = get_db()

    try:
        return db.execute(
            sql,
            parameters,
        ).fetchone()

    finally:
        db.close()


def query_all(sql, parameters=()):

    db = get_db()

    try:
        return db.execute(
            sql,
            parameters,
        ).fetchall()

    finally:
        db.close()


def execute(sql, parameters=()):

    db = get_db()

    try:

        cursor = db.execute(
            sql,
            parameters,
        )

        db.commit()

        return cursor.lastrowid

    finally:
        db.close()


# ============================================================
# DATABASE INITIALIZATION + MIGRATIONS
# ============================================================

def init_database():

    db = get_db()

    db.executescript(
        """

        CREATE TABLE IF NOT EXISTS users (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            name TEXT NOT NULL,

            username TEXT NOT NULL UNIQUE,

            birth_date TEXT NOT NULL,

            email TEXT NOT NULL UNIQUE,

            password_hash TEXT NOT NULL,

            profile_photo TEXT,

            profile_header TEXT,

            profile_header_type TEXT
                NOT NULL DEFAULT 'color',

            profile_header_value TEXT
                DEFAULT '#111111',

            bio TEXT
                DEFAULT '',

            status TEXT
                DEFAULT '',

            verified INTEGER
                NOT NULL DEFAULT 0,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP
        );


        CREATE TABLE IF NOT EXISTS follows (

            follower_id INTEGER NOT NULL,

            following_id INTEGER NOT NULL,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (
                follower_id,
                following_id
            ),

            FOREIGN KEY (
                follower_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE,

            FOREIGN KEY (
                following_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE,

            CHECK (
                follower_id != following_id
            )
        );


        CREATE TABLE IF NOT EXISTS profile_visits (

            visitor_id INTEGER NOT NULL,

            profile_id INTEGER NOT NULL,

            first_visit_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (
                visitor_id,
                profile_id
            ),

            FOREIGN KEY (
                visitor_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE,

            FOREIGN KEY (
                profile_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS posts (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            author_id INTEGER NOT NULL,

            content TEXT
                NOT NULL DEFAULT '',

            media_filename TEXT,

            media_type TEXT,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (
                author_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS post_likes (

            user_id INTEGER NOT NULL,

            post_id INTEGER NOT NULL,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (
                user_id,
                post_id
            ),

            FOREIGN KEY (
                user_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE,

            FOREIGN KEY (
                post_id
            )
            REFERENCES posts(id)
            ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS comments (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            post_id INTEGER NOT NULL,

            user_id INTEGER NOT NULL,

            content TEXT NOT NULL,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (
                post_id
            )
            REFERENCES posts(id)
            ON DELETE CASCADE,

            FOREIGN KEY (
                user_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS conversations (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP
        );


        CREATE TABLE IF NOT EXISTS conversation_members (

            conversation_id INTEGER NOT NULL,

            user_id INTEGER NOT NULL,

            joined_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (
                conversation_id,
                user_id
            ),

            FOREIGN KEY (
                conversation_id
            )
            REFERENCES conversations(id)
            ON DELETE CASCADE,

            FOREIGN KEY (
                user_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS messages (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            conversation_id INTEGER NOT NULL,

            sender_id INTEGER NOT NULL,

            content TEXT NOT NULL,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            read_at TEXT,

            FOREIGN KEY (
                conversation_id
            )
            REFERENCES conversations(id)
            ON DELETE CASCADE,

            FOREIGN KEY (
                sender_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE
        );


        CREATE TABLE IF NOT EXISTS notifications (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            user_id INTEGER NOT NULL,

            actor_id INTEGER,

            type TEXT NOT NULL,

            reference_id INTEGER,

            is_read INTEGER
                NOT NULL DEFAULT 0,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            FOREIGN KEY (
                user_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE,

            FOREIGN KEY (
                actor_id
            )
            REFERENCES users(id)
            ON DELETE SET NULL
        );


        CREATE TABLE IF NOT EXISTS communities (

            id INTEGER PRIMARY KEY AUTOINCREMENT,

            name TEXT NOT NULL UNIQUE,

            description TEXT
                DEFAULT '',

            icon TEXT,

            created_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP
        );


        CREATE TABLE IF NOT EXISTS community_members (

            community_id INTEGER NOT NULL,

            user_id INTEGER NOT NULL,

            joined_at TEXT
                NOT NULL DEFAULT CURRENT_TIMESTAMP,

            PRIMARY KEY (
                community_id,
                user_id
            ),

            FOREIGN KEY (
                community_id
            )
            REFERENCES communities(id)
            ON DELETE CASCADE,

            FOREIGN KEY (
                user_id
            )
            REFERENCES users(id)
            ON DELETE CASCADE
        );

        """
    )

    db.commit()


    # ========================================================
    # MIGRATION DA TABELA USERS
    # ========================================================

    existing_columns = {
        row["name"]
        for row in db.execute(
            "PRAGMA table_info(users)"
        ).fetchall()
    }


    migrations = {

        "name":
            "TEXT",

        "username":
            "TEXT",

        "birth_date":
            "TEXT",

        "email":
            "TEXT",

        "password_hash":
            "TEXT",

        "profile_photo":
            "TEXT",

        "profile_header":
            "TEXT",

        "profile_header_type":
            "TEXT NOT NULL DEFAULT 'color'",

        "profile_header_value":
            "TEXT DEFAULT '#111111'",

        "bio":
            "TEXT DEFAULT ''",

        "status":
            "TEXT DEFAULT ''",

        "verified":
            "INTEGER NOT NULL DEFAULT 0",

        "created_at":
            "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
    }


    for column, definition in migrations.items():

        if column not in existing_columns:

            db.execute(
                f"""
                ALTER TABLE users
                ADD COLUMN {column}
                {definition}
                """
            )


    db.commit()


    # ========================================================
    # MIGRAÇÕES DAS OUTRAS TABELAS
    # ========================================================

    # Se alguma versão anterior do projeto possuir tabelas
    # parcialmente criadas, o CREATE TABLE IF NOT EXISTS
    # acima preserva os dados e as estruturas existentes.
    #
    # As novas tabelas são criadas automaticamente.
    # ========================================================


    # ========================================================
    # REGRA DO SELO VERIFICADO
    # ========================================================

    db.execute(
        """
        UPDATE users

        SET verified =
            CASE
                WHEN username = 'eozffprivacy'
                THEN 1
                ELSE 0
            END
        """
    )

    db.commit()


    # ========================================================
    # COMUNIDADE PRINCIPAL
    # ========================================================

    community = db.execute(
        """
        SELECT id
        FROM communities
        WHERE name = ?
        """,
        ("Project Z",),
    ).fetchone()


    if not community:

        db.execute(
            """
            INSERT INTO communities
            (
                name,
                description,
                icon
            )
            VALUES (?, ?, ?)
            """,
            (
                "Project Z",
                "A comunidade principal da plataforma.",
                "Z",
            ),
        )


    db.commit()

    db.close()


# ============================================================
# AUTHENTICATION
# ============================================================

def get_current_user():

    user_id = session.get("user_id")

    if not user_id:
        return None

    return query_one(
        """
        SELECT *
        FROM users
        WHERE id = ?
        """,
        (user_id,),
    )


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

            WHERE user_id = ?

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

            WHERE cm.user_id = ?

              AND m.sender_id != ?

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

    if not valid_image(
        file.filename
    ):
        return None

    extension = (
        Path(
            secure_filename(
                file.filename
            )
        )
        .suffix
        .lower()
    )

    filename = (
        uuid.uuid4().hex
        + extension
    )

    file.save(
        directory / filename
    )

    return filename


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
        VALUES (?, ?, ?, ?)
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

        WHERE following_id = ?
        """,
        (user_id,),
    )

    return row["total"]


def following_count(user_id):

    row = query_one(
        """
        SELECT COUNT(*) AS total

        FROM follows

        WHERE follower_id = ?
        """,
        (user_id,),
    )

    return row["total"]


def visit_count(user_id):

    row = query_one(
        """
        SELECT COUNT(*) AS total

        FROM profile_visits

        WHERE profile_id = ?
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

        WHERE follower_id = ?

          AND following_id = ?
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


        if len(password) < 6:

            flash(
                "A senha precisa ter pelo menos 6 caracteres.",
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


        exists = query_one(
            """
            SELECT id

            FROM users

            WHERE username = ?

               OR email = ?
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

            VALUES (?, ?, ?, ?, ?, ?)
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
                INSERT OR IGNORE INTO
                community_members
                (
                    community_id,
                    user_id
                )
                VALUES (?, ?)
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

            WHERE username = ?

               OR email = ?
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


        execute(
            """
            UPDATE users

            SET verified =
                CASE
                    WHEN username =
                        'eozffprivacy'
                    THEN 1
                    ELSE 0
                END

            WHERE id = ?
            """,
            (user["id"],),
        )


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

                  AND pl2.user_id = ?
            ) AS liked

        FROM posts p

        JOIN users u
          ON u.id = p.author_id

        WHERE
            p.author_id = ?

            OR p.author_id IN (

                SELECT following_id

                FROM follows

                WHERE follower_id = ?
            )

        ORDER BY
            p.id DESC

        LIMIT 50
        """,
        (
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
                    WHERE f.follower_id = ?
                      AND f.following_id = u.id
                ) AS following

            FROM users u

            WHERE u.username LIKE ?
               OR u.name LIKE ?

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
                    WHERE f.follower_id = ?
                      AND f.following_id = u.id
                ) AS following

            FROM users u

            WHERE u.id != ?

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

            VALUES (?, ?, ?, ?)
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

        WHERE id = ?
        """,
        (post_id,),
    )


    if not post:

        abort(404)


    existing = query_one(
        """
        SELECT 1

        FROM post_likes

        WHERE user_id = ?

          AND post_id = ?
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

            WHERE user_id = ?

              AND post_id = ?
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

            VALUES (?, ?)
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

        WHERE post_id = ?
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

        WHERE username = ?
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

            WHERE follower_id = ?

              AND following_id = ?
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

            VALUES (?, ?)
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

        WHERE username = ?
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
            INSERT OR IGNORE INTO profile_visits
            (
                visitor_id,
                profile_id
            )

            VALUES (?, ?)
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

                  AND pl2.user_id = ?
            ) AS liked

        FROM posts p

        JOIN users u
          ON u.id = p.author_id

        WHERE p.author_id = ?

        ORDER BY p.id DESC

        LIMIT 50
        """,
        (
            current["id"],
            user["id"],
        ),
    )


    return render_template(
        "profile.html",
        user=user,
        posts=posts,
        followers=follower_count(
            user["id"]
        ),
        following=following_count(
            user["id"]
        ),
        visits=visit_count(
            user["id"]
        ),
        is_following=is_following(
            current["id"],
            user["id"]
        ),
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

        header = request.files.get(
            "profile_header"
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


        execute(
            """
            UPDATE users

            SET
                name = ?,
                status = ?,
                bio = ?,
                profile_photo = ?,
                profile_header = ?,
                profile_header_type = ?,
                profile_header_value = ?

            WHERE id = ?
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
            UPDATE users

            SET verified =
                CASE
                    WHEN username =
                        'eozffprivacy'
                    THEN 1
                    ELSE 0
                END
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

        WHERE n.user_id = ?

        ORDER BY n.id DESC

        LIMIT 100
        """,
        (user["id"],),
    )


    execute(
        """
        UPDATE notifications

        SET is_read = 1

        WHERE user_id = ?
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

        WHERE a.user_id = ?

          AND b.user_id = ?

        GROUP BY c.id

        HAVING COUNT(*) = 2

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
        """
    )


    execute(
        """
        INSERT INTO conversation_members
        (
            conversation_id,
            user_id
        )

        VALUES (?, ?)
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

        VALUES (?, ?)
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
        SELECT
            c.id,

            other.id AS other_id,

            other.name AS other_name,

            other.username
                AS other_username,

            other.profile_photo
                AS other_photo,

            other.verified
                AS other_verified,

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

        WHERE mine.user_id = ?

          AND other_member.user_id != ?

        ORDER BY
            last_message_at DESC,
            c.id DESC
        """,
        (
            user["id"],
            user["id"],
        ),
    )


    return render_template(
        "chat.html",
        conversations=conversations,
    )


# ============================================================
# CONVERSATION
# ============================================================

@app.route("/chat/<username>")
@login_required
def conversation(username):

    user = get_current_user()


    target = query_one(
        """
        SELECT *

        FROM users

        WHERE username = ?
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

        WHERE conversation_id = ?

          AND sender_id != ?

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

        JOIN users u
          ON u.id = m.sender_id

        WHERE m.conversation_id = ?

        ORDER BY m.id ASC

        LIMIT 200
        """,
        (conversation_id,),
    )


    return render_template(
        "conversation.html",
        target=target,
        messages=messages,
        conversation_id=conversation_id,
    )


# ============================================================
# SEND MESSAGE
# ============================================================

@app.post(
    "/api/chat/<int:conversation_id>/send"
)
@login_required
def send_message(
    conversation_id,
):

    user = get_current_user()


    member = query_one(
        """
        SELECT 1

        FROM conversation_members

        WHERE conversation_id = ?

          AND user_id = ?
        """,
        (
            conversation_id,
            user["id"],
        ),
    )


    if not member:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Acesso negado.",
            }
        ), 403


    content = (
        request.form
        .get("content", "")
        .strip()
    )


    if not content:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Mensagem vazia.",
            }
        ), 400


    if len(content) > 5000:

        return jsonify(
            {
                "ok": False,
                "error":
                    "Mensagem muito grande.",
            }
        ), 400


    message_id = execute(
        """
        INSERT INTO messages
        (
            conversation_id,
            sender_id,
            content
        )

        VALUES (?, ?, ?)
        """,
        (
            conversation_id,
            user["id"],
            content,
        ),
    )


    target = query_one(
        """
        SELECT user_id

        FROM conversation_members

        WHERE conversation_id = ?

          AND user_id != ?

        LIMIT 1
        """,
        (
            conversation_id,
            user["id"],
        ),
    )


    if target:

        create_notification(
            target["user_id"],
            user["id"],
            "message",
            conversation_id,
        )


    return jsonify(
        {
            "ok": True,
            "message_id": message_id,
            "content": content,
        }
    )


# ============================================================
# GET MESSAGES
# ============================================================

@app.get(
    "/api/chat/<int:conversation_id>/messages"
)
@login_required
def api_messages(
    conversation_id,
):

    user = get_current_user()


    member = query_one(
        """
        SELECT 1

        FROM conversation_members

        WHERE conversation_id = ?

          AND user_id = ?
        """,
        (
            conversation_id,
            user["id"],
        ),
    )


    if not member:

        return jsonify(
            {
                "ok": False,
            }
        ), 403


    after = request.args.get(
        "after",
        type=int,
    )


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

        JOIN users u
          ON u.id = m.sender_id

        WHERE m.conversation_id = ?

          AND m.id > ?

        ORDER BY m.id ASC

        LIMIT 100
        """,
        (
            conversation_id,
            after,
        ),
    )


    return jsonify(
        {
            "ok": True,

            "messages": [
                dict(message)
                for message in messages
            ],
        }
    )


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

        WHERE c.id = ?
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

        WHERE community_id = ?

          AND user_id = ?
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

        WHERE id = ?
        """,
        (community_id,),
    )


    if not community_data:

        abort(404)


    member = query_one(
        """
        SELECT 1

        FROM community_members

        WHERE community_id = ?

          AND user_id = ?
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

            WHERE community_id = ?

              AND user_id = ?
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

            VALUES (?, ?)
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

@app.route(
    "/uploads/avatars/<filename>"
)
def avatar_file(filename):

    return send_from_directory(
        AVATAR_DIR,
        filename,
    )


@app.route(
    "/uploads/headers/<filename>"
)
def header_file(filename):

    return send_from_directory(
        HEADER_DIR,
        filename,
    )


@app.route(
    "/uploads/posts/<filename>"
)
def post_file(filename):

    return send_from_directory(
        POST_DIR,
        filename,
    )


# ============================================================
# API STATUS
# ============================================================

@app.get("/api/status")
def api_status():

    return jsonify(
        {
            "project": "Project Z",
            "status": "online",
            "version": "1.0.0",

            "database": "sqlite",

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


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=8080,
        debug=True,
    )