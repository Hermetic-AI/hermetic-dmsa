"""Coding memory 数据库结构与旧 FTS 存储迁移."""

import secrets
import sqlite3


def scope_token(user_id: str) -> str:
    """将不透明 scope 编码成单个无碰撞 FTS 词项."""
    return "s" + user_id.encode("utf-8").hex()


# 条目统计表结构单独抽出: 只有 RSI 侧连接的库也需要这张表才能记账.
STATS_SCHEMA = """
            CREATE TABLE IF NOT EXISTS memory_stats(
              chunk_id TEXT NOT NULL, user_id TEXT NOT NULL,
              retrieved INTEGER NOT NULL DEFAULT 0,
              confirmed INTEGER NOT NULL DEFAULT 0,
              contradicted INTEGER NOT NULL DEFAULT 0,
              verified INTEGER NOT NULL DEFAULT 0,
              evicted INTEGER NOT NULL DEFAULT 0,
              first_revision INTEGER NOT NULL DEFAULT 0,
              created_at INTEGER NOT NULL, last_used INTEGER, updated_at INTEGER NOT NULL,
              PRIMARY KEY(user_id, chunk_id));
            CREATE INDEX IF NOT EXISTS memory_stats_user ON memory_stats(user_id, chunk_id);
"""


SCHEMA = (
    """
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
              tokenize='trigram');
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
              fingerprint TEXT NOT NULL, dimension INTEGER NOT NULL, vector TEXT NOT NULL,
              text_hash TEXT);
            CREATE INDEX IF NOT EXISTS vectors_scope ON vectors(user_id, fingerprint);
            CREATE INDEX IF NOT EXISTS vectors_content ON vectors(fingerprint, text_hash)
              WHERE text_hash IS NOT NULL;
            CREATE TABLE IF NOT EXISTS exact_terms(
              user_id TEXT NOT NULL, chunk_id TEXT NOT NULL, term TEXT NOT NULL,
              PRIMARY KEY(user_id, chunk_id, term));
            CREATE INDEX IF NOT EXISTS exact_scope_term ON exact_terms(user_id, term, chunk_id);
            CREATE TABLE IF NOT EXISTS code_anchors(
              user_id TEXT NOT NULL, chunk_id TEXT NOT NULL, term TEXT NOT NULL,
              path TEXT NOT NULL, symbol TEXT NOT NULL, kind TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS code_anchors_term ON code_anchors(user_id, term);
            CREATE INDEX IF NOT EXISTS code_anchors_chunk ON code_anchors(user_id, chunk_id);
            CREATE TABLE IF NOT EXISTS code_relations(
              user_id TEXT NOT NULL, chunk_id TEXT NOT NULL, rel_type TEXT NOT NULL,
              target TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS code_relations_target ON code_relations(user_id, target);
            CREATE INDEX IF NOT EXISTS code_relations_chunk ON code_relations(user_id, chunk_id);
            CREATE TABLE IF NOT EXISTS repo_snapshots(
              user_id TEXT NOT NULL, root TEXT NOT NULL, created_at TEXT NOT NULL,
              payload TEXT NOT NULL, PRIMARY KEY(user_id, root));
            CREATE TABLE IF NOT EXISTS corpus(user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS scope_epochs(
              user_id TEXT PRIMARY KEY, epoch INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS procedures(
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, request_id TEXT NOT NULL,
              title TEXT NOT NULL, steps TEXT NOT NULL, preconditions TEXT NOT NULL,
              verification TEXT NOT NULL, trigger_terms TEXT NOT NULL, paths TEXT NOT NULL,
              outcome TEXT NOT NULL, created_at INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS procedures_scope ON procedures(user_id, id);
            CREATE TABLE IF NOT EXISTS procedure_terms(
              user_id TEXT NOT NULL, procedure_id TEXT NOT NULL, term TEXT NOT NULL,
              PRIMARY KEY(user_id, procedure_id, term));
            CREATE INDEX IF NOT EXISTS procedure_terms_term ON procedure_terms(user_id, term);
            CREATE TABLE IF NOT EXISTS write_authors(
              user_id TEXT NOT NULL, chunk_id TEXT NOT NULL, author TEXT NOT NULL,
              PRIMARY KEY(user_id, chunk_id));
            CREATE INDEX IF NOT EXISTS write_authors_author ON write_authors(user_id, author);
            CREATE TABLE IF NOT EXISTS write_conflicts(
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL,
              chunk_id TEXT NOT NULL, rival_chunk_id TEXT NOT NULL,
              author TEXT NOT NULL, rival_author TEXT NOT NULL,
              similarity REAL NOT NULL, status TEXT NOT NULL, resolution TEXT NOT NULL,
              created_at INTEGER NOT NULL, resolved_at INTEGER);
            CREATE INDEX IF NOT EXISTS write_conflicts_scope ON write_conflicts(user_id, status);
            CREATE TABLE IF NOT EXISTS memory_consolidations(
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, chunk_id TEXT NOT NULL,
              group_key TEXT NOT NULL, rule TEXT NOT NULL, source_ids TEXT NOT NULL,
              paths TEXT NOT NULL, symbols TEXT NOT NULL, transferable INTEGER NOT NULL,
              created_at INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS memory_consolidations_scope
              ON memory_consolidations(user_id, group_key);
            CREATE TABLE IF NOT EXISTS memory_transfers(
              id TEXT PRIMARY KEY, user_id TEXT NOT NULL, source_scope TEXT NOT NULL,
              target_scope TEXT NOT NULL, chunk_id TEXT NOT NULL, source_ids TEXT NOT NULL,
              created_at INTEGER NOT NULL);
            CREATE INDEX IF NOT EXISTS memory_transfers_scope
              ON memory_transfers(user_id, target_scope);
"""
    + STATS_SCHEMA
    + """
            CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""
)


def _vector_hash_column(db: sqlite3.Connection) -> str:
    """为既有 vectors 表补 text_hash 列; 新库由建表语句直接给出."""
    columns = {str(row[1]) for row in db.execute("PRAGMA table_info(vectors)")}
    if not columns or "text_hash" in columns:
        return ""
    return "ALTER TABLE vectors ADD COLUMN text_hash TEXT;"


def _fts_uses_trigram(db: sqlite3.Connection) -> bool:
    """判断既有虚表是否已是 trigram 分词; 不存在时视为无需迁移."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='chunk_fts'").fetchone()
    return row is None or "trigram" in str(row[0]).lower()


def initialize(db: sqlite3.Connection) -> None:
    """原子迁移早期虚表存储及旧分词器, 保留稳定 ID、原文及所有历史请求."""
    row = db.execute("SELECT sql FROM sqlite_master WHERE name='chunks'").fetchone()
    legacy = row is not None and "VIRTUAL TABLE" in row[0].upper()
    prefix = "ALTER TABLE chunks RENAME TO legacy_chunks;" if legacy else ""
    prefix += _vector_hash_column(db)
    suffix = ""
    if legacy:
        suffix = (
            "INSERT INTO chunks SELECT *, 's'||lower(hex(user_id)) FROM legacy_chunks;"
            "DROP TABLE legacy_chunks;"
        )
    stale_fts = row is not None and not _fts_uses_trigram(db)
    if stale_fts:
        prefix += (
            "DROP TRIGGER IF EXISTS chunks_ai;DROP TRIGGER IF EXISTS chunks_ad;"
            "DROP TABLE chunk_fts;"
        )
        suffix += "INSERT INTO chunk_fts(chunk_fts) VALUES('rebuild');"
    try:
        db.executescript("BEGIN IMMEDIATE;" + prefix + SCHEMA + suffix + "COMMIT;")
        db.execute(
            "INSERT OR IGNORE INTO metadata(key, value) VALUES('corpus_id', ?)",
            (secrets.token_hex(32),),
        )
        db.execute(
            "INSERT OR IGNORE INTO scope_epochs(user_id, epoch) "
            "SELECT user_id, revision FROM corpus"
        )
        db.execute("INSERT OR IGNORE INTO metadata(key, value) VALUES('corpus_generation', '0')")
        db.commit()
    except sqlite3.Error:
        db.rollback()
        raise
