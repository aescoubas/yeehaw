package store

const ddl = `
CREATE TABLE IF NOT EXISTS projects (
	id          INTEGER PRIMARY KEY AUTOINCREMENT,
	name        TEXT NOT NULL UNIQUE,
	root_path   TEXT NOT NULL,
	guidelines  TEXT NOT NULL DEFAULT '',
	git_remote  TEXT NOT NULL DEFAULT '',
	main_branch TEXT NOT NULL DEFAULT 'main',
	created_at  TEXT NOT NULL DEFAULT (datetime('now')),
	updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS roadmaps (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	project_id INTEGER NOT NULL REFERENCES projects(id),
	raw_text   TEXT NOT NULL,
	status     TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','invalid','approved','executing','completed')),
	created_at TEXT NOT NULL DEFAULT (datetime('now')),
	updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS roadmap_phases (
	id                INTEGER PRIMARY KEY AUTOINCREMENT,
	roadmap_id        INTEGER NOT NULL REFERENCES roadmaps(id),
	number            INTEGER NOT NULL,
	title             TEXT NOT NULL,
	verification_text TEXT NOT NULL DEFAULT '',
	status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','running','passed','failed')),
	created_at        TEXT NOT NULL DEFAULT (datetime('now')),
	updated_at        TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
	id            INTEGER PRIMARY KEY AUTOINCREMENT,
	phase_id      INTEGER NOT NULL REFERENCES roadmap_phases(id),
	number        TEXT NOT NULL,
	title         TEXT NOT NULL,
	description   TEXT NOT NULL DEFAULT '',
	status        TEXT NOT NULL DEFAULT 'queued' CHECK (status IN ('queued','dispatched','running','done','failed','timeout','skipped')),
	agent         TEXT NOT NULL DEFAULT '',
	branch        TEXT NOT NULL DEFAULT '',
	worktree_path TEXT NOT NULL DEFAULT '',
	signal_dir    TEXT NOT NULL DEFAULT '',
	attempt_count INTEGER NOT NULL DEFAULT 0,
	max_attempts  INTEGER NOT NULL DEFAULT 4,
	started_at    TEXT,
	finished_at   TEXT,
	created_at    TEXT NOT NULL DEFAULT (datetime('now')),
	updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS git_worktrees (
	id        INTEGER PRIMARY KEY AUTOINCREMENT,
	task_id   INTEGER NOT NULL REFERENCES tasks(id),
	path      TEXT NOT NULL,
	branch    TEXT NOT NULL,
	base_sha  TEXT NOT NULL DEFAULT '',
	state     TEXT NOT NULL DEFAULT 'active' CHECK (state IN ('active','merged','removed')),
	created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	project_id INTEGER REFERENCES projects(id),
	task_id    INTEGER REFERENCES tasks(id),
	kind       TEXT NOT NULL,
	message    TEXT NOT NULL,
	created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS alerts (
	id         INTEGER PRIMARY KEY AUTOINCREMENT,
	project_id INTEGER REFERENCES projects(id),
	task_id    INTEGER REFERENCES tasks(id),
	severity   TEXT NOT NULL DEFAULT 'info' CHECK (severity IN ('info','warn','error')),
	message    TEXT NOT NULL,
	status     TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open','resolved')),
	created_at TEXT NOT NULL DEFAULT (datetime('now')),
	resolved_at TEXT
);

CREATE TABLE IF NOT EXISTS scheduler_config (
	id              INTEGER PRIMARY KEY CHECK (id = 1),
	max_global      INTEGER NOT NULL DEFAULT 5,
	max_per_project INTEGER NOT NULL DEFAULT 3,
	timeout_minutes INTEGER NOT NULL DEFAULT 60,
	updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Seed scheduler config singleton
INSERT OR IGNORE INTO scheduler_config (id) VALUES (1);
`
