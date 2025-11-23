import sqlite3
import datetime

def migrate_passwords():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    print("Migrating student passwords (DOB) to ddmmyyyy format...")
    
    c.execute("SELECT id, dob FROM students")
    students = c.fetchall()
    
    count = 0
    for student in students:
        sid, dob = student
        # Check if already in ddmmyyyy format (8 digits)
        if len(dob) == 8 and dob.isdigit():
            continue
            
        try:
            # Try parsing yyyy-mm-dd
            date_obj = datetime.datetime.strptime(dob, "%Y-%m-%d")
            new_dob = date_obj.strftime("%d%m%Y")
            
            c.execute("UPDATE students SET dob=? WHERE id=?", (new_dob, sid))
            count += 1
        except ValueError:
            print(f"Skipping invalid DOB format for student ID {sid}: {dob}")
            
    conn.commit()
    conn.close()
    print(f"Migration complete. Updated {count} records.")

if __name__ == "__main__":
    migrate_passwords()
