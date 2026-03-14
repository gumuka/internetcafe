from flask import Flask, render_template, url_for, request, redirect, flash, send_file, session, jsonify
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import sqlite3
import io
import time
import random

db_local = "internetcafe.db"

app = Flask(__name__)
app.secret_key = "secret123"

# ==========================================
# 🏗️ ส่วนการจัดการฐานข้อมูล (Database Setup)
# ==========================================
def setup_database():
    connect = sqlite3.connect(db_local)
    cursor = connect.cursor()

    # 🌟 1. ปล่อยข้อมูล Person และ Member ไว้เหมือนเดิม (ไม่ลบทิ้ง)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Person (
        Person_ID INTEGER PRIMARY KEY AUTOINCREMENT,
        Username TEXT UNIQUE,
        Password TEXT,
        Full_Name TEXT,
        Type TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS Member (
        Person_ID INTEGER PRIMARY KEY,
        Time_Balance INTEGER DEFAULT 0,
        Remaining_Time INTEGER DEFAULT 0,
        Phone_Number TEXT,
        FOREIGN KEY(Person_ID) REFERENCES Person(Person_ID)
    )
    """)

    # 🌟 2. ลบเฉพาะตาราง Computer ของเดิม เพื่อสร้างใหม่ให้มีระบบ IP Address
    cursor.execute("DROP TABLE IF EXISTS Computer")
    cursor.execute("""
    CREATE TABLE Computer (
        Computer_ID INTEGER PRIMARY KEY AUTOINCREMENT,
        Computer_Name TEXT,
        IP_Address TEXT UNIQUE, 
        Status TEXT DEFAULT 'Online',
        Current_Member_ID INTEGER,
        FOREIGN KEY (Current_Member_ID) REFERENCES Person(Person_ID)
    )
    """)

    # 🌟 3. ใส่ข้อมูลเครื่อง IP จริงของกลุ่มคุณ
    pcs = [
        ('PC-01 (Your Pc)', '10.54.54.145', 'Online'),
        ('PC-02 (Friend 1)', '10.54.48.202', 'Online'),
        ('PC-03 (Friend 2)', '10.54.55.217', 'Online'),
        ('Local Test', '127.0.0.1', 'Online')
    ]
    cursor.executemany("INSERT INTO Computer (Computer_Name, IP_Address, Status) VALUES (?, ?, ?)", pcs)

    connect.commit()
    connect.close()
    
# ==========================================
# 🔐 ระบบยืนยันตัวตน (Flask-Login)
# ==========================================
login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = "login"

class User(UserMixin):
    def __init__(self, person_id, username, full_name, role, phone=None):
        self.id = str(person_id) 
        self.username = username
        self.full_name = full_name
        self.role = role
        self.phone = phone if phone else "-"

@login_manager.user_loader
def load_user(user_id):
    connect = sqlite3.connect(db_local)
    connect.row_factory = sqlite3.Row
    cursor = connect.cursor()
    cursor.execute("""
        SELECT p.Person_ID, p.Username, p.Full_Name, p.Type, m.Phone_Number 
        FROM Person p
        LEFT JOIN Member m ON p.Person_ID = m.Person_ID
        WHERE p.Person_ID = ?
    """, (user_id,))
    user = cursor.fetchone()
    connect.close()

    if user:
        return User(user["Person_ID"], user["Username"], user["Full_Name"], user["Type"], user["Phone_Number"])
    return None

# ==========================================
# ⏳ ระบบจัดการเวลา Real-time
# ==========================================
@app.before_request
def update_member_time():
    if current_user.is_authenticated and current_user.role != "Admin":
        now = int(time.time())
        last_active = session.get('last_active')
        
        if last_active:
            elapsed_time = now - last_active
            if elapsed_time > 0:
                connect = sqlite3.connect(db_local)
                cursor = connect.cursor()
                cursor.execute("""
                    UPDATE Member SET Remaining_Time = MAX(0, Remaining_Time - ?) WHERE Person_ID = ?
                """, (elapsed_time, current_user.id))
                
                cursor.execute("SELECT Remaining_Time FROM Member WHERE Person_ID = ?", (current_user.id,))
                res = cursor.fetchone()
                if res and res[0] <= 0:
                    cursor.execute("""
                        UPDATE Computer SET Status = 'Online', Current_Member_ID = NULL WHERE Current_Member_ID = ?
                    """, (current_user.id,))
                connect.commit()
                connect.close()
        session['last_active'] = now

# ==========================================
# 🏠 เส้นทางหน้าเว็บ (Routes)
# ==========================================
@app.route("/")
def home():
    return redirect(url_for("login"))

@app.route("/dashboard")
@login_required
def dashboard():
    connect = sqlite3.connect(db_local)
    cursor = connect.cursor()
    cursor.execute("SELECT Remaining_Time FROM Member WHERE Person_ID = ?", (current_user.id,))
    member_data = cursor.fetchone()
    connect.close()
    
    total_seconds = member_data[0] if member_data and member_data[0] is not None else 0
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    seconds = total_seconds % 60
    time_display = f"{hours} : {minutes:02d} : {seconds:02d}"

    return render_template("dashboard.html", username=current_user.full_name, time_display=time_display, total_seconds=total_seconds)

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        connect = sqlite3.connect(db_local, timeout=20) 
        connect.row_factory = sqlite3.Row
        cursor = connect.cursor()
        cursor.execute("SELECT * FROM Person WHERE Username = ? AND Password = ?", (username, password))
        user = cursor.fetchone()

        if user:
            user_obj = User(user["Person_ID"], user["Username"], user["Full_Name"], user["Type"])
            login_user(user_obj)
            
            if user_obj.role != "Admin":
                # 🌟 ใช้ระบบจับ IP ของเครื่องที่เข้าเว็บอัตโนมัติ
                user_ip = request.remote_addr
                
                # เปลี่ยนมาดึงค่า Computer_ID แทน เพื่อให้ตรงกับโครงสร้างเก่า
                cursor.execute("SELECT Computer_ID FROM Computer WHERE IP_Address = ?", (user_ip,))
                pc = cursor.fetchone()
                
                if pc:
                    # 🌟 อัปเดตสถานะโดยใช้ Computer_ID เพื่อความแม่นยำและไม่กระทบระบบเดิม
                    cursor.execute("""
                        UPDATE Computer SET Status = 'In Use', Current_Member_ID = ? WHERE Computer_ID = ?
                    """, (user_obj.id, pc["Computer_ID"]))
                    connect.commit()
                else:
                    flash(f"⚠️ กำลังใช้งานจากอุปกรณ์ที่ไม่ได้ลงทะเบียน (IP: {user_ip})")

            connect.close()
            return redirect(url_for("admin_home") if user_obj.role == "Admin" else url_for("dashboard"))
        else:
            connect.close()
            flash("❌ Username หรือ Password ไม่ถูกต้อง!")

    return render_template("login.html")

@app.route("/logout")
@login_required
def logout():
    if current_user.role != "Admin":
        connect = sqlite3.connect(db_local)
        cursor = connect.cursor()
        cursor.execute("""
            UPDATE Computer SET Status = 'Online', Current_Member_ID = NULL WHERE Current_Member_ID = ?
        """, (current_user.id,))
        connect.commit()
        connect.close()

    session.pop('last_active', None) 
    logout_user()
    return redirect(url_for("login"))

@app.route("/contact", methods=["GET", "POST"])
@login_required
def contact():
    # โค้ด Contact (คงเดิม)
    if request.method == "POST":
        data = request.get_json()
        message = data.get("message")
        if message:
            connect = sqlite3.connect(db_local)
            cursor = connect.cursor()
            cursor.execute("INSERT INTO messages (username, message) VALUES (?, ?)", (current_user.full_name, message))
            connect.commit()
            connect.close()
            return jsonify({"status": "success"})
    return render_template("contact.html")

@app.route("/credit", methods=["GET", "POST"])
@login_required
def credit():
    if request.method == "POST":
        selected_time = request.form.get("package")
        packages = {"30min": ("30 นาที", 20), "1hour": ("1 ชั่วโมง", 40), "1_30": ("1 ชั่วโมง 30 นาที", 60), "2hour": ("2 ชั่วโมง", 80), "2_30": ("2 ชั่วโมง 30 นาที", 100), "3hour": ("3 ชั่วโมง", 120)}
        if selected_time in packages:
            hour_text, price = packages[selected_time]
            return render_template("payment.html", hour_text=hour_text, price=price, qr_name="qr"+str(price))
    return render_template("credit.html")

@app.route("/upload_qr", methods=["GET", "POST"])
@login_required
def upload_qr():
    # โค้ด Upload (คงเดิม)
    if request.method == "POST":
        selected_price = request.form.get("price")
        if "image" not in request.files: return "No file selected"
        file = request.files["image"]
        if file.filename == "" or not selected_price: return "Invalid input"
        qr_name = "qr" + selected_price
        img_data = file.read()
        connect = sqlite3.connect(db_local)
        cursor = connect.cursor()
        cursor.execute("SELECT id FROM images WHERE name = ?", (qr_name,))
        if cursor.fetchone():
            cursor.execute("UPDATE images SET image = ? WHERE name = ?", (img_data, qr_name))
        else:
            cursor.execute("INSERT INTO images (name, image) VALUES (?, ?)", (qr_name, img_data))
        connect.commit()
        connect.close()
        return f"{qr_name} uploaded successfully"
    return '''<h2>Upload QR</h2><form method="POST" enctype="multipart/form-data"><select name="price"><option value="20">20</option><option value="40">40</option><option value="60">60</option><option value="80">80</option><option value="100">100</option><option value="120">120</option></select><input type="file" name="image"><input type="submit" value="Upload"></form>'''

@app.route("/confirm_payment/<int:price>")
@login_required
def confirm_payment(price):
    seconds_to_add = price * 90 
    connect = sqlite3.connect(db_local)
    cursor = connect.cursor()
    cursor.execute("UPDATE Member SET Remaining_Time = Remaining_Time + ? WHERE Person_ID = ?", (seconds_to_add, current_user.id))
    connect.commit()
    connect.close()
    return redirect(url_for("dashboard"))

# ==========================================
# 🛡️ ส่วนของผู้ดูแลระบบ (Admin Routes)
# ==========================================
@app.route("/admin_home")
@login_required
def admin_home():
    if current_user.role != "Admin": return redirect(url_for("dashboard"))
    return render_template("AdminHome.html", username=current_user.full_name)

@app.route("/admin_addtime", methods=["GET", "POST"])
@login_required
def admin_addtime():
    if current_user.role != "Admin": return redirect(url_for("dashboard"))
    if request.method == "POST":
        target_username = request.form.get("username")
        minutes = request.form.get("minutes")
        if target_username and minutes:
            connect = sqlite3.connect(db_local)
            cursor = connect.cursor()
            cursor.execute("SELECT Person_ID FROM Person WHERE Username = ?", (target_username,))
            user = cursor.fetchone()
            if user:
                cursor.execute("UPDATE Member SET Remaining_Time = Remaining_Time + ? WHERE Person_ID = ?", (int(minutes)*60, user[0]))
                connect.commit()
                flash(target_username, "success")
            else:
                flash("ไม่พบ Username นี้", "error")
            connect.close()
            return redirect(url_for("admin_addtime"))
    return render_template("AdminAddtime.html")

@app.route("/admin_register", methods=["GET", "POST"])
@login_required
def admin_register():
    if current_user.role != "Admin": return redirect(url_for("dashboard"))
    success = False 
    if request.method == "POST":
        user = request.form.get("username")
        pwd = request.form.get("password")
        phone = request.form.get("phone")
        role = request.form.get("role")
        connect = sqlite3.connect(db_local)
        cursor = connect.cursor()
        try:
            cursor.execute("INSERT INTO Person (Username, Password, Full_Name, Type) VALUES (?, ?, ?, ?)", (user, pwd, user, role))
            new_id = cursor.lastrowid
            cursor.execute("INSERT INTO Member (Person_ID, Phone_Number, Remaining_Time) VALUES (?, ?, 0)", (new_id, phone))
            connect.commit()
            success = True
        except Exception as e:
            flash("❌ เกิดข้อผิดพลาด")
        finally:
            connect.close()
    return render_template("AdminRegister.html", success=success)

@app.route("/admin_messages")
@login_required
def admin_messages():
    if current_user.role != "Admin": return redirect(url_for("dashboard"))
    connect = sqlite3.connect(db_local)
    cursor = connect.cursor()
    cursor.execute("SELECT id, username, message, created_at FROM messages ORDER BY created_at DESC")
    all_messages = cursor.fetchall()
    connect.close()
    return render_template("admin_messages.html", messages=all_messages)

@app.route("/delete_message/<int:msg_id>")
@login_required
def delete_message(msg_id):
    if current_user.role != "Admin": return redirect(url_for("dashboard"))
    connect = sqlite3.connect(db_local)
    cursor = connect.cursor()
    cursor.execute("DELETE FROM messages WHERE id = ?", (msg_id,))
    connect.commit()
    connect.close()
    return redirect(url_for("admin_messages"))

@app.route("/admin_reset_password", methods=["GET", "POST"])
@login_required
def admin_reset_password():
    if current_user.role != "Admin": return redirect(url_for("dashboard"))
    success = False
    if request.method == "POST":
        target_username = request.form.get("username")
        new_password = request.form.get("new_password")
        if target_username and new_password:
            connect = sqlite3.connect(db_local)
            cursor = connect.cursor()
            cursor.execute("SELECT Person_ID FROM Person WHERE Username = ?", (target_username,))
            user = cursor.fetchone()
            if user:
                cursor.execute("UPDATE Person SET Password = ? WHERE Person_ID = ?", (new_password, user[0]))
                connect.commit()
                success = True 
            else:
                flash("❌ ไม่พบ Username นี้ในระบบ")
            connect.close()
    return render_template("AdminResetPassword.html", success=success)

@app.route("/admin_user_edit", methods=["GET", "POST"])
@login_required
def admin_user_edit():
    if current_user.role != "Admin": return redirect(url_for("dashboard"))
    success = False
    if request.method == "POST":
        target_user = request.form.get("target_username")
        new_pass = request.form.get("new_password")
        new_tel = request.form.get("new_tel")
        connect = sqlite3.connect(db_local)
        cursor = connect.cursor()
        cursor.execute("SELECT Person_ID FROM Person WHERE Username = ?", (target_user,))
        user = cursor.fetchone()
        if user:
            if new_pass: cursor.execute("UPDATE Person SET Password = ? WHERE Person_ID = ?", (new_pass, user[0]))
            if new_tel: cursor.execute("UPDATE Member SET Phone_Number = ? WHERE Person_ID = ?", (new_tel, user[0]))
            connect.commit()
            success = True
        else:
            flash("user_not_found") 
        connect.close()
    return render_template("AdminUserEdit.html", success=success)

@app.route("/status")
@login_required
def status():
    if current_user.role != "Admin":
        return redirect(url_for('index'))
    
    connect = sqlite3.connect(db_local)
    connect.row_factory = sqlite3.Row
    cursor = connect.cursor()

    # 🌟 ดึงข้อมูลทั้ง Computer_ID แบบเก่า และ IP_Address มาใช้ร่วมกัน
    query = """
        SELECT c.Computer_ID, c.IP_Address, c.Computer_Name, c.Status, p.Full_Name 
        FROM Computer c 
        LEFT JOIN Person p ON c.Current_Member_ID = p.Person_ID
    """
    cursor.execute(query)
    computers = cursor.fetchall()
    connect.close()
    
    return render_template("status.html", computers=computers)

@app.route("/view_qr/<qr_name>")
@login_required
def view_qr(qr_name):
    connect = sqlite3.connect(db_local)
    cursor = connect.cursor()
    cursor.execute("SELECT image FROM images WHERE name = ?", (qr_name,))
    data = cursor.fetchone()
    connect.close()
    if data: return send_file(io.BytesIO(data[0]), mimetype="image/png")
    return "No QR found"

@app.route("/profile")
@login_required
def profile():
    connect = sqlite3.connect(db_local)
    connect.row_factory = sqlite3.Row
    cursor = connect.cursor()
    cursor.execute("SELECT Remaining_Time, Phone_Number FROM Member WHERE Person_ID = ?", (current_user.id,))
    m_data = cursor.fetchone()
    cursor.execute("SELECT Password FROM Person WHERE Person_ID = ?", (current_user.id,))
    p_data = cursor.fetchone()
    connect.close()
    total_sec = m_data["Remaining_Time"] if m_data else 0
    phone = m_data["Phone_Number"] if m_data and m_data["Phone_Number"] else "-"
    pwd = p_data["Password"] if p_data else "********"
    return render_template("profile.html", username=current_user.username, password=pwd, total_seconds=total_sec, phone=phone)

@app.route('/change_password', methods=['GET', 'POST'])
@login_required
def change_password():
    if request.method == "POST":
        old_pass = request.form.get("old_password")
        new_pass = request.form.get("new_password")
        connect = sqlite3.connect(db_local)
        cursor = connect.cursor()
        cursor.execute("SELECT * FROM Person WHERE Person_ID = ? AND Password = ?", (current_user.id, old_pass))
        if cursor.fetchone():
            cursor.execute("UPDATE Person SET Password = ? WHERE Person_ID = ?", (new_pass, current_user.id))
            connect.commit()
            flash("✅ เปลี่ยนรหัสผ่านสำเร็จ")
            return redirect(url_for("dashboard"))
        connect.close()
        flash("❌ รหัสผ่านเดิมไม่ถูกต้อง")
    return render_template("change_password.html")

@app.route('/forgot_password', methods=['GET', 'POST'])
def forgot_password():
    show_reset_form = session.get('can_reset_password', False)
    if request.method == 'POST':
        if "phone" in request.form:
            phone_input = request.form['phone'].strip()
            connect = sqlite3.connect(db_local)
            cursor = connect.cursor()
            cursor.execute("SELECT Person_ID FROM Member WHERE Phone_Number = ?", (phone_input,))
            member = cursor.fetchone()
            connect.close()
            
            if member:
                otp = random.randint(100000, 999999)
                session['otp'] = str(otp)
                session['reset_person_id'] = member[0]
                flash(f"✅ พบข้อมูลสมาชิก! OTP คือ: {otp}", "success")
            else:
                flash(f"❌ ไม่พบเบอร์โทร {phone_input} ในระบบ", "danger")
            return redirect(url_for('forgot_password'))
        
        elif "otp_input" in request.form:
            if request.form['otp_input'] == session.get('otp'):
                session['can_reset_password'] = True
                flash("✅ ยืนยัน OTP สำเร็จ กรุณาตั้งรหัสผ่านใหม่", "success")
            else:
                flash("❌ OTP ไม่ถูกต้อง", "danger")
            return redirect(url_for('forgot_password'))

        elif "new_password" in request.form:
            new_p = request.form.get("new_password")
            if new_p == request.form.get("confirm_password"):
                connect = sqlite3.connect(db_local)
                cursor = connect.cursor()
                cursor.execute("UPDATE Person SET Password = ? WHERE Person_ID = ?", (new_p, session.get('reset_person_id')))
                connect.commit()
                connect.close()
                session.clear()
                flash("✅ เปลี่ยนรหัสผ่านสำเร็จแล้ว!", "success")
                return redirect(url_for('login'))
            flash("❌ รหัสไม่ตรงกัน", "danger")

    return render_template('forgot_password.html', show_reset_form=show_reset_form)

if __name__ == "__main__":
    setup_database() # เรียกฟังก์ชันเดียวจบ ครอบคลุมทุกตาราง
    app.run(host='0.0.0.0', port=5000, debug=True)