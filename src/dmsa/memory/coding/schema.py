"""Coding memory 数据库结构与旧 FTS 存储迁移."""

import sqlite3


def scope_token(user_id: str) -> str:
    """将不透明 scope 编码成单个无碰撞 FTS 词项."""
    return "s" + user_id.encode("utf-8").hex()

SCHEMA = """
CREATE TABLE IF NOT EXISTS requests(
              user_id TEXT NOT NULL, request_id TEXT NOT NULL, session_id TEXT NOT NULL,
              payload_hash TEXT NOT NULL, revision INTEGER NOT NULL,
              PRIMARY KEY(user_id, request_id));
            CREATE TABLE IF NOT EXISTS messages(
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
              session_id TEXT NOT NULL, ordinal INTEGER NOT NULL, role TEXT NOT NULL,
              timestamp INTEGER, content TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS messages_scope ON messages(user_id, session_id, ordinal);
            CREATE TABLE IF NOT EXISTS chunks(
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
              session_id TEXT NOT NULL, message_id TEXT NOT NULL, ordinal INTEGER NOT NULL,
              start INTEGER NOT NULL, end INTEGER NOT NULL, role TEXT NOT NULL,
              outcome TEXT NOT NULL, paths TEXT NOT NULL, symbols TEXT NOT NULL,
              text TEXT NOT NULL, scope TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS chunks_scope ON chunks(user_id, id);
            CREATE INDEX IF NOT EXISTS chunks_neighbors ON chunks(user_id, message_id, ordinal);
            CREATE VIRTUAL TABLE IF NOT EXISTS chunk_fts USING fts5(
              paths, symbols, text, scope, content='chunks', content_rowid='rowid',
              tokenize='unicode61');
            CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
              INSERT INTO chunk_fts(rowid, paths, symbols, text, scope)
                VALUES(new.rowid, new.paths, new.symbols, new.text, new.scope);
            END;
            CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
              INSERT INTO chunk_fts(chunk_fts, rowid, paths, symbols, text, scope)
                VALUES('delete', old.rowid, old.paths, old.symbols, old.text, old.scope);
            END;
            CREATE TABLE IF NOT EXISTS vectors(
              chunk_id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              fingerprint TEXT NOT NULL, dimension INTEGER NOT NULL, vector TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS vectors_scope ON vectors(user_id, fingerprint);
            CREATE TABLE IF NOT EXISTS exact_terms(
              user_id TEXT NOT NULL, chunk_id TEXT NOT NULL, term TEXT NOT NULL,
              PRIMARY KEY(user_id, chunk_id, term));
            CREATE INDEX IF NOT EXISTS exact_scope_term ON exact_terms(user_id, term, chunk_id);
            CREATE TABLE IF NOT EXISTS corpus(user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS scope_epochs(
              user_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
"""


def initialize(db: sqlite3.Connection) -> None:
    """原子迁移早期虚表存储, 保留稳定 ID、原文及所有历史请求."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='chunks'").fetchone()
    legacy = row is not None and "VIRTUAL TABLE" in row[0].upper()
    prefix = "ALTER TABLE chunks RENAME TO legacy_chunks;" if legacy else ""
    suffix = ""
    if legacy:
        suffix = (
            "INSERT INTO chunks SELECT *, 's'||lower(hex(user_id)) FROM legacy_chunks;"
            "DROP TABLE legacy_chunks;"
        )
    try:
        db.executescript("BEGIN IMMEDIATE;" + prefix + SCHEMA + suffix + "COMMIT;")
    except sqlite3.Error:
        db.rollback()
        raise
