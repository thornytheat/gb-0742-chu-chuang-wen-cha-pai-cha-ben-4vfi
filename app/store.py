import json
import sqlite3
from contextlib import closing, contextmanager

from app.errors import FieldError
from app.manifest import canonical


class ArchiveStore:
    def __init__(self, path):
        self.path = path

    @contextmanager
    def connect(self):
        with closing(sqlite3.connect(self.path, timeout=10)) as connection, connection:
            yield connection

    def setup(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("""
                CREATE TABLE IF NOT EXISTS shift_archives (
                    id TEXT PRIMARY KEY,
                    manifest TEXT NOT NULL,
                    evidence TEXT NOT NULL,
                    input TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS coverage_versions (
                    run_id TEXT PRIMARY KEY,
                    shift_key TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    cutoff TEXT,
                    supersedes TEXT,
                    comparison TEXT NOT NULL,
                    UNIQUE(shift_key, version)
                )
            """)
            self._backfill(connection)

    def _backfill(self, connection):
        """Register archives written before coverage versioning as version 1."""
        legacy = connection.execute("""
            SELECT id, manifest FROM shift_archives
            WHERE id NOT IN (SELECT run_id FROM coverage_versions) ORDER BY rowid
        """).fetchall()
        for run_id, raw in legacy:
            manifest = json.loads(raw)
            key = manifest.get("shift_key") or manifest["shift"]
            version = connection.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM coverage_versions WHERE shift_key = ?",
                                         (key,)).fetchone()[0]
            manifest.update(shift_key=key, version=version)
            connection.execute("UPDATE shift_archives SET manifest = ? WHERE id = ?", (canonical(manifest), run_id))
            connection.execute("INSERT INTO coverage_versions VALUES (?, ?, ?, ?, ?, ?)",
                               (run_id, key, version, None, None, canonical({})))

    def insert(self, run_id, build, evidence, source):
        """Store one archive and its coverage version in a single transaction.

        The run id is the receipt digest, so an identical input reuses the
        existing version instead of adding a new one. ``build`` receives the
        previous version of the same shift (read inside the transaction) and
        returns the manifest and comparison, so version numbering and the
        before/after diff cannot fork under concurrent same-shift submissions.
        """
        with closing(sqlite3.connect(self.path, timeout=10, isolation_level=None)) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute("SELECT manifest FROM shift_archives WHERE id = ?",
                                              (run_id,)).fetchone()
                if existing is not None:
                    connection.execute("COMMIT")
                    return json.loads(existing[0]), False
                previous = connection.execute("""
                    SELECT shift_archives.manifest, shift_archives.evidence, shift_archives.input
                    FROM coverage_versions JOIN shift_archives ON shift_archives.id = coverage_versions.run_id
                    WHERE coverage_versions.shift_key = ? ORDER BY coverage_versions.version DESC LIMIT 1
                """, (build.shift_key,)).fetchone()
                manifest, comparison = build(None if previous is None else
                                             tuple(json.loads(value) for value in previous))
                version = connection.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM coverage_versions WHERE shift_key = ?",
                    (manifest["shift_key"],)).fetchone()[0]
                stored = {**manifest, "version": version}
                connection.execute("INSERT INTO shift_archives VALUES (?, ?, ?, ?)",
                                   (stored["id"], canonical(stored), canonical(evidence), canonical(source)))
                connection.execute("INSERT INTO coverage_versions VALUES (?, ?, ?, ?, ?, ?)",
                                   (stored["id"], stored["shift_key"], version, stored.get("cutoff"),
                                    stored.get("supersedes"), canonical(comparison)))
                connection.execute("COMMIT")
                return stored, True
            except Exception:
                connection.execute("ROLLBACK")
                raise

    def versions(self, shift_key):
        with self.connect() as connection:
            rows = connection.execute("""
                SELECT shift_archives.manifest, shift_archives.evidence, coverage_versions.comparison
                FROM coverage_versions JOIN shift_archives ON shift_archives.id = coverage_versions.run_id
                WHERE coverage_versions.shift_key = ? ORDER BY coverage_versions.version
            """, (shift_key,)).fetchall()
        return [(json.loads(manifest), json.loads(evidence), json.loads(comparison))
                for manifest, evidence, comparison in rows]

    def comparison(self, run_id):
        with self.connect() as connection:
            row = connection.execute("SELECT comparison FROM coverage_versions WHERE run_id = ?", (run_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def fetch(self, run_id):
        with self.connect() as connection:
            record = connection.execute("SELECT manifest, evidence, input FROM shift_archives WHERE id = ?", (run_id,)).fetchone()
        if record is None:
            raise FieldError("UNKNOWN_RUN", "该归卷未登记", 404)
        return tuple(json.loads(value) for value in record)

    def history(self):
        with self.connect() as connection:
            return [json.loads(row[0]) for row in connection.execute("SELECT manifest FROM shift_archives ORDER BY rowid DESC")]
