# AI-Based Attendance System with Face Recognition

An intelligent attendance management system that uses facial recognition technology to track student attendance in educational institutions. This application is built with **Flask** and uses **SQLite** for data storage.

## Features

- **Real-time Face Recognition**: Automated attendance marking using webcam feed.
- **Admin Dashboard**: Comprehensive management of students, teachers, classes, and subjects.
- **Role-Based Access**: Separate portals for Admins, Teachers, and Students.
- **Attendance Analytics**: Visual graphs and detailed history of attendance records.
- **Data Integrity**: Cryptographic hash chain to prevent tampering with attendance records.
- **Floating Notifications**: Modern, non-intrusive notification system.

## Getting Started

### Prerequisites

- Python 3.8+
- Webcam for face recognition

### Installation

1.  **Clone the repository**:
    ```bash
    git clone https://github.com/Soham-Rao/Attendance.git
    cd Attendance
    ```

2.  **Install dependencies**:
    ```bash
    pip install -r requirements.txt
    ```

3.  **Initialize the Database**:
    ```bash
    python database.py
    ```

4.  **Run the application**:
    ```bash
    python app.py
    ```

5.  **Access the App**:
    Open your browser and navigate to `http://localhost:5000`.

## Deployment

This application uses **SQLite**, a file-based database. For free hosting, **PythonAnywhere** is the recommended platform as it supports persistent file storage, ensuring your database isn't wiped on server restarts.

## Technical Details

-   **Backend**: Flask (Python)
-   **Database**: SQLite
-   **Face Recognition**: `face_recognition` library (dlib) & OpenCV
-   **Frontend**: HTML5, CSS3, JavaScript (Jinja2 Templates)

## Contributing

1.  Fork the repository
2.  Create your feature branch
3.  Commit your changes
4.  Push to the branch
5.  Create a Pull Request
