import os
import re
import shutil
from flask import Flask, render_template, request, redirect, session, make_response, url_for, flash
import sqlite3
import pickle
from io import StringIO
import csv
import datetime
from database import init_db
from face_recog import capture_face_encoding, run_live_attendance, get_class_encodings, process_frame
import base64
import numpy as np
import cv2
import json
from utils.hashing import calculate_hash, get_last_hash, recalculate_chain
from verify_integrity import verify_chain

app = Flask(__name__)
app.secret_key = "face_attendance_secret"
init_db()

# ---------- LOGIN ----------
# ── ID validation patterns ─────────────────────────────────────────────────────
STUDENT_ID_PATTERN = re.compile(r"^1bg\d{2}[A-Za-z]+?\d{3}$", re.IGNORECASE)
#   Explanation:
#   ^1bg          → literal “1bg” at the start
#   \d{2}         → exactly two digits
#   [A-Za-z]+?    → one or more letters (lazy, any length)
#   \d{3}$        → exactly three digits at the end

TEACHER_ID_PATTERN = re.compile(r"^t\d{3}$", re.IGNORECASE)
#   ^t            → literal “t” at the start
#   \d{3}$        → exactly three digits
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role")
        username = request.form.get("username")
        password = request.form.get("password")
        conn = sqlite3.connect('attendance.db')
        c = conn.cursor()

        # --------------------------------------------------------------
        # 1️⃣  Validate ID format before any DB lookup
        # --------------------------------------------------------------
        if role == "student":
            # Student IDs must match 1bg + 2 digits + letters + 3 digits
            if not STUDENT_ID_PATTERN.fullmatch(username):
                flash(
                    "⚠️ Student IDs must start with **1BG**, followed by 2 digits, "
                    "some letters, then 3 digits (e.g. 1BG23CS123). "
                    f"You entered: “{username}”. Please correct it.",
                    "warning"
                )
                conn.close()
                return render_template("login.html")
        elif role == "teacher":
            # Teacher IDs must match t + 3 digits
            if not TEACHER_ID_PATTERN.fullmatch(username):
                flash(
                    "⚠️ Teacher IDs must be in the form **T###** (e.g. T001). "
                    f"You entered: “{username}”. Please check the spelling.",
                    "warning"
                )
                conn.close()
                return render_template("login.html")
        # (admin IDs are not validated – they are fixed strings)

        # --------------------------------------------------------------
        # 2️⃣  Existing authentication logic (unchanged)
        # --------------------------------------------------------------
        if role == "admin":
            c.execute("SELECT * FROM admin WHERE username=? AND password=?", (username, password))
        elif role == "student":
            # Convert yyyy-mm-dd to ddmmyyyy if needed
            if "-" in password:
                try:
                    parts = password.split("-")
                    # yyyy-mm-dd -> ddmmyyyy
                    password = f"{parts[2]}{parts[1]}{parts[0]}"
                except:
                    pass
            c.execute("SELECT * FROM students WHERE usn=? AND dob=?", (username, password))
        elif role == "teacher":
            # Convert yyyy-mm-dd to ddmmyyyy if needed
            if "-" in password:
                try:
                    parts = password.split("-")
                    password = f"{parts[2]}{parts[1]}{parts[0]}"
                except:
                    pass
            c.execute("SELECT * FROM teachers WHERE teacher_id=? AND dob=?", (username, password))
        else:
            conn.close()
            flash("Invalid role selected", "danger")
            return render_template("login.html")

        user = c.fetchone()
        conn.close()

        if user:
            session["username"] = username
            session["role"] = role
            return redirect("/dashboard")
        else:
            flash("Invalid credentials. Please try again.", "danger")
            return render_template("login.html")

    return render_template("login.html")
# ---------- DASHBOARD ----------
@app.route("/dashboard")
def dashboard():
    if "username" not in session:
        return redirect("/")
    if session["role"] == "admin":
        return render_template("admin_dashboard.html")
    elif session["role"] == "teacher":
        return redirect("/teacher_dashboard")

    usn = session["username"]
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_id FROM students WHERE usn=?", (usn,))
    student_row = c.fetchone()
    
    if not student_row:
        conn.close()
        return "Student not found", 404
    
    student_id, class_id = student_row
    
    # Get all subjects with attendance data
    c.execute("""
        SELECT DISTINCT subject FROM attendance
        WHERE student_id=?
    """, (student_id,))
    subjects = [s[0] for s in c.fetchall()]
    
    # Calculate attendance percentage for each subject
    subject_stats = []
    total_present = 0
    total_classes = 0
    
    for subject in subjects:
        c.execute("""
            SELECT COUNT(*) as total,
                   SUM(CASE WHEN status='Present' THEN 1 ELSE 0 END) as present
            FROM attendance
            WHERE student_id=? AND subject=?
        """, (student_id, subject))
        result = c.fetchone()
        total = result[0] or 0
        present = result[1] or 0
        percentage = round((present / total * 100) if total > 0 else 0, 1)
        
        # Determine color based on percentage
        if percentage >= 75:
            status_color = "success"  # Green
        elif percentage >= 60:
            status_color = "warning"  # Yellow/Orange
        else:
            status_color = "danger"   # Red
        
        subject_stats.append({
            'name': subject,
            'present': present,
            'total': total,
            'percentage': percentage,
            'status_color': status_color
        })
        
        total_present += present
        total_classes += total
    
    # Calculate overall attendance
    overall_percentage = round((total_present / total_classes * 100) if total_classes > 0 else 0, 1)

        # Get list of attendance IDs that have pending change requests for this student
    c.execute("""
        SELECT DISTINCT p.attendance_id 
        FROM pending_attendance_changes p
        JOIN attendance a ON p.attendance_id = a.id
        WHERE a.student_id = (SELECT id FROM students WHERE usn = ?)
    """, (usn,))
    pending_ids = {row[0] for row in c.fetchall()}

    conn.close()
    return render_template("student_dashboard.html",
                           usn=usn,
                           subject_stats=subject_stats,
                           overall_percentage=overall_percentage,
                           total_present=total_present,
                           total_classes=total_classes,
                           pending_ids=pending_ids)

# ---------- CLASS MANAGEMENT ----------
@app.route("/classes")
def classes():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_name FROM classes")
    classes = c.fetchall()
    conn.close()
    return render_template("classes.html", classes=classes)

@app.route("/add_class", methods=["POST"])
def add_class():
    # 1. Get and clean the class name
    class_name = request.form["class_name"].strip()
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # 2. Check if class already exists (Case Insensitive)
    c.execute("SELECT id FROM classes WHERE LOWER(class_name)=LOWER(?)", (class_name,))
    existing_class = c.fetchone()
    
    if existing_class:
        conn.close()
        # 3. If duplicate, flash error and return
        flash(f"Class '{class_name}' already exists!", "error")
        return redirect("/classes")
    
    # 4. If unique, insert
    c.execute("INSERT INTO classes (class_name) VALUES (?)", (class_name,))
    conn.commit()
    conn.close()
    
    flash(f"Class '{class_name}' added successfully!", "success")
    return redirect("/classes")

@app.route("/delete_class/<int:class_id>", methods=["POST"])
def delete_class(class_id):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Optional: Check if class exists or has dependencies (students, subjects)
    # For now, we'll assume cascade delete or manual cleanup is desired, 
    # but SQLite foreign keys need to be enabled for cascade. 
    # Let's do a manual cleanup for safety if foreign keys aren't strict.
    
    # Delete students
    c.execute("DELETE FROM students WHERE class_id=?", (class_id,))
    # Delete subjects
    c.execute("DELETE FROM subjects WHERE class_id=?", (class_id,))
    # Delete teacher assignments
    c.execute("DELETE FROM teacher_assignments WHERE class_id=?", (class_id,))
    # Delete the class
    c.execute("DELETE FROM classes WHERE id=?", (class_id,))
    
    conn.commit()
    conn.close()
    
    flash("Class and associated data deleted successfully!", "success")
    return redirect("/classes")
    
# ---------- ADD STUDENT (dropdown) ----------
@app.route("/manage_students")
def manage_students():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_name FROM classes ORDER BY class_name")
    classes = c.fetchall()
    
    # Fetch all students with class names
    c.execute("""
        SELECT s.id, s.usn, s.name, s.dob, s.class_id, c.class_name 
        FROM students s
        JOIN classes c ON s.class_id = c.id
        ORDER BY c.class_name, s.usn
    """)
    students = c.fetchall()
    
    conn.close()
    return render_template("manage_students.html", classes=classes, students=students)

@app.route("/delete_student/<int:student_id>", methods=["POST"])
def delete_student(student_id):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Get student info to delete face image
    c.execute("SELECT usn FROM students WHERE id=?", (student_id,))
    student = c.fetchone()
    
    if student:
        usn = student[0]
        # Try to find and delete the image file
        # We don't store the extension, so we might need to check common ones or store it
        # For now, let's just delete the DB record. 
        # Ideally, we should clean up 'registered_faces' too.
        for ext in ['.jpg', '.jpeg', '.png']:
            path = os.path.join('registered_faces', f"{usn}{ext}")
            if os.path.exists(path):
                try:
                    os.remove(path)
                except:
                    pass
    
    c.execute("DELETE FROM students WHERE id=?", (student_id,))
    # Also delete attendance records?
    c.execute("DELETE FROM attendance WHERE student_id=?", (student_id,))
    
    conn.commit()
    conn.close()
    
    flash("Student deleted successfully!", "success")
    return redirect("/manage_students")

@app.route("/delete_assignment/<int:assignment_id>", methods=["POST"])
def delete_assignment(assignment_id):
    if session.get("role") != "admin":
        return "Unauthorized", 403
        
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("DELETE FROM teacher_assignments WHERE id=?", (assignment_id,))
    conn.commit()
    conn.close()
    
    flash("Assignment deleted successfully!", "success")
    return redirect("/admin/manage_teachers")

@app.route("/add_student", methods=["POST"])
def add_student():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    usn = request.form["usn"]
    name = request.form["name"]
    dob_raw = request.form["dob"]
    # Convert yyyy-mm-dd to ddmmyyyy
    try:
        parts = dob_raw.split("-")
        dob = f"{parts[2]}{parts[1]}{parts[0]}"
    except:
        dob = dob_raw # Fallback
    class_id = request.form["class_id"]
    
    # Ensure registered_faces directory exists
    if not os.path.exists('registered_faces'):
        os.makedirs('registered_faces')

    image_file = request.files.get("student_image")
    image_path_input = request.form.get("image_path")
    
    dest_path = None
    
    if image_file and image_file.filename:
        # Handle file upload
        ext = os.path.splitext(image_file.filename)[1]
        dest_path = os.path.join('registered_faces', f"{usn}{ext}")
        image_file.save(dest_path)
    elif image_path_input:
        # Handle manual path
        image_path = image_path_input.strip('"').strip("'")
        if os.path.exists(image_path):
            ext = os.path.splitext(image_path)[1]
            dest_path = os.path.join('registered_faces', f"{usn}{ext}")
            shutil.copyfile(image_path, dest_path)
        else:
            flash("Source image file not found", "danger")
            return redirect("/manage_students")
    else:
        flash("No image provided", "danger")
        return redirect("/add_student_form")

    if not dest_path:
        flash("Failed to save image", "danger")
        return redirect("/add_student_form")

    encoding = capture_face_encoding(dest_path)
    if encoding is None:
        # Clean up if no face found
        if os.path.exists(dest_path):
            os.remove(dest_path)
        flash("Face not found in image. Please use a clear photo.", "danger")
        return redirect("/add_student_form")
        
    face_blob = pickle.dumps(encoding)
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO students (usn, name, dob, class_id, face_encoding) VALUES (?,?,?,?,?)",
                  (usn, name, dob, class_id, face_blob))
        conn.commit()
        flash("Student added successfully!", "success")
    except sqlite3.IntegrityError:
        conn.close()
        flash("Student with this USN already exists.", "warning")
        return redirect("/add_student_form")
        
    conn.close()
    return redirect("/add_student_form")

# ---------- TEACHER MANAGEMENT ----------
@app.route("/add_teacher", methods=["POST"])
def add_teacher():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    
    teacher_id = request.form["teacher_id"]
    name = request.form["name"]
    dob_raw = request.form["dob"]
    # Convert yyyy-mm-dd to ddmmyyyy
    try:
        parts = dob_raw.split("-")
        dob = f"{parts[2]}{parts[1]}{parts[0]}"
    except:
        dob = dob_raw # Fallback
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    try:
        c.execute("INSERT INTO teachers (teacher_id, name, dob) VALUES (?,?,?)", (teacher_id, name, dob))
        conn.commit()
        flash("Teacher added successfully!", "success")
    except sqlite3.IntegrityError:
        flash("Teacher ID already exists.", "error")
        
    conn.close()
    return redirect("/admin/manage_teachers")

@app.route("/assign_teacher", methods=["POST"])
def assign_teacher():
    if session.get("role") != "admin":
        return "Unauthorized", 403
        
    teacher_id = request.form["teacher_id"] # This is the unique ID string
    class_id = request.form["class_id"]
    subject = request.form["subject"]
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Check if assignment already exists
    c.execute("SELECT id FROM teacher_assignments WHERE teacher_id=? AND class_id=? AND subject=?", 
              (teacher_id, class_id, subject))
    if c.fetchone():
        flash("This assignment already exists.", "warning")
    else:
        c.execute("INSERT INTO teacher_assignments (teacher_id, class_id, subject) VALUES (?,?,?)",
                  (teacher_id, class_id, subject))
        conn.commit()
        flash("Teacher assigned successfully!", "success")
        
    conn.close()
    return redirect("/admin/manage_teachers")

@app.route("/admin/manage_teachers")
def manage_teachers():
    if session.get("role") != "admin":
        return "Unauthorized", 403
        
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Get all teachers
    c.execute("SELECT * FROM teachers ORDER BY name")
    teachers = c.fetchall()
    
    # Get all classes
    c.execute("SELECT id, class_name FROM classes ORDER BY class_name")
    classes = c.fetchall()
    
    # Get all assignments with details
    c.execute("""
        SELECT ta.id, t.name, c.class_name, ta.subject 
        FROM teacher_assignments ta
        JOIN teachers t ON ta.teacher_id = t.teacher_id
        JOIN classes c ON ta.class_id = c.id
        ORDER BY t.name, c.class_name
    """)
    assignments = c.fetchall()
    
    conn.close()
    return render_template("manage_teachers.html", teachers=teachers, classes=classes, assignments=assignments)

# ---------- SUBJECT MANAGEMENT ----------
@app.route("/subjects")
def subjects():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_name FROM classes ORDER BY class_name")
    classes = c.fetchall()
    
    selected_class_id = request.args.get("class_id")
    subjects_list = []
    
    if selected_class_id:
        c.execute("SELECT id, name FROM subjects WHERE class_id=? ORDER BY name", (selected_class_id,))
        subjects_list = c.fetchall()
        
    conn.close()
    return render_template("subjects.html", classes=classes, subjects=subjects_list, 
                           selected_class_id=int(selected_class_id) if selected_class_id else None)

@app.route("/add_subject", methods=["POST"])
def add_subject():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    
    class_id = request.form["class_id"]
    name = request.form["name"].strip()  # .strip() removes accidental spaces
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # 1. Check if subject already exists for this specific class
    # We use LOWER() to ensure "Math" and "math" are treated as duplicates
    c.execute("SELECT id FROM subjects WHERE class_id=? AND LOWER(name)=LOWER(?)", (class_id, name))
    existing_subject = c.fetchone()
    
    if existing_subject:
        conn.close()
        # 2. If duplicate found, flash ERROR and return
        flash(f"Subject '{name}' already exists in this class!", "error")
        return redirect(f"/subjects?class_id={class_id}")
    
    # 3. If unique, proceed with INSERT
    c.execute("INSERT INTO subjects (class_id, name) VALUES (?,?)", (class_id, name))
    conn.commit()
    conn.close()
    
    flash(f"Subject '{name}' added successfully!", "success")
    return redirect(f"/subjects?class_id={class_id}")

@app.route("/delete_subject/<int:sid>", methods=["POST"])
def delete_subject(sid):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    class_id = request.form.get("class_id")
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("DELETE FROM subjects WHERE id=?", (sid,))
    conn.commit()
    conn.close()
    
    flash("Subject deleted successfully!", "success")
    return redirect(f"/subjects?class_id={class_id}")

# ---------- MARK ATTENDANCE (with dropdowns) ----------
# ---------- TEACHER DASHBOARD ----------
@app.route("/teacher_dashboard")
def teacher_dashboard():
    if session.get("role") != "teacher":
        return "Unauthorized", 403
        
    teacher_id = session["username"]
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Fetch assignments: (class_id, subject, class_name)
    c.execute("""
        SELECT ta.class_id, ta.subject, c.class_name
        FROM teacher_assignments ta
        JOIN classes c ON ta.class_id = c.id
        WHERE ta.teacher_id=?
    """, (teacher_id,))
    assignments = c.fetchall()
    conn.close()
    
    return render_template("teacher_dashboard.html", assignments=assignments)

# ---------- MARK ATTENDANCE (Teacher Only) ----------
@app.route("/mark_attendance", methods=["GET", "POST"])
def mark_attendance():
    role = session.get("role")
    if role not in ["admin", "teacher"]:
        return "Unauthorized: Only teachers/admins can mark attendance.", 403
        
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # 1. Fetch available classes/subjects based on role
    if role == "teacher":
        teacher_id = session["username"]
        # Fetch assigned classes
        c.execute("""
            SELECT DISTINCT c.id, c.class_name 
            FROM classes c
            JOIN teacher_assignments ta ON c.id = ta.class_id
            WHERE ta.teacher_id=?
        """, (teacher_id,))
        classes = c.fetchall()
        
        # If no classes assigned, show error or empty
        if not classes:
            conn.close()
            return "You have no assigned classes.", 403
            
    else: # Admin
        c.execute("SELECT id, class_name FROM classes")
        classes = c.fetchall()

    # 2. Handle Selection
    selected_class_id = request.args.get("class_id") or request.form.get("class_id")
    selected_subject = request.args.get("subject") or request.form.get("subject")
    
    # Default to first class if not selected (optional, but helps UX)
    # Actually, let's not default, let user select.
    
    subjects = []
    if selected_class_id:
        try:
            cid = int(selected_class_id)
            # Fetch subjects
            if role == "teacher":
                teacher_id = session["username"]
                c.execute("""
                    SELECT subject FROM teacher_assignments 
                    WHERE teacher_id=? AND class_id=?
                    ORDER BY subject
                """, (teacher_id, cid))
            else:
                c.execute("SELECT name FROM subjects WHERE class_id=? ORDER BY name", (cid,))
            
            subjects = [row[0] for row in c.fetchall()]
        except ValueError:
            pass

    if request.method == "POST":
        # ... (POST logic remains mostly same, just ensure we have valid data)
        if not selected_class_id or not selected_subject:
             flash("Please select both class and subject.", "error")
             return redirect(request.url)

        class_id = int(selected_class_id)
        subject = selected_subject
        hour = request.form["hour"]
        
        # Get total student count for the class
        c.execute("SELECT COUNT(*) FROM students WHERE class_id=?", (class_id,))
        total_students = c.fetchone()[0]

        # Fetch existing present students for this session
        date_today = datetime.date.today().strftime("%Y-%m-%d")
        c.execute("""
            SELECT attendance.student_id, students.name
            FROM attendance 
            JOIN students ON attendance.student_id = students.id 
            WHERE students.class_id=? AND attendance.subject=? AND attendance.date=? AND attendance.hour=? AND attendance.status='Present'
        """, (class_id, subject, date_today, hour))
        existing_present_data = [{"id": row[0], "name": row[1]} for row in c.fetchall()]

        conn.close()
        
        return render_template("live_attendance.html", 
                               class_id=class_id, 
                               subject=subject, 
                               hour=hour, 
                               total_students=total_students,
                               existing_present_data=existing_present_data)

    conn.close()
    return render_template("mark_attendance.html",
                           classes=classes,
                           selected_class_id=int(selected_class_id) if selected_class_id else None,
                           subjects=subjects,
                           selected_subject=selected_subject)

# ---------- ADMIN ATTENDANCE ----------
@app.route("/admin/attendance", methods=["GET", "POST"])
def admin_attendance():
    role = session.get("role")
    if role not in ["admin", "teacher"]:
        return "Unauthorized", 403

    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()

    # ------------------- POST (Update/Request Changes) -------------------
    if request.method == "POST":
        # If teacher, we need a reason
        reason = request.form.get("reason")
        if role == "teacher" and not reason:
            flash("Reason is required for change requests.", "error")
            return redirect(request.url)

        changes_made = False
        use_same_comment = request.form.get("use_same_comment") is not None
        success_count = 0
        skipped_student_pending = 0
        skipped_teacher_pending = 0
        
        for key, value in request.form.items():
            if key.startswith("status_"):
                att_id = key.split("_")[1]
                
                # Fetch current status to see if it actually changed
                c.execute("SELECT status FROM attendance WHERE id=?", (att_id,))
                current_status = c.fetchone()[0]
                
                if current_status != value:
                    if role == "admin":
                        # Admin updates directly
                        c.execute("UPDATE attendance SET status=? WHERE id=?", (value, att_id))
                        # Auto-rehash immediately for Admin
                        recalculate_chain(c, att_id)
                        changes_made = True
                        success_count += 1
                    elif role == "teacher":
                        # Check if there's already a pending request for this attendance record
                        c.execute("SELECT id, request_role FROM pending_attendance_changes WHERE attendance_id=?", (att_id,))
                        existing_request = c.fetchone()
                        
                        if existing_request:
                            if existing_request[1] == "student":
                                skipped_student_pending += 1
                            else:
                                skipped_teacher_pending += 1
                            continue  # Skip this record
                        
                        # Teacher requests change
                        teacher_id = session["username"]
                        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                        
                        # Build final comment with individual comment if provided
                        if use_same_comment:
                            final_comment = reason
                        else:
                            individual_comment = request.form.get(f"individual_{att_id}", "").strip()
                            if individual_comment:
                                final_comment = f"{reason} | {individual_comment}"
                            else:
                                final_comment = reason
                        
                        c.execute("""
                            INSERT INTO pending_attendance_changes 
                            (attendance_id, new_status, requested_by, timestamp, comment, request_role)
                            VALUES (?, ?, ?, ?, ?, 'teacher')
                        """, (att_id, value, teacher_id, timestamp, final_comment))
                        changes_made = True
                        success_count += 1

        conn.commit()
        conn.close()

        # Flash success message
        if changes_made:
            if role == "admin":
                flash(f"Updated {success_count} attendance records successfully!", "success")
            else:
                msg = f"Change request submitted for {success_count} records."
                if skipped_student_pending > 0:
                    msg += f" Skipped {skipped_student_pending} (already pending from student)."
                if skipped_teacher_pending > 0:
                    msg += f" Skipped {skipped_teacher_pending} (already pending from teacher)."
                flash(msg, "success")
        else:
            if skipped_student_pending > 0 or skipped_teacher_pending > 0:
                 msg = "No new changes submitted."
                 if skipped_student_pending > 0:
                    msg += f" Skipped {skipped_student_pending} records (already pending from student)."
                 if skipped_teacher_pending > 0:
                    msg += f" Skipped {skipped_teacher_pending} records (already pending from teacher)."
                 flash(msg, "warning")
            else:
                flash("No changes detected.", "info")

        # Preserve filters on redirect
        params = []
        for p in ["class_id", "subject", "hour", "date", "student_id"]:
            v = request.args.get(p)
            if v:
                params.append(f"{p}={v}")
        qs = "&".join(params)

        return redirect(f"/admin/attendance?{qs}")

    # ------------------- GET (Normal Page Load) -------------------
    class_id = request.args.get("class_id")
    subject = request.args.get("subject")
    hour = request.args.get("hour")
    date = request.args.get("date", datetime.date.today().strftime("%Y-%m-%d"))
    student_id = request.args.get("student_id")

    try:
        class_id_int = int(class_id) if class_id else None
    except:
        class_id_int = None

    # Fetch class list
    # If teacher, only show assigned classes
    if role == "teacher":
        teacher_id = session["username"]
        c.execute("""
            SELECT DISTINCT c.id, c.class_name 
            FROM classes c
            JOIN teacher_assignments ta ON c.id = ta.class_id
            WHERE ta.teacher_id=?
        """, (teacher_id,))
    else:
        c.execute("SELECT id, class_name FROM classes")
    
    classes = c.fetchall()

    subjects = []
    students_list = []
    attendance_records = []

    if class_id_int:
        # Subjects list
        # If teacher, only show assigned subjects for this class
        if role == "teacher":
            teacher_id = session["username"]
            c.execute("""
                SELECT subject FROM teacher_assignments 
                WHERE teacher_id=? AND class_id=?
                ORDER BY subject
            """, (teacher_id, class_id_int))
        else:
            c.execute("SELECT name FROM subjects WHERE class_id=? ORDER BY name", (class_id_int,))
            
        subjects = [s[0] for s in c.fetchall()]

        # Students list
        c.execute("SELECT id, usn, name FROM students WHERE class_id=? ORDER BY name", (class_id_int,))
        students_list = c.fetchall()

        # Base attendance query
        if subject:
            query = """SELECT attendance.id, students.usn, students.name, attendance.subject, attendance.hour,
                              attendance.date, attendance.status
                       FROM attendance
                       JOIN students ON attendance.student_id = students.id
                       WHERE students.class_id=? AND attendance.subject=? AND attendance.date=?"""
            params = [class_id_int, subject, date]

            if hour:
                query += " AND attendance.hour=?"
                params.append(hour)
        else:
            query = """SELECT attendance.id, students.usn, students.name, attendance.subject, attendance.hour,
                              attendance.date, attendance.status
                       FROM attendance
                       JOIN students ON attendance.student_id = students.id
                       WHERE students.class_id=? AND attendance.date=?"""
            params = [class_id_int, date]

            if hour:
                query += " AND attendance.hour=?"
                params.append(hour)

        if student_id:
            query += " AND students.id=?"
            params.append(student_id)

        query += " ORDER BY students.usn, attendance.subject, attendance.hour"
        c.execute(query, params)

        attendance_records = c.fetchall()
        
        # Get list of attendance IDs that have pending changes
        c.execute("""
            SELECT DISTINCT attendance_id 
            FROM pending_attendance_changes 
            WHERE attendance_id IN (SELECT id FROM attendance WHERE student_id IN 
                (SELECT id FROM students WHERE class_id=?))
        """, (class_id_int,))
        pending_ids = {row[0] for row in c.fetchall()}

    conn.close()

    return render_template(
        "admin_attendance.html",
        classes=classes,
        subjects=subjects,
        students_list=students_list,
        attendance_records=attendance_records,
        selected_class=class_id_int,
        selected_subject=subject,
        selected_hour=hour,
        selected_date=date,
        selected_student=int(student_id) if student_id else None,
        pending_ids=pending_ids if class_id_int else set()
    )

@app.route("/delete_attendance/<int:att_id>", methods=["POST"])
def delete_attendance(att_id):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    
    # 1. Capture ALL filters from the form (including hidden inputs)
    class_id = request.form.get("class_id") or ""
    subject = request.form.get("subject") or ""
    hour = request.form.get("hour") or ""     # <--- Ensure this is captured
    date = request.form.get("date") or ""
    student_id = request.form.get("student_id") or ""

    # 2. Perform Deletion
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("DELETE FROM attendance WHERE id=?", (att_id,))
    conn.commit()
    conn.close()

    # 3. Flash Message
    flash("Attendance record deleted successfully.", "success")

    # 4. Redirect preserving ALL filters
    redir = f"/admin/attendance?class_id={class_id}&subject={subject}&hour={hour}&date={date}&student_id={student_id}"
    
    return redirect(redir)

# ---------- ADMIN STUDENT ATTENDANCE HISTORY ----------
@app.route("/admin/student_attendance_history", methods=["GET", "POST"])
def student_attendance_history():
    role = session.get("role")
    if role not in ["admin", "teacher"]:
        return "Unauthorized", 403
        
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Fetch classes
    # If teacher, only show assigned classes
    if role == "teacher":
        teacher_id = session["username"]
        c.execute("""
            SELECT DISTINCT c.id, c.class_name 
            FROM classes c
            JOIN teacher_assignments ta ON c.id = ta.class_id
            WHERE ta.teacher_id=?
        """, (teacher_id,))
    else:
        c.execute("SELECT id, class_name FROM classes ORDER BY class_name")
        
    classes = c.fetchall()
    
    selected_class_id = request.args.get("class_id") or request.form.get("class_id")
    selected_student_id = request.args.get("student_id") or request.form.get("student_id")
    selected_subject = request.args.get("subject") or request.form.get("subject")
    start_date = request.args.get("start_date") or request.form.get("start_date")
    end_date = request.args.get("end_date") or request.form.get("end_date")
    
    filtered_students, attendance_records, student_info, subjects = [], [], None, []
    
    if selected_class_id:
        try:
            cid = int(selected_class_id)
            # Get subjects for this class
            if role == "teacher":
                teacher_id = session["username"]
                c.execute("""
                    SELECT subject FROM teacher_assignments 
                    WHERE teacher_id=? AND class_id=?
                    ORDER BY subject
                """, (teacher_id, cid))
            else:
                c.execute("SELECT name FROM subjects WHERE class_id=? ORDER BY name", (cid,))
            subjects = [row[0] for row in c.fetchall()]

            # Get students
            c.execute("SELECT id, usn, name FROM students WHERE class_id=? ORDER BY name", (cid,))
            filtered_students = c.fetchall()
        except ValueError:
            pass

    if selected_student_id:
        c.execute("SELECT usn, name, class_name FROM students JOIN classes ON students.class_id = classes.id WHERE students.id=?", (selected_student_id,))
        student_info = c.fetchone()
        
        query = "SELECT subject, date, status, hour FROM attendance WHERE student_id=?"
        params = [selected_student_id]
        
        if selected_subject:
            query += " AND subject=?"
            params.append(selected_subject)
            
        if start_date and end_date:
            query += " AND date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        elif start_date:
            query += " AND date >= ?"
            params.append(start_date)
        elif end_date:
            query += " AND date <= ?"
            params.append(end_date)
            
        query += " ORDER BY date DESC, hour ASC"
        c.execute(query, params)
        attendance_records = c.fetchall()
        
    elif selected_class_id:
        query = """
            SELECT students.usn, students.name, attendance.subject, attendance.date, attendance.status, attendance.hour
            FROM attendance
            JOIN students ON attendance.student_id = students.id
            WHERE students.class_id=?
        """
        params = [selected_class_id]
        
        if selected_subject:
            query += " AND attendance.subject=?"
            params.append(selected_subject)
            
        if start_date and end_date:
            query += " AND attendance.date BETWEEN ? AND ?"
            params.extend([start_date, end_date])
        elif start_date:
            query += " AND attendance.date >= ?"
            params.append(start_date)
        elif end_date:
            query += " AND attendance.date <= ?"
            params.append(end_date)
            
        query += " ORDER BY attendance.date DESC, attendance.hour ASC, students.name ASC"
        c.execute(query, params)
        attendance_records = c.fetchall()

    # Calculate subject-wise attendance statistics
    subject_stats = {}
    for record in attendance_records:
        # If student selected: record = (subject, date, status) -> subject is index 0
        # If class selected: record = (usn, name, subject, date, status) -> subject is index 2
        subj = record[0] if selected_student_id else record[2]
        status = record[2] if selected_student_id else record[4]
        
        if subj not in subject_stats:
            subject_stats[subj] = {
                'total': 0,
                'present': 0,
                'absent': 0,
                'percentage': 0
            }
        subject_stats[subj]['total'] += 1
        if status == 'Present':
            subject_stats[subj]['present'] += 1
        else:
            subject_stats[subj]['absent'] += 1
            
    for subj in subject_stats:
        subject_stats[subj]['percentage'] = round(
            (subject_stats[subj]['present'] / subject_stats[subj]['total'] * 100)
            if subject_stats[subj]['total'] > 0 else 0
        )
    
    # Calculate overall statistics
    total_records = len(attendance_records)
    present_count = sum(s['present'] for s in subject_stats.values())
    absent_count = total_records - present_count
    attendance_percentage = round((present_count / total_records * 100) if total_records > 0 else 0)
    
    conn.close()
    return render_template("student_attendance_history.html",
                           classes=classes, filtered_students=filtered_students,
                           subjects=subjects,
                           attendance_records=attendance_records,
                           selected_class_id=int(selected_class_id) if selected_class_id else None,
                           selected_student_id=int(selected_student_id) if selected_student_id else None,
                           selected_subject=selected_subject,
                           student_info=student_info, start_date=start_date, end_date=end_date,
                           total_records=total_records,
                           present_count=present_count,
                           absent_count=absent_count,
                           attendance_percentage=attendance_percentage,
                           subject_stats=subject_stats)

# ---------- OPTIONAL: CSV DOWNLOAD ENDPOINT ----------
@app.route("/admin/download_attendance")
def download_attendance():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    class_id = request.args.get("class_id")
    subject = request.args.get("subject")
    date = request.args.get("date")
    student_id = request.args.get("student_id")
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    query = """SELECT students.usn, students.name, attendance.subject, attendance.date, attendance.status
               FROM attendance
               JOIN students ON attendance.student_id = students.id
               WHERE students.class_id=?"""
    params = [class_id]
    if subject:
        query += " AND attendance.subject=?"
        params.append(subject)
    if date:
        query += " AND attendance.date=?"
        params.append(date)
    if student_id:
        query += " AND students.id=?"
        params.append(student_id)
    query += " ORDER BY attendance.date DESC, students.name"
    c.execute(query, params)
    records = c.fetchall()
    conn.close()
    # create CSV
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["USN", "Name", "Subject", "Date", "Status"])
    for rec in records:
        writer.writerow(rec)
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=attendance_report.csv"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/admin/download_student_history")
def download_student_history():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    student_id = request.args.get("student_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    subject = request.args.get("subject")
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Get student info
    c.execute("SELECT usn, name FROM students WHERE id=?", (student_id,))
    student_info = c.fetchone()
    
    query = """SELECT attendance.subject, attendance.date, attendance.status, attendance.hour
               FROM attendance
               WHERE student_id=?"""
    params = [student_id]
    
    if subject:
        query += " AND subject=?"
        params.append(subject)
    
    if start_date and end_date:
        query += " AND date BETWEEN ? AND ?"
        params.extend([start_date, end_date])
    elif start_date:
        query += " AND date >= ?"
        params.append(start_date)
    elif end_date:
        query += " AND date <= ?"
        params.append(end_date)
    
    query += " ORDER BY date DESC, hour ASC"
    c.execute(query, params)
    records = c.fetchall()
    conn.close()
    
    # Create CSV
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["Subject", "Date", "Status", "Hour"])
    for rec in records:
        writer.writerow(rec)
    
    output = make_response(si.getvalue())
    filename = f"attendance_{student_info[0] if student_info else 'student'}.csv"
    output.headers["Content-Disposition"] = f"attachment; filename={filename}"
    output.headers["Content-type"] = "text/csv"
    return output

@app.route("/admin/download_class_history")
def download_class_history():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    class_id = request.args.get("class_id")
    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")
    subject = request.args.get("subject")
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    query = """SELECT students.usn, students.name, attendance.subject, attendance.date, attendance.status, attendance.hour
               FROM attendance
               JOIN students ON attendance.student_id = students.id
               WHERE students.class_id=?"""
    params = [class_id]
    
    if subject:
        query += " AND attendance.subject=?"
        params.append(subject)
    
    if start_date and end_date:
        query += " AND attendance.date BETWEEN ? AND ?"
        params.extend([start_date, end_date])
    elif start_date:
        query += " AND attendance.date >= ?"
        params.append(start_date)
    elif end_date:
        query += " AND attendance.date <= ?"
        params.append(end_date)
    
    query += " ORDER BY attendance.date DESC, students.name ASC, attendance.hour ASC"
    c.execute(query, params)
    records = c.fetchall()
    conn.close()
    
    # Create CSV
    si = StringIO()
    writer = csv.writer(si)
    writer.writerow(["USN", "Name", "Subject", "Date", "Status", "Hour"])
    for rec in records:
        writer.writerow(rec)
    
    output = make_response(si.getvalue())
    output.headers["Content-Disposition"] = f"attachment; filename=class_attendance_history.csv"
    output.headers["Content-type"] = "text/csv"
    return output

# ---------- STUDENT: Attendance Graph & History ----------
@app.route("/attendance_graph/<subject>")
def attendance_graph(subject):
    if "username" not in session or session["role"] != "student":
        return redirect("/")
    usn = session["username"]
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id FROM students WHERE usn=?", (usn,))
    student_id = c.fetchone()[0]
    c.execute("""SELECT id, date, status, hour 
                 FROM attendance 
                 WHERE student_id=? AND subject=?
                 ORDER BY date, hour""", (student_id, subject))
    attendance_records = c.fetchall()
    # --------------------------------------------------------------
    # 1️⃣  Get the set of attendance IDs that already have a pending
    #     change request for THIS STUDENT (and optionally this subject)
    # --------------------------------------------------------------
    c.execute("""
        SELECT DISTINCT p.attendance_id
        FROM pending_attendance_changes p
        JOIN attendance a ON p.attendance_id = a.id
        WHERE a.student_id = ?
          AND a.subject = ?
    """, (student_id, subject))
    pending_ids = {row[0] for row in c.fetchall()}
    dates = []
    statuses = []
    for record in attendance_records:
        # Format: "date - Hour X"
        dates.append(f"{record[1]} - Hour {record[3]}")
        # Debug: print the status to check what we're getting
        status_str = record[2].strip() if record[2] else ""
        status_value = 1 if status_str.lower() == "present" else 0
        statuses.append(status_value)
    conn.close()
    return render_template("attendance_graph.html", 
                          subject=subject, 
                          dates=dates, 
                          statuses=statuses, 
                          records=attendance_records,
                          pending_ids=pending_ids)

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


# ---------- API FOR BROWSER-BASED RECOGNITION ----------

@app.route("/api/recognize", methods=["POST"])
def api_recognize():
    data = request.get_json()
    image_data = data.get("image")
    class_id = data.get("class_id")

    if not image_data or not class_id:
        return {"error": "Missing data"}, 400

    # Decode base64 image
    try:
        header, encoded = image_data.split(",", 1)
        data = base64.b64decode(encoded)
        np_arr = np.frombuffer(data, np.uint8)
        frame = cv2.imdecode(np_arr, cv2.IMREAD_COLOR)
    except Exception as e:
        return {"error": "Invalid image data"}, 400

    # Get encodings (In production, cache this!)
    known_encodings, student_ids, student_names = get_class_encodings(class_id)

    # Process frame
    results = process_frame(frame, known_encodings, student_ids, student_names)

    return {"results": results}

# Check and migrate DB if needed
def check_db_migration():
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    try:
        c.execute("SELECT hour FROM attendance LIMIT 1")
    except sqlite3.OperationalError:
        print("Migrating database: Adding 'hour' column to attendance table...")
        c.execute("ALTER TABLE attendance ADD COLUMN hour INTEGER DEFAULT 1")
        conn.commit()
    conn.close()

check_db_migration()

@app.route("/api/submit_attendance", methods=["POST"])
def api_submit_attendance():
    data = request.get_json()
    class_id = data.get("class_id")
    subject = data.get("subject")
    hour = data.get("hour")
    present_student_ids = data.get("student_ids", [])

    if not class_id or not subject or not hour:
        return {"error": "Missing class_id, subject, or hour"}, 400

    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Get all students in class to mark absent ones
    c.execute("SELECT id FROM students WHERE class_id=?", (class_id,))
    all_students = [row[0] for row in c.fetchall()]
    
    date_today = datetime.date.today().strftime("%Y-%m-%d")
    
    # Get the last hash to start the chain for this batch
    previous_hash = get_last_hash(c)
    
    for sid in all_students:
        status = "Present" if sid in present_student_ids else "Absent"
        # Check if already marked for today/subject/hour to avoid duplicates
        c.execute("SELECT id FROM attendance WHERE student_id=? AND subject=? AND date=? AND hour=?", (sid, subject, date_today, hour))
        existing = c.fetchone()
        
        if existing:
             # If updating, we update the status. 
             # NOTE: This will technically break the hash chain verification for this record 
             # and potentially subsequent ones, which is the intended behavior for tamper detection.
             # We do NOT update the hash here to preserve the original chain history as much as possible,
             # or we could update it and break the next link. 
             # For now, we just update status.
             c.execute("UPDATE attendance SET status=? WHERE id=?", (status, existing[0]))
        else:
            # Calculate hash for the new record
            current_hash = calculate_hash(sid, subject, date_today, status, hour, previous_hash)
            
            c.execute("INSERT INTO attendance (student_id, subject, date, status, hour, previous_hash, current_hash) VALUES (?,?,?,?,?,?,?)",
                      (sid, subject, date_today, status, hour, previous_hash, current_hash))
            
            # Update previous_hash for the next iteration
            previous_hash = current_hash

    conn.commit()
    conn.close()

    return {"status": "success", "message": "Attendance marked successfully"}

@app.route("/admin/verify_integrity")
def verify_integrity_route():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    
    result = verify_chain()
    return render_template("verify_integrity.html", result=result)

@app.route("/api/get_subjects/<int:class_id>")
def get_subjects_api(class_id):
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT name FROM subjects WHERE class_id=? ORDER BY name", (class_id,))
    subjects = [row[0] for row in c.fetchall()]
    conn.close()
    return json.dumps(subjects)

# ---------- ADMIN APPROVALS ----------
@app.route("/admin/approvals")
def admin_approvals():
    if session.get("role") != "admin":
        return "Unauthorized", 403
        
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Fetch pending changes with details
    c.execute("""
        SELECT p.id, p.timestamp, 
               CASE WHEN p.request_role = 'teacher' THEN t.name ELSE s_req.name END as requester_name,
               p.request_role,
               c.class_name, a.subject, 
               s.name as student_name, s.usn, 
               a.status as old_status, p.new_status, p.comment, p.document_path, a.hour
        FROM pending_attendance_changes p
        JOIN attendance a ON p.attendance_id = a.id
        JOIN students s ON a.student_id = s.id
        JOIN classes c ON s.class_id = c.id
        LEFT JOIN teachers t ON p.requested_by = t.teacher_id
        LEFT JOIN students s_req ON p.requested_by = s_req.usn
        ORDER BY p.timestamp DESC
    """)

    rows = c.fetchall()
    
    pending_changes = []
    for row in rows:
        pending_changes.append({
            "id": row[0],
            "timestamp": row[1],
            "requester_name": row[2],
            "request_role": row[3],
            "class_name": row[4],
            "subject": row[5],
            "student_name": row[6],
            "usn": row[7],
            "old_status": row[8],
            "new_status": row[9],
            "comment": row[10],
            "document_path": row[11],
            "hour": row[12]
        })
    
    # Group changes by requester, timestamp (within same second), and group comment
    from collections import defaultdict
    groups = defaultdict(list)
    
    for change in pending_changes:
        # Extract group comment (before | separator)
        group_comment = change['comment'].split(' | ')[0]
        # Group by requester, timestamp (first 19 chars = yyyy-mm-dd hh:mm:ss), and group comment
        key = (change['requester_name'], change['timestamp'][:19], group_comment)
        groups[key].append(change)
    
    # Convert to list of groups
    grouped_changes = list(groups.values())
    
    conn.close()
    return render_template("admin_approvals.html", grouped_changes=grouped_changes)

@app.route("/student/request_change", methods=["POST"])
def student_request_change():
    if "username" not in session or session["role"] != "student":
        return "Unauthorized", 403
        
    attendance_id = request.form.get("attendance_id")
    reason = request.form.get("reason")
    usn = session["username"]
    
    # Handle file upload
    document_path = None
    if "document" in request.files:
        file = request.files["document"]
        if file and file.filename:
            filename = f"{usn}_{datetime.datetime.now().strftime('%Y%m%d%H%M%S')}_{file.filename}"
            # Ensure uploads directory exists
            upload_dir = os.path.join("static", "uploads", "documents")
            if not os.path.exists(upload_dir):
                os.makedirs(upload_dir)
            
            filepath = os.path.join(upload_dir, filename)
            file.save(filepath)
            document_path = f"uploads/documents/{filename}" # Relative path for serving
            
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # Check if there's already a pending request for this attendance record
    c.execute("SELECT id, request_role FROM pending_attendance_changes WHERE attendance_id=?", (attendance_id,))
    existing_request = c.fetchone()
    
    if existing_request:
        conn.close()
        requester_type = "a teacher" if existing_request[1] == "teacher" else "another student"
        flash(f"⚠️ A change request for this record is already pending from {requester_type}. Please wait for admin action.", "warning")
        return redirect("/dashboard")
    
    # Insert pending change request
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    c.execute("""
        INSERT INTO pending_attendance_changes 
        (attendance_id, new_status, requested_by, timestamp, comment, request_role, document_path)
        VALUES (?, ?, ?, ?, ?, 'student', ?)
    """, (attendance_id, 'Present', usn, timestamp, reason, document_path))
    
    conn.commit()
    conn.close()
    
    flash("Change request submitted successfully! Waiting for admin approval.", "success")
    return redirect("/dashboard")

@app.route("/approve_change/<int:change_id>", methods=["POST"])
def approve_change(change_id):
    if session.get("role") != "admin":
        return "Unauthorized", 403
        
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    # 1. Get change details
    c.execute("SELECT attendance_id, new_status FROM pending_attendance_changes WHERE id=?", (change_id,))
    change = c.fetchone()
    
    if change:
        att_id, new_status = change
        
        # 2. Update attendance status
        c.execute("UPDATE attendance SET status=? WHERE id=?", (new_status, att_id))
        
        # 3. Recalculate Hash Chain from this record forward
        # This is the critical step for "Rewrite History"
        recalculate_chain(c, att_id)
        
        # 4. Remove from pending
        c.execute("DELETE FROM pending_attendance_changes WHERE id=?", (change_id,))
        
        conn.commit()
        flash("Change approved and hash chain recalculated.", "success")
    else:
        flash("Change request not found.", "error")
        
    conn.close()
    return redirect("/admin/approvals")

@app.route("/reject_change/<int:change_id>", methods=["POST"])
def reject_change(change_id):
    if session.get("role") != "admin":
        return "Unauthorized", 403
        
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("DELETE FROM pending_attendance_changes WHERE id=?", (change_id,))
    conn.commit()
    conn.close()
    
    flash("Change request rejected.", "info")
    return redirect("/admin/approvals")

@app.route("/approve_batch", methods=["POST"])
def approve_batch():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    
    change_ids = request.form.getlist("change_ids[]")
    if not change_ids:
        flash("No changes selected", "error")
        return redirect("/admin/approvals")
    
    conn = sqlite3.connect('attendance.db')
    conn.execute("BEGIN IMMEDIATE")  # Exclusive lock for transaction safety
    c = conn.cursor()
    
    try:
        attendance_ids = []
        
        # Collect all attendance IDs and apply changes
        for change_id in change_ids:
            c.execute("SELECT attendance_id, new_status FROM pending_attendance_changes WHERE id=?", 
                      (change_id,))
            result = c.fetchone()
            if result:
                att_id, new_status = result
                # Update status
                c.execute("UPDATE attendance SET status=? WHERE id=?", (new_status, att_id))
                attendance_ids.append(att_id)
        
        # Find minimum ID to recalculate from
        if attendance_ids:
            min_id = min(attendance_ids)
            
            # SINGLE recalculation from minimum ID
            recalculate_chain(c, min_id)
        
        # Remove all from pending
        placeholders = ",".join("?" * len(change_ids))
        c.execute(f"DELETE FROM pending_attendance_changes WHERE id IN ({placeholders})", 
                  change_ids)
        
        conn.commit()
        flash(f"{len(change_ids)} change(s) approved successfully!", "success")
        
    except Exception as e:
        conn.rollback()
        flash(f"Error during batch approval: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect("/admin/approvals")

@app.route("/reject_batch", methods=["POST"])
def reject_batch():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    
    change_ids = request.form.getlist("change_ids[]")
    if not change_ids:
        flash("No changes selected", "error")
        return redirect("/admin/approvals")
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    
    try:
        # Remove all from pending
        placeholders = ",".join("?" * len(change_ids))
        c.execute(f"DELETE FROM pending_attendance_changes WHERE id IN ({placeholders})", 
                  change_ids)
        
        conn.commit()
        flash(f"{len(change_ids)} change(s) rejected.", "info")
        
    except Exception as e:
        conn.rollback()
        flash(f"Error during batch rejection: {str(e)}", "error")
    finally:
        conn.close()
    
    return redirect("/admin/approvals")

if __name__ == "__main__":
    app.run(debug=True)
