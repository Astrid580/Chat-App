from flask import Flask, render_template, request, session, redirect, url_for
from flask_socketio import join_room, leave_room, send, SocketIO
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from flask_sqlalchemy import SQLAlchemy
import random
from string import ascii_uppercase
from cryptography.fernet import Fernet
import os

app = Flask(__name__)
app.config["SECRET_KEY"] = "periwinkle"
socketio = SocketIO(app)

rooms = {}

if not os.path.exists("secret.key"):
    with open("secret.key", "wb") as key_file:
        key_file.write(Fernet.generate_key())

def load_key():
    return open("secret.key", "rb").read()

key = load_key()
cipher = Fernet(key)

def encrypt_msg(message):
    return cipher.encrypt(message.encode()).decode()

def decrypt_msg(encrypted_message):
    try:
        return cipher.decrypt(encrypted_message.encode()).decode()
    except:
        return "[Error: Could not decrypt message]"


def generate_unique_code(length):
    code = ""
    while True:
        for _ in range(length):
            code += random.choice(ascii_uppercase)
        if code not in rooms:
            break
    return code

app.config["SQLALCHEMY_DATABASE_URI"] = "sqlite:///users.db"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
db = SQLAlchemy(app)

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(25), unique=True, nullable=False)
    password_hash = db.Column(db.String(150), nullable=False)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)


@app.route("/")
def Home():
    if "username" in session:
        return redirect(url_for('home'))
    return render_template("index.html")

@app.route("/login", methods=["POST"])
def login():
    username = request.form['username']
    password = request.form['password']
    user = User.query.filter_by(username=username).first()
    if user and user.check_password(password):
        session['username'] = username
        return redirect(url_for('home'))
    else:
        return render_template('index.html', error='Invalid username or password.')

@app.route("/register", methods=["POST"])
def register():
    username = request.form['username']
    password = request.form['password']
    user = User.query.filter_by(username=username).first()
    if user:
        return render_template("index.html", error="User already exists.")
    else:
        new_user = User(username=username)
        new_user.set_password(password)
        db.session.add(new_user)
        db.session.commit()
        session['username'] = username
        return redirect(url_for('home'))

@app.route('/dashboard')
def dashboard():
    if 'username' in session:
        return render_template('home.html', username=session['username'])
    return redirect(url_for('Home'))

@app.route("/logout", methods=["GET", "POST"])
def logout():
    session.clear()
    return redirect(url_for('Home'))

@app.route("/home", methods=["POST", "GET"])
def home():
    if "username" not in session:
        return redirect(url_for('Home'))

    if request.method == "POST":
        name = session.get("username")
        code = request.form.get("code")
        join = request.form.get("join", False)
        create = request.form.get("create", False)
        
        color = request.form.get("color")
        if not color: color = "#ffffff"

        if not name:
            return redirect(url_for('Home'))

        if join != False and not code:
            return render_template("home.html", error="Please enter a room code.", code=code, name=name)
        
        room = code
        if create != False:
            room = generate_unique_code(4)
            rooms[room] = {"members": {}, "messages": []}
        elif code not in rooms:
            return render_template("home.html", error="Room does not exist.", code=code, name=name)

        session["room"] = room
        session["name"] = name
        session["color"] = color
        return redirect(url_for("room"))

    return render_template("home.html", name=session.get("username"))

@app.route("/room")
def room():
    room = session.get("room")
    name = session.get("name") or session.get("username")
    
    if room is None or name is None or room not in rooms:
        return redirect(url_for("home"))

    decrypted_messages = []
    for msg in rooms[room]["messages"]:
        temp_msg = msg.copy()
        temp_msg["message"] = decrypt_msg(msg["message"])
        decrypted_messages.append(temp_msg)

    return render_template("room.html", code=room, messages=decrypted_messages)

@socketio.on("message")
def message(data):
    room = session.get("room")
    if room not in rooms:
        return

    msg_type = data.get("type", "text") 
    raw_message = data["data"]
    
    encrypted_content = encrypt_msg(raw_message)

    content_to_store = {
        "name": session.get("name"),
        "message": encrypted_content,
        "type": msg_type,
        "timestamp": datetime.now().strftime("%I:%M %p"),
        "color": session.get("color")
    }
    
    rooms[room]["messages"].append(content_to_store)

    content_to_send = {
        "name": session.get("name"),
        "message": raw_message,
        "type": msg_type,
        "timestamp": datetime.now().strftime("%I:%M %p"),
        "color": session.get("color")
    }

    send(content_to_send, to=room)
    print(f"{session.get('name')} sent a message (saved encrypted)")

@socketio.on("connect")
def connect(auth):
    room = session.get("room")
    name = session.get("name")
    color = session.get("color")
    
    if not room or not name:
        return
    if room not in rooms:
        leave_room(room)
        return

    join_room(room)

    rooms[room]["members"][request.sid] = {"name": name, "color": color}

    send({"name": name, "message": "has entered the room.", "timestamp": datetime.now().strftime("%I:%M %p")}, to=room)
    
    members_list = list(rooms[room]["members"].values())
    socketio.emit("update_members", {"members": members_list}, to=room)
    
    print(f"{name} joined room {room}")

@socketio.on("disconnect")
def disconnect():
    room = session.get("room")
    name = session.get("name")
    leave_room(room)

    if room in rooms:
        if request.sid in rooms[room]["members"]:
            del rooms[room]["members"][request.sid]
        if len(rooms[room]["members"]) == 0:
            del rooms[room]
        else:
            members_list = list(rooms[room]["members"].values())
            socketio.emit("update_members", {"members": members_list}, to=room)

    send({"name": name, "message": "has left the room", "timestamp": datetime.now().strftime("%I:%M %p")}, to=room)
    print(f"{name} has left the room {room}")

if __name__ == "__main__":
    with app.app_context():
        db.create_all()
    socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)