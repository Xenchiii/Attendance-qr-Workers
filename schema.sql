CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL CHECK (role IN ('teacher', 'staff', 'admin')),
  employee_no TEXT NOT NULL UNIQUE,
  first_name TEXT NOT NULL,
  last_name TEXT NOT NULL,
  email TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'INACTIVE', 'SUSPENDED')),
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  last_login TEXT NULL
);

CREATE TABLE IF NOT EXISTS rooms (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  code TEXT NOT NULL UNIQUE,
  name TEXT NOT NULL,
  building TEXT NOT NULL,
  floor TEXT NOT NULL DEFAULT '',
  capacity INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'AVAILABLE' CHECK (status IN ('AVAILABLE', 'OCCUPIED', 'INACTIVE', 'MAINTENANCE'))
);

CREATE TABLE IF NOT EXISTS qr_sessions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  teacher_id INTEGER NOT NULL REFERENCES users(id),
  room_id INTEGER NOT NULL REFERENCES rooms(id),
  token_hash TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'USED', 'EXPIRED', 'CANCELLED'))
);
CREATE INDEX IF NOT EXISTS idx_qr_teacher ON qr_sessions (teacher_id, status);

CREATE TABLE IF NOT EXISTS attendance (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  teacher_id INTEGER NOT NULL REFERENCES users(id),
  room_id INTEGER NOT NULL REFERENCES rooms(id),
  qr_session_id INTEGER NULL REFERENCES qr_sessions(id),
  scanned_by INTEGER NULL REFERENCES users(id),
  time_in TEXT NOT NULL,
  time_out TEXT NULL,
  duration_minutes INTEGER NULL,
  status TEXT NOT NULL DEFAULT 'ACTIVE' CHECK (status IN ('ACTIVE', 'COMPLETED', 'INCOMPLETE', 'CANCELLED'))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_one_active_attendance ON attendance (teacher_id) WHERE status = 'ACTIVE';
CREATE INDEX IF NOT EXISTS idx_attendance_time ON attendance (time_in);
CREATE INDEX IF NOT EXISTS idx_attendance_room ON attendance (room_id);

CREATE TABLE IF NOT EXISTS auth_sessions (
  token_hash TEXT PRIMARY KEY,
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  expires_at TEXT NOT NULL
);

INSERT OR IGNORE INTO rooms (code, name, building, floor, capacity) VALUES
  ('RM-101', 'Room 101', 'Main Building', '1st Floor', 40),
  ('RM-102', 'Room 102', 'Main Building', '1st Floor', 40),
  ('LAB-1', 'Computer Lab 1', 'Tech Building', '2nd Floor', 35),
  ('LAB-2', 'Science Lab', 'Tech Building', '2nd Floor', 30),
  ('AVR', 'AV Room', 'Annex', 'Ground Floor', 80);
