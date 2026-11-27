import os, json, secrets, time
from datetime import datetime, timedelta, timezone
from functools import wraps
from flask import Flask, request, jsonify, render_template, redirect, url_for, session
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, logout_user, login_required, current_user
from werkzeug.security import generate_password_hash, check_password_hash
from flask_wtf.csrf import CSRFProtect
from sqlalchemy import text

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "")
if not app.config["SECRET_KEY"]:
    raise RuntimeError("Set a long random SECRET_KEY in the environment before starting.")
app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "sqlite:///afyatrack.db")
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = os.environ.get("COOKIE_SECURE", "0") == "1"
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=8)
app.config["WTF_CSRF_CHECK_DEFAULT"] = False

db = SQLAlchemy(app)
login_manager = LoginManager(app)
csrf = CSRFProtect(app)

class Unit(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(160), unique=True, nullable=False)
    code = db.Column(db.String(40), unique=True, nullable=False)
    active = db.Column(db.Boolean, default=True, nullable=False)

class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    role = db.Column(db.String(24), nullable=False, default="unit_user")
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), nullable=True)
    active = db.Column(db.Boolean, default=True, nullable=False)
    failed_attempts = db.Column(db.Integer, default=0, nullable=False)
    locked_until = db.Column(db.DateTime, nullable=True)
    unit = db.relationship("Unit")

class UnitData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    unit_id = db.Column(db.Integer, db.ForeignKey("unit.id"), unique=True, nullable=False)
    payload = db.Column(db.Text, nullable=False, default="{}")
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

def user_json(user):
    return {"username": user.username, "role": user.role,
            "unit_name": user.unit.name if user.unit else None}

def require_unit(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        if current_user.role == "platform_admin":
            unit_id = session.get("view_unit_id")
            if not unit_id:
                return jsonify(error="Platform administrator must select a unit workspace."), 400
        else:
            unit_id = current_user.unit_id
        if not unit_id:
            return jsonify(error="This account is not assigned to a PHC unit."), 403
        unit = db.session.get(Unit, unit_id)
        if not unit or not unit.active:
            return jsonify(error="The assigned unit is inactive."), 403
        return fn(unit, *args, **kwargs)
    return wrapped

@app.after_request
def security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self';"
    return response

@app.get("/")
def index():
    return render_template("index.html")

@app.post("/login")
@csrf.exempt
def login():
    data = request.get_json(silent=True) or {}
    username, password = str(data.get("username", "")).strip(), str(data.get("password", ""))
    user = User.query.filter_by(username=username).first()
    now = datetime.utcnow()
    if not user or not user.active:
        return jsonify(error="Invalid username or password."), 401
    if user.locked_until and user.locked_until > now:
        return jsonify(error="Account temporarily locked. Try again later."), 429
    if not check_password_hash(user.password_hash, password):
        user.failed_attempts += 1
        if user.failed_attempts >= 5:
            user.failed_attempts = 0
            user.locked_until = now + timedelta(minutes=15)
        db.session.commit()
        return jsonify(error="Invalid username or password."), 401
    user.failed_attempts, user.locked_until = 0, None
    db.session.commit()
    login_user(user, remember=False, fresh=True)
    session.permanent = True
    if user.role == "platform_admin":
        session.pop("view_unit_id", None)
    return jsonify(user=user_json(user))

@app.post("/logout")
@login_required
@csrf.exempt
def logout():
    logout_user()
    session.clear()
    return jsonify(ok=True)

@app.get("/api/me")
@login_required
def me():
    return jsonify(user=user_json(current_user))

@app.get("/api/data")
@login_required
@require_unit
def get_data(unit):
    row = UnitData.query.filter_by(unit_id=unit.id).first()
    payload = json.loads(row.payload) if row else {}
    return jsonify({k: payload.get(k, []) for k in
        ("patients","records","appointments","staff","labRequests","medicines","pharmacyLog")})

@app.put("/api/data")
@login_required
@require_unit
def put_data(unit):
    incoming = request.get_json(silent=True)
    if not isinstance(incoming, dict):
        return jsonify(error="Expected a JSON data object."), 400
    allowed = ("patients","records","appointments","staff","labRequests","medicines","pharmacyLog")
    clean = {}
    for key in allowed:
        value = incoming.get(key, [])
        if not isinstance(value, list):
            return jsonify(error=f"{key} must be a list."), 400
        clean[key] = value
    row = UnitData.query.filter_by(unit_id=unit.id).first()
    if row is None:
        row = UnitData(unit_id=unit.id, payload=json.dumps(clean, ensure_ascii=False))
        db.session.add(row)
    else:
        row.payload = json.dumps(clean, ensure_ascii=False)
    db.session.commit()
    return jsonify(ok=True, updated_at=row.updated_at.isoformat())

@app.get("/admin/units")
@login_required
def list_units():
    if current_user.role != "platform_admin":
        return jsonify(error="Platform administrator only."), 403
    return jsonify(units=[{"id":u.id,"name":u.name,"code":u.code,"active":u.active}
                          for u in Unit.query.order_by(Unit.name).all()])

@app.post("/admin/units")
@login_required
@csrf.exempt
def create_unit():
    if current_user.role != "platform_admin":
        return jsonify(error="Platform administrator only."), 403
    d = request.get_json(silent=True) or {}
    name, code = str(d.get("name","")).strip(), str(d.get("code","")).strip().upper()
    if not name or not code:
        return jsonify(error="Unit name and code are required."), 400
    if Unit.query.filter((Unit.name==name)|(Unit.code==code)).first():
        return jsonify(error="A unit with that name or code already exists."), 409
    u=Unit(name=name,code=code); db.session.add(u); db.session.commit()
    return jsonify(id=u.id,name=u.name,code=u.code), 201

@app.post("/admin/users")
@login_required
@csrf.exempt
def create_user():
    if current_user.role != "platform_admin":
        return jsonify(error="Platform administrator only."), 403
    d=request.get_json(silent=True) or {}
    username=str(d.get("username","")).strip()
    password=str(d.get("password",""))
    role=str(d.get("role","unit_user"))
    unit_id=d.get("unit_id")
    if not username or len(password)<12 or role not in ("unit_admin","unit_user"):
        return jsonify(error="Username, a 12-character minimum password, and valid unit role are required."),400
    if User.query.filter_by(username=username).first():
        return jsonify(error="Username already exists."),409
    unit=db.session.get(Unit, int(unit_id)) if unit_id else None
    if not unit:
        return jsonify(error="Valid unit_id required."),400
    user=User(username=username,password_hash=generate_password_hash(password),role=role,unit_id=unit.id)
    db.session.add(user);db.session.commit()
    return jsonify(ok=True,username=username,unit=unit.name),201

@app.post("/admin/select-unit")
@login_required
@csrf.exempt
def select_unit():
    if current_user.role!="platform_admin":
        return jsonify(error="Platform administrator only."),403
    d=request.get_json(silent=True) or {}
    unit=db.session.get(Unit,int(d.get("unit_id",0)))
    if not unit or not unit.active:return jsonify(error="Unit not found or inactive."),404
    session["view_unit_id"]=unit.id
    return jsonify(ok=True,unit=unit.name)

@app.cli.command("init-db")
def init_db():
    db.create_all()
    print("AfyaTrack database tables created.")

@app.cli.command("create-platform-admin")
def create_platform_admin():
    db.create_all()
    username=input("Admin username: ").strip()
    password=input("Admin password (minimum 12 characters): ")
    if len(password)<12: raise SystemExit("Password too short.")
    if User.query.filter_by(username=username).first(): raise SystemExit("Username already exists.")
    db.session.add(User(username=username,password_hash=generate_password_hash(password),role="platform_admin"))
    db.session.commit()
    print("Platform administrator created.")

@app.cli.command("create-unit")
def create_unit_cli():
    name=input("PHC unit name: ").strip()
    code=input("Unique unit code: ").strip().upper()
    if not name or not code: raise SystemExit("Name and code are required.")
    if Unit.query.filter((Unit.name==name)|(Unit.code==code)).first(): raise SystemExit("Unit already exists.")
    db.session.add(Unit(name=name,code=code));db.session.commit()
    print("Unit created.")

@app.cli.command("create-unit-user")
def create_unit_user_cli():
    unit_code=input("Unit code: ").strip().upper()
    unit=Unit.query.filter_by(code=unit_code).first()
    if not unit: raise SystemExit("Unit code not found.")
    username=input("Username: ").strip()
    password=input("Password (minimum 12 characters): ")
    role=input("Role (unit_admin/unit_user) [unit_admin]: ").strip() or "unit_admin"
    if len(password)<12 or role not in ("unit_admin","unit_user"): raise SystemExit("Invalid password or role.")
    if User.query.filter_by(username=username).first(): raise SystemExit("Username already exists.")
    db.session.add(User(username=username,password_hash=generate_password_hash(password),role=role,unit_id=unit.id))
    db.session.commit()
    print("Unit user created.")

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT","8000")))
