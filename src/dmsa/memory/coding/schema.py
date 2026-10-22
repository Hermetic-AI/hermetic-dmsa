"""Coding memory 数据库结构."""

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
            CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5(
              id UNINDEXED, user_id UNINDEXED, request_id UNINDEXED,
              session_id UNINDEXED, message_id UNINDEXED, ordinal UNINDEXED,
              start UNINDEXED, end UNINDEXED, role UNINDEXED, outcome UNINDEXED,
              paths, symbols, text, tokenize='unicode61');
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
