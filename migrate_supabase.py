import os
import sqlite3
import psycopg

SUPABASE_URL = "postgresql://postgres.daulnliocuulnfitqqsv@aws-0-us-east-1.pooler.supabase.com:5432/postgres"
PASSWORD = os.environ.get("SUPABASE_DB_PASSWORD")

if not PASSWORD:
    raise SystemExit("SUPABASE_DB_PASSWORD não está definida.")

PG_URL = SUPABASE_URL.replace(
    "postgresql://postgres.",
    "postgresql://postgres.",
    1
)

PG_URL = PG_URL.replace(
    "@aws-0-us-east-1.pooler.supabase.com",
    f":{PASSWORD}@aws-0-us-east-1.pooler.supabase.com",
    1
)

SQLITE_DB = "projectz.db"

TABLES = [
    "users",
    "follows",
    "profile_visits",
    "posts",
    "post_likes",
    "comments",
    "conversations",
    "conversation_members",
    "messages",
    "notifications",
    "communities",
    "community_members",
]

SCHEMA = """
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
);

CREATE TABLE IF NOT EXISTS follows (
    follower_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    following_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (follower_id, following_id),
    CHECK (follower_id <> following_id)
);

CREATE TABLE IF NOT EXISTS profile_visits (
    visitor_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    profile_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    first_visit_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (visitor_id, profile_id)
);

CREATE TABLE IF NOT EXISTS posts (
    id BIGSERIAL PRIMARY KEY,
    author_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL DEFAULT '',
    media_filename TEXT,
    media_type TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS post_likes (
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, post_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id BIGSERIAL PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES posts(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversations (
    id BIGSERIAL PRIMARY KEY,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS conversation_members (
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (conversation_id, user_id)
);

CREATE TABLE IF NOT EXISTS messages (
    id BIGSERIAL PRIMARY KEY,
    conversation_id BIGINT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    sender_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    content TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS notifications (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    actor_id BIGINT REFERENCES users(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    reference_id BIGINT,
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS communities (
    id BIGSERIAL PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    description TEXT DEFAULT '',
    icon TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS community_members (
    community_id BIGINT NOT NULL REFERENCES communities(id) ON DELETE CASCADE,
    user_id BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    joined_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (community_id, user_id)
);
"""


def main():
    print("=== PROJECT Z → SUPABASE ===")

    print("[1/4] Abrindo SQLite...")
    sqlite = sqlite3.connect(SQLITE_DB)
    sqlite.row_factory = sqlite3.Row

    print("[2/4] Conectando ao Supabase...")
    pg = psycopg.connect(PG_URL)

    try:
        with pg.cursor() as cur:
            print("[3/4] Criando tabelas PostgreSQL...")
            cur.execute(SCHEMA)

        pg.commit()

        # Limpa somente as tabelas do Project Z no Supabase.
        # Não toca no SQLite.
        with pg.cursor() as cur:
            for table in reversed(TABLES):
                cur.execute(f"DELETE FROM {table}")

        pg.commit()

        print("[4/4] Copiando dados...")

        for table in TABLES:
            rows = sqlite.execute(
                f"SELECT * FROM {table}"
            ).fetchall()

            if not rows:
                print(f"  {table}: 0 registros")
                continue

            columns = rows[0].keys()
            column_list = ", ".join(columns)
            placeholders = ", ".join(
                ["%s"] * len(columns)
            )

            sql = f"""
                INSERT INTO {table}
                ({column_list})
                VALUES ({placeholders})
            """

            with pg.cursor() as cur:
                for row in rows:
                    cur.execute(
                        sql,
                        tuple(row[column] for column in columns)
                    )

            pg.commit()

            print(
                f"  {table}: {len(rows)} registros"
            )

        print()
        print("Ajustando sequências...")

        serial_tables = [
            "users",
            "posts",
            "comments",
            "conversations",
            "messages",
            "notifications",
            "communities",
        ]

        with pg.cursor() as cur:
            for table in serial_tables:
                cur.execute(
                    f"""
                    SELECT setval(
                        pg_get_serial_sequence(
                            '{table}',
                            'id'
                        ),
                        COALESCE(
                            (SELECT MAX(id) FROM {table}),
                            1
                        ),
                        true
                    )
                    """
                )

        pg.commit()

        print()
        print("=== MIGRAÇÃO CONCLUÍDA ===")

        with pg.cursor() as cur:
            for table in TABLES:
                cur.execute(
                    f"SELECT COUNT(*) FROM {table}"
                )
                count = cur.fetchone()[0]
                print(f"{table}: {count}")

    finally:
        sqlite.close()
        pg.close()


if __name__ == "__main__":
    main()
