import sqlite3

def tamper_data():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # 1. Get Class ID for 'CSE-C'
    c.execute("SELECT id FROM classes WHERE class_name = ?", ("CSE-C",))
    class_row = c.fetchone()
    
    if not class_row:
        print("Class 'CSE-C' not found.")
        conn.close()
        return

    class_id = class_row[0]
    print(f"Found Class 'CSE-C' with ID: {class_id}")
    
    # 2. Get all students in this class
    c.execute("SELECT id, name FROM students WHERE class_id = ?", (class_id,))
    students = c.fetchall()
    
    if not students:
        print("No students found in CSE-C.")
        conn.close()
        return
        
    student_ids = [s[0] for s in students]
    print(f"Found {len(students)} students in CSE-C.")
    
    if not student_ids:
        conn.close()
        return

    # 3. Update attendance records for these students to 'Present'
    # We are NOT updating the hashes, so this should break the chain.
    placeholders = ','.join(['?'] * len(student_ids))
    query = f"UPDATE attendance SET status = 'Present' WHERE student_id IN ({placeholders})"
    
    c.execute(query, student_ids)
    rows_affected = c.rowcount
    
    conn.commit()
    conn.close()
    
    print(f"Tampering complete. Updated {rows_affected} attendance records to 'Present'.")
    print("Run verify_integrity.py to check if this is detected.")

if __name__ == "__main__":
    tamper_data()
