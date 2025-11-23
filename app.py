import os
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

app = Flask(__name__)
app.secret_key = "face_attendance_secret"
init_db()

# ---------- LOGIN ----------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        role = request.form.get("role")
        username = request.form.get("username")
        password = request.form.get("password")
        conn = sqlite3.connect('attendance.db')
        c = conn.cursor()
        if role == "admin":
            c.execute("SELECT * FROM admin WHERE username=? AND password=?", (username, password))
        elif role == "student":
            c.execute("SELECT * FROM students WHERE usn=? AND dob=?", (username, password))
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

    usn = session["username"]
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_id FROM students WHERE usn=?", (usn,))
    student_row = c.fetchone()
    
    if not student_row:
        conn.close()
        return "Student not found", 404
    
    student_id, class_id = student_row
    
    current_subject, current_status = None, None
    
    # Check current ongoing class
    if class_id:
        now = datetime.datetime.now().time()
        c.execute("SELECT subject, start_time, end_time FROM timetable WHERE class_id=?", (class_id,))
        for subject, start_str, end_str in c.fetchall():
            start_time = datetime.datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.datetime.strptime(end_str, "%H:%M").time()
            if start_time <= now <= end_time:
                current_subject = subject
                break
        if current_subject:
            today_str = datetime.date.today().strftime("%Y-%m-%d")
            c.execute("SELECT status FROM attendance WHERE student_id=? AND subject=? AND date=?",
                      (student_id, current_subject, today_str))
            row = c.fetchone()
            current_status = row[0] if row else "Absent"
    
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
    
    conn.close()
    return render_template("student_dashboard.html",
                           usn=usn,
                           current_subject=current_subject,
                           current_status=current_status,
                           subject_stats=subject_stats,
                           overall_percentage=overall_percentage,
                           total_present=total_present,
                           total_classes=total_classes)

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
    class_name = request.form["class_name"]
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("INSERT INTO classes (class_name) VALUES (?)", (class_name,))
    conn.commit()
    conn.close()
    return redirect("/classes")

# ---------- TIMETABLE ----------
@app.route("/timetable")
def timetable():
    selected_class_id = request.args.get("class_id")
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_name FROM classes")
    classes = c.fetchall()
    timetable = []
    if selected_class_id:
        c.execute("""
            SELECT timetable.id, classes.class_name, timetable.subject, timetable.start_time, timetable.end_time
            FROM timetable
            JOIN classes ON timetable.class_id = classes.id
            WHERE timetable.class_id=?
            ORDER BY timetable.start_time
        """, (selected_class_id,))
        timetable = c.fetchall()
    conn.close()
    return render_template("timetable.html", classes=classes, timetable=timetable,
                           selected_class_id=int(selected_class_id) if selected_class_id else None)

@app.route("/add_timetable", methods=["POST"])
def add_timetable():
    class_id = request.form["class_id"]
    subject = request.form["subject"]
    start_time = request.form["start_time"]
    end_time = request.form["end_time"]
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("INSERT INTO timetable (class_id, subject, start_time, end_time) VALUES (?,?,?,?)",
              (class_id, subject, start_time, end_time))
    conn.commit()
    conn.close()
    return redirect(f"/timetable?class_id={class_id}")

@app.route("/delete_timetable/<int:tid>", methods=["POST"])
def delete_timetable(tid):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    class_id = request.form.get("class_id")
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT subject FROM timetable WHERE id=?", (tid,))
    row = c.fetchone()
    if row:
        subject_to_delete = row[0]
        c.execute("""
            DELETE FROM attendance
            WHERE subject=? AND student_id IN (
                SELECT id FROM students WHERE class_id=?
            )
        """, (subject_to_delete, class_id))
    c.execute("DELETE FROM timetable WHERE id=?", (tid,))
    conn.commit()
    conn.close()
    return redirect(f"/timetable?class_id={class_id}")

@app.route("/edit_timetable/<int:tid>", methods=["GET", "POST"])
def edit_timetable(tid):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    if request.method == "POST":
        subject = request.form["subject"]
        start_time = request.form["start_time"]
        end_time = request.form["end_time"]
        class_id = request.form["class_id"]
        c.execute("UPDATE timetable SET subject=?, start_time=?, end_time=? WHERE id=?",
                  (subject, start_time, end_time, tid))
        conn.commit()
        conn.close()
        return redirect(f"/timetable?class_id={class_id}")
    else:
        c.execute("SELECT class_id, subject, start_time, end_time FROM timetable WHERE id=?", (tid,))
        row = c.fetchone()
        conn.close()
        if row:
            class_id, subject, start_time, end_time = row
            return render_template("edit_timetable.html", tid=tid, class_id=class_id,
                                   subject=subject, start_time=start_time, end_time=end_time)
    return "Not found", 404

# ---------- ADD STUDENT (dropdown) ----------
@app.route("/add_student_form")
def add_student_form():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_name FROM classes ORDER BY class_name")
    classes = c.fetchall()
    conn.close()
    return render_template("add_student.html", classes=classes)

@app.route("/add_student", methods=["POST"])
def add_student():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    usn = request.form["usn"]
    name = request.form["name"]
    dob = request.form["dob"]
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
            return redirect("/add_student_form")
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
    name = request.form["name"]
    
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("INSERT INTO subjects (class_id, name) VALUES (?,?)", (class_id, name))
    conn.commit()
    conn.close()
    
    flash("Subject added successfully!", "success")
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
@app.route("/mark_attendance", methods=["GET", "POST"])
def mark_attendance():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_name FROM classes")
    classes = c.fetchall()
    selected_class_id = request.args.get("class_id")
    subjects = []
    if selected_class_id:
        try:
            cid = int(selected_class_id)
            # Fetch subjects from the new subjects table
            c.execute("SELECT name FROM subjects WHERE class_id=? ORDER BY name", (cid,))
            subjects = [row[0] for row in c.fetchall()]
        except ValueError:
            pass
    conn.close()
    if request.method == "POST":
        conn = sqlite3.connect('attendance.db')
        c = conn.cursor()
        class_id = int(request.form["class_id"])
        subject = request.form["subject"]
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
        
        # run_live_attendance(class_id, subject) # DEPRECATED: Local webcam loop
        # return "Attendance process completed"
        return render_template("live_attendance.html", 
                               class_id=class_id, 
                               subject=subject, 
                               hour=hour, 
                               total_students=total_students,
                               existing_present_data=existing_present_data)
    return render_template("mark_attendance.html",
                           classes=classes,
                           selected_class_id=int(selected_class_id) if selected_class_id else None,
                           subjects=subjects)

# ---------- ADMIN ATTENDANCE ----------
@app.route("/admin/attendance", methods=["GET", "POST"])
def admin_attendance():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    if request.method == "POST":
        for key, value in request.form.items():
            if key.startswith("status_"):
                att_id = key.split("_")[1]
                c.execute("UPDATE attendance SET status=? WHERE id=?", (value, att_id))
        conn.commit()
    class_id = request.args.get("class_id")
    subject = request.args.get("subject")
    hour = request.args.get("hour")
    date = request.args.get("date", datetime.date.today().strftime("%Y-%m-%d"))
    student_id = request.args.get("student_id")
    try:
        class_id_int = int(class_id) if class_id else None
    except:
        class_id_int = None
    c.execute("SELECT id, class_name FROM classes")
    classes = c.fetchall()
    subjects = []
    students_list = []
    attendance_records = []
    if class_id_int:
        # Fetch subjects from subjects table
        c.execute("SELECT name FROM subjects WHERE class_id=? ORDER BY name", (class_id_int,))
        subjects = [s[0] for s in c.fetchall()]
        c.execute("SELECT id, usn, name FROM students WHERE class_id=? ORDER BY name", (class_id_int,))
        students_list = c.fetchall()
        if subject:
            query = """SELECT attendance.id, students.usn, students.name, attendance.subject, attendance.hour, attendance.date, attendance.status
                       FROM attendance JOIN students ON attendance.student_id = students.id
                       WHERE students.class_id=? AND attendance.subject=? AND attendance.date=?"""
            params = [class_id_int, subject, date]
            if hour:
                query += " AND attendance.hour=?"
                params.append(hour)
        else:
            query = """SELECT attendance.id, students.usn, students.name, attendance.subject, attendance.hour, attendance.date, attendance.status
                       FROM attendance JOIN students ON attendance.student_id = students.id
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
        attendance_records = c.fetchall() # Always a list, works for single/multiple records 
    conn.close()
    return render_template("admin_attendance.html",
                           classes=classes, subjects=subjects,
                           students_list=students_list,
                           attendance_records=attendance_records,
                           selected_class=class_id_int,
                           selected_subject=subject,
                           selected_hour=hour,
                           selected_date=date,
                           selected_student=int(student_id) if student_id else None)

@app.route("/delete_attendance/<int:att_id>", methods=["POST"])
def delete_attendance(att_id):
    if session.get("role") != "admin":
        return "Unauthorized", 403
    class_id = request.form.get("class_id")
    subject = request.form.get("subject")
    date = request.form.get("date")
    student_id = request.form.get("student_id")
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("DELETE FROM attendance WHERE id=?", (att_id,))
    conn.commit()
    conn.close()
    redir = f"/admin/attendance?class_id={class_id}&subject={subject}&date={date}"
    if student_id:
        redir += f"&student_id={student_id}"
    return redirect(redir)

# ---------- ADMIN STUDENT ATTENDANCE HISTORY ----------
@app.route("/admin/student_attendance_history", methods=["GET", "POST"])
def student_attendance_history():
    if session.get("role") != "admin":
        return "Unauthorized", 403
    conn = sqlite3.connect('attendance.db')
    c = conn.cursor()
    c.execute("SELECT id, class_name FROM classes ORDER BY class_name")
    classes = c.fetchall()
    selected_class_id = request.args.get("class_id") or request.form.get("class_id")
    selected_student_id = request.args.get("student_id") or request.form.get("student_id")
    selected_subject = request.args.get("subject") or request.form.get("subject")
    start_date = request.args.get("start_date") or request.form.get("start_date")
    end_date = request.args.get("end_date") or request.form.get("end_date")
    
    filtered_students, attendance_records, student_info, subjects = [], [], None, []
    
    if selected_class_id:
        # Get students
        c.execute("SELECT id, usn, name FROM students WHERE class_id=? ORDER BY name", (selected_class_id,))
        filtered_students = c.fetchall()
        # Get subjects for this class from subjects table
        c.execute("SELECT name FROM subjects WHERE class_id=? ORDER BY name", (selected_class_id,))
        subjects = [row[0] for row in c.fetchall()]

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
    c.execute("""SELECT date, status 
                 FROM attendance 
                 WHERE student_id=? AND subject=?
                 ORDER BY date""", (student_id, subject))
    attendance_records = c.fetchall()
    dates = []
    statuses = []
    for record in attendance_records:
        dates.append(record[0])
        statuses.append(1 if record[1].strip().lower() == "present" else 0)
    conn.close()
    return render_template("attendance_graph.html", 
                          subject=subject, 
                          dates=dates, 
                          statuses=statuses, 
                          records=attendance_records)

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
    
    for sid in all_students:
        status = "Present" if sid in present_student_ids else "Absent"
        # Check if already marked for today/subject/hour to avoid duplicates
        c.execute("SELECT id FROM attendance WHERE student_id=? AND subject=? AND date=? AND hour=?", (sid, subject, date_today, hour))
        existing = c.fetchone()
        if existing:
             c.execute("UPDATE attendance SET status=? WHERE id=?", (status, existing[0]))
        else:
            c.execute("INSERT INTO attendance (student_id, subject, date, status, hour) VALUES (?,?,?,?,?)",
                      (sid, subject, date_today, status, hour))

    conn.commit()
    conn.close()

    return {"status": "success", "message": "Attendance marked successfully"}

if __name__ == "__main__":
    app.run(debug=True)
