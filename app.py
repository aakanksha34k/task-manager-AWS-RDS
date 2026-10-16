import os
from datetime import date
from functools import wraps

from flask import Flask, render_template, request, redirect, url_for, session, flash
from flask_sqlalchemy import SQLAlchemy
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ["FLASK_SECRET_KEY"]
app.config["SQLALCHEMY_DATABASE_URI"] = (
    f"mysql+pymysql://{os.environ['DB_USER']}:{os.environ['DB_PASSWORD']}"
    f"@{os.environ['DB_HOST']}:{os.environ.get('DB_PORT', '3306')}/"
    f"{os.environ.get('DB_NAME', 'student_tasks')}?charset=utf8mb4"
)
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "true").lower() == "true"
db = SQLAlchemy(app)

class User(db.Model):
    __tablename__ = "users"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(150), unique=True, nullable=False, index=True)
    password_hash = db.Column(db.String(255), nullable=False)
    tasks = db.relationship("Task", backref="owner", cascade="all, delete-orphan")

class Task(db.Model):
    __tablename__ = "tasks"
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    title = db.Column(db.String(200), nullable=False)
    deadline = db.Column(db.Date, nullable=True)
    priority = db.Column(db.Enum("Low", "Medium", "High"), nullable=False, default="Medium")
    status = db.Column(db.Enum("Pending", "Completed"), nullable=False, default="Pending")

def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if "user_id" not in session:
            flash("Please log in first.", "warning")
            return redirect(url_for("login"))
        return view(*args, **kwargs)
    return wrapped

@app.route("/")
def index():
    if "user_id" in session:
        return redirect(url_for("dashboard"))
    return redirect(url_for("login"))

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not name or not email or len(password) < 12:
            flash("Enter your name/email and a password of at least 12 characters.", "danger")
            return render_template("register.html")
        if User.query.filter_by(email=email).first():
            flash("That email is already registered.", "danger")
            return render_template("register.html")
        user = User(name=name, email=email, password_hash=generate_password_hash(password))
        db.session.add(user)
        db.session.commit()
        flash("Account created. Please log in.", "success")
        return redirect(url_for("login"))
    return render_template("register.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        user = User.query.filter_by(email=email).first()
        if not user or not check_password_hash(user.password_hash, password):
            flash("Invalid email or password.", "danger")
            return render_template("login.html")
        session.clear()
        session["user_id"] = user.id
        session["user_name"] = user.name
        return redirect(url_for("dashboard"))
    return render_template("login.html")

@app.post("/logout")
@login_required
def logout():
    session.clear()
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    tasks = Task.query.filter_by(user_id=session["user_id"]).order_by(
        Task.status.asc(), Task.deadline.asc()
    ).all()
    return render_template("dashboard.html", tasks=tasks, today=date.today())

@app.post("/tasks")
@login_required
def add_task():
    title = request.form.get("title", "").strip()
    deadline_raw = request.form.get("deadline", "").strip()
    priority = request.form.get("priority", "Medium")
    if not title or len(title) > 200 or priority not in {"Low", "Medium", "High"}:
        flash("Please provide a valid task title and priority.", "danger")
        return redirect(url_for("dashboard"))
    deadline = None
    if deadline_raw:
        try:
            deadline = date.fromisoformat(deadline_raw)
        except ValueError:
            flash("Invalid deadline.", "danger")
            return redirect(url_for("dashboard"))
    db.session.add(Task(user_id=session["user_id"], title=title, deadline=deadline, priority=priority))
    db.session.commit()
    flash("Task added.", "success")
    return redirect(url_for("dashboard"))

@app.post("/tasks/<int:task_id>/toggle")
@login_required
def toggle_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=session["user_id"]).first_or_404()
    task.status = "Completed" if task.status == "Pending" else "Pending"
    db.session.commit()
    return redirect(url_for("dashboard"))

@app.post("/tasks/<int:task_id>/delete")
@login_required
def delete_task(task_id):
    task = Task.query.filter_by(id=task_id, user_id=session["user_id"]).first_or_404()
    db.session.delete(task)
    db.session.commit()
    flash("Task deleted.", "success")
    return redirect(url_for("dashboard"))

@app.get("/health")
def health():
    # Does not expose database details or credentials.
    try:
        db.session.execute(text("SELECT 1"))
        return {"status": "ok"}, 200
    except Exception:
        app.logger.exception("Health check database failure")
        return {"status": "unavailable"}, 503

if __name__ == "__main__":
    # Local development only. Production uses Gunicorn.
    app.run(host="127.0.0.1", port=5000, debug=False)
