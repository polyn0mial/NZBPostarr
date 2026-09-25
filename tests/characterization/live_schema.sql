-- DDL of the live history database (data/history/usenet_uploads.db): tables, then indexes, then triggers.
-- sqlite_sequence is omitted: SQLite creates it for AUTOINCREMENT tables.
-- The schema dump cuts the uploads DDL off after in_nntp_seconds REAL. The ALTER TABLE lines after
-- the tables restore the columns the live indexes and the Upload model need, with the types
-- init_database() adds them with (itype has the TEXT type of the other legacy columns).
CREATE TABLE interface_stats ( id INTEGER NOT NULL, interface_name VARCHAR NOT NULL, upload_mbps FLOAT NOT NULL, download_mbps FLOAT NOT NULL, recorded_at DATETIME NOT NULL, PRIMARY KEY (id) );
CREATE TABLE job_history ( id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL UNIQUE, category TEXT NOT NULL, status TEXT NOT NULL, test_mode INTEGER DEFAULT 0, items_processed INTEGER DEFAULT 0, items_total INTEGER DEFAULT 0, items_skipped INTEGER DEFAULT 0, bytes_processed INTEGER DEFAULT 0, started_at DATETIME NOT NULL, completed_at DATETIME, duration_seconds REAL DEFAULT 0, error_message TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP , total_bytes INTEGER DEFAULT 0);
CREATE TABLE queue_items ( id INTEGER NOT NULL, path VARCHAR NOT NULL, category VARCHAR NOT NULL, itype VARCHAR NOT NULL, name VARCHAR NOT NULL, position INTEGER NOT NULL, added_at DATETIME NOT NULL, PRIMARY KEY (id), UNIQUE (path) );
CREATE TABLE server_failures ( id INTEGER PRIMARY KEY AUTOINCREMENT, server_host TEXT NOT NULL, server_name TEXT, failure_reason TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP );
CREATE TABLE server_speeds ( id INTEGER PRIMARY KEY AUTOINCREMENT, server_host TEXT NOT NULL, server_name TEXT, size_bytes INTEGER NOT NULL, duration_seconds REAL NOT NULL, bytes_per_second REAL, created_at DATETIME DEFAULT CURRENT_TIMESTAMP );
CREATE TABLE stage_timings ( id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT NOT NULL, size_bytes INTEGER NOT NULL, rar_seconds REAL DEFAULT 0, par2_seconds REAL DEFAULT 0, nntp_seconds REAL DEFAULT 0, api_seconds REAL DEFAULT 0, total_seconds REAL GENERATED ALWAYS AS ( rar_seconds + par2_seconds + nntp_seconds + api_seconds ) STORED, created_at DATETIME DEFAULT CURRENT_TIMESTAMP );
CREATE TABLE system_stats ( id INTEGER NOT NULL, cpu_percent FLOAT, memory_percent FLOAT, upload_mbps FLOAT, download_mbps FLOAT, total_sent_mb FLOAT NOT NULL, total_recv_mb FLOAT NOT NULL, connections INTEGER NOT NULL, disk_percent FLOAT NOT NULL, disk_free_gb FLOAT NOT NULL, disk_read_mbps FLOAT NOT NULL, disk_write_mbps FLOAT NOT NULL, errors_in INTEGER NOT NULL, errors_out INTEGER NOT NULL, drops_in INTEGER NOT NULL, drops_out INTEGER NOT NULL, swap_percent FLOAT NOT NULL, load_avg FLOAT NOT NULL, recorded_at DATETIME NOT NULL, PRIMARY KEY (id) );
CREATE TABLE system_stats_history (id INTEGER PRIMARY KEY AUTOINCREMENT, cpu_percent REAL, memory_percent REAL, upload_mbps REAL, download_mbps REAL, recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP);
CREATE TABLE upload_results ( id INTEGER NOT NULL, upload_id INTEGER NOT NULL, indexer_id VARCHAR NOT NULL, uploaded_at DATETIME NOT NULL, duration FLOAT, speed_bps FLOAT, server_name VARCHAR, status VARCHAR NOT NULL, error VARCHAR, PRIMARY KEY (id), FOREIGN KEY(upload_id) REFERENCES uploads (id) );
CREATE TABLE upload_stats ( id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT NOT NULL, size_bytes INTEGER NOT NULL, duration_seconds REAL NOT NULL, bytes_per_second REAL GENERATED ALWAYS AS (CASE WHEN duration_seconds > 0 THEN size_bytes / duration_seconds ELSE 0 END) STORED, created_at DATETIME DEFAULT CURRENT_TIMESTAMP );
CREATE TABLE upload_stats_avg ( id INTEGER PRIMARY KEY CHECK (id = 1), total_bytes INTEGER DEFAULT 0, total_seconds REAL DEFAULT 0, sample_count INTEGER DEFAULT 0, avg_bytes_per_second REAL GENERATED ALWAYS AS (CASE WHEN total_seconds > 0 THEN total_bytes / total_seconds ELSE 0 END) STORED, last_updated DATETIME DEFAULT CURRENT_TIMESTAMP );
CREATE TABLE "uploads" ( id INTEGER PRIMARY KEY AUTOINCREMENT, item_name TEXT NOT NULL UNIQUE, filesize TEXT, uploaded_at_in TEXT, uploaded_at_geek TEXT, uploaded_at_omg TEXT, created_at DATETIME DEFAULT CURRENT_TIMESTAMP, updated_at DATETIME DEFAULT CURRENT_TIMESTAMP , nzb_size_bytes INTEGER, nzb_newsgroups TEXT, nzb_enriched_at DATETIME, nzb_file_count INTEGER, nzb_segment_count INTEGER, nzb_poster TEXT, nzb_post_date DATETIME, nzb_path TEXT, in_duration REAL, in_speed_bps REAL, in_server_host TEXT, in_server_name TEXT, in_rar_seconds REAL, in_par2_seconds REAL, in_nntp_seconds REAL );
ALTER TABLE uploads ADD COLUMN itype TEXT;
ALTER TABLE uploads ADD COLUMN parsed_title VARCHAR;
ALTER TABLE uploads ADD COLUMN media_type VARCHAR;
ALTER TABLE uploads ADD COLUMN season_number INTEGER;
ALTER TABLE uploads ADD COLUMN episode_number INTEGER;
ALTER TABLE uploads ADD COLUMN episode_end_number INTEGER;
CREATE INDEX idx_created ON uploads(created_at);
CREATE INDEX idx_item ON uploads(item_name);
CREATE INDEX idx_job_history_started ON job_history(started_at DESC);
CREATE INDEX idx_job_history_status ON job_history(status);
CREATE INDEX idx_server_failures_created ON server_failures(created_at DESC);
CREATE INDEX idx_server_failures_host ON server_failures(server_host);
CREATE INDEX idx_stage_timings_created ON stage_timings(created_at);
CREATE INDEX idx_stats_created ON upload_stats(created_at);
CREATE INDEX idx_type ON uploads(itype);
CREATE INDEX ix_interface_stats_recorded_at ON interface_stats (recorded_at);
CREATE INDEX ix_job_history_started_at ON job_history(started_at);
CREATE INDEX ix_queue_items_position ON queue_items (position);
CREATE INDEX ix_system_stats_recorded_at ON system_stats (recorded_at);
CREATE INDEX ix_upload_results_indexer_id ON upload_results (indexer_id);
CREATE INDEX ix_upload_results_indexer_status_upload ON upload_results(indexer_id, status, upload_id);
CREATE INDEX ix_upload_results_status ON upload_results (status);
CREATE INDEX ix_upload_results_status_uploaded_at ON upload_results(status, uploaded_at);
CREATE INDEX ix_upload_results_upload_id ON upload_results(upload_id);
CREATE INDEX ix_upload_results_upload_id_indexer_id ON upload_results(upload_id, indexer_id);
CREATE INDEX ix_upload_results_uploaded_at ON upload_results(uploaded_at);
CREATE INDEX ix_uploads_created_at ON uploads(created_at);
CREATE INDEX ix_uploads_media_type ON uploads(media_type);
CREATE INDEX ix_uploads_parsed_title ON uploads(parsed_title);
CREATE INDEX ix_uploads_updated_at ON uploads(updated_at);
CREATE TRIGGER update_avg_on_insert AFTER INSERT ON upload_stats BEGIN UPDATE upload_stats_avg SET total_bytes = total_bytes + NEW.size_bytes, total_seconds = total_seconds + NEW.duration_seconds, sample_count = sample_count + 1, last_updated = CURRENT_TIMESTAMP WHERE id = 1; END;
