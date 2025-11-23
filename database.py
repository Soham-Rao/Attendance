import sqlite3

def init_db():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS admin (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS students (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        usn TEXT UNIQUE,
        name TEXT,
        dob TEXT,
        class_id INTEGER,
        face_encoding BLOB
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS classes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_name TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS timetable (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_id INTEGER,
        subject TEXT,
        start_time TEXT,
        end_time TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS attendance (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        student_id INTEGER,
        subject TEXT,
        date TEXT,
        status TEXT,
        hour INTEGER,
        previous_hash TEXT,
        current_hash TEXT
    )''')

    # Migration: Add hash columns if they don't exist
    try:
        c.execute("ALTER TABLE attendance ADD COLUMN previous_hash TEXT")
    except sqlite3.OperationalError:
        pass  # Column likely already exists

    try:
        c.execute("ALTER TABLE attendance ADD COLUMN current_hash TEXT")
    except sqlite3.OperationalError:
        pass  # Column likely already exists

    c.execute('''CREATE TABLE IF NOT EXISTS subjects (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        class_id INTEGER,
        name TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS teachers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teacher_id TEXT UNIQUE,
        name TEXT,
        dob TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS teacher_assignments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        teacher_id TEXT,
        class_id INTEGER,
        subject TEXT
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS pending_attendance_changes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        attendance_id INTEGER,
        new_status TEXT,
        requested_by TEXT,
        timestamp TEXT,
        comment TEXT,
        FOREIGN KEY(attendance_id) REFERENCES attendance(id)
    )''')

    # Insert default admin if none exists
    c.execute("SELECT * FROM admin")
    if not c.fetchone():
        c.execute("INSERT INTO admin (username, password) VALUES (?,?)", ("admin", "admin123"))

    conn.commit()
    conn.close()

if __name__ == "__main__":
    init_db()
