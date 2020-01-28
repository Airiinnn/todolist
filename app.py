# Python standard libraries
import json
import os
import sqlite3
from datetime import datetime, timedelta

# Third-party libraries
from flask import Flask, render_template, redirect, request, url_for
from flask_login import (
    LoginManager,
    current_user,
    login_required,
    login_user,
    logout_user,
)
from oauthlib.oauth2 import WebApplicationClient
import requests

# Internal imports
from db import init_db_command
from user import User

# Configuration
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", None)
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", None)
GOOGLE_DISCOVERY_URL = (
    "https://accounts.google.com/.well-known/openid-configuration"
)

# Flask app setup
app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY") or os.urandom(24)

# User session management setup
# https://flask-login.readthedocs.io/en/latest
login_manager = LoginManager()
login_manager.init_app(app)

# Naive database setup
try:
    init_db_command()
except sqlite3.OperationalError:
    # Assume it's already been created
    pass

# OAuth 2 client setup
client = WebApplicationClient(GOOGLE_CLIENT_ID)

# Flask-Login helper to retrieve a user from our db
@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)
    
@app.route("/")
def index():
    if current_user.is_authenticated:
        connection = sqlite3.connect("sqlite_db")
        cursor = connection.cursor()
        cursor.execute("SELECT * FROM todo WHERE op='{}'".format(current_user.id))
        todos = cursor.fetchall()
        connection.close()

        now = datetime.now()
        overdue = []
        for i in range(len(todos)):
            due = datetime.strptime(todos[i][3], "%Y-%m-%d")
            if now > due + timedelta(days=1):
                overdue.append(todos[i][0])
        
        return render_template("index.html", name=current_user.name, overdue=overdue, todos=todos)
    else:
        return render_template("login.html")

@app.route("/add")
@login_required
def add():
    return render_template("add.html")

@app.route("/submit", methods=["POST"])
@login_required
def submit():
    title = request.form.get("title")
    details = request.form.get("details")
    date = request.form.get("date")
    
    connection = sqlite3.connect("sqlite_db")
    cursor = connection.cursor()
    cursor.execute(
        "INSERT INTO todo (title, details, due, op) "
        "VALUES (?, ?, ?, ?)",
        (title, details, date, current_user.id)
    )
    connection.commit()
    connection.close()

    return redirect("/")

@app.route("/delete")
@login_required
def delete():
    connection = sqlite3.connect("sqlite_db")
    cursor = connection.cursor()
    cursor.execute("SELECT * FROM todo WHERE op='{}'".format(current_user.id))
    todos = cursor.fetchall()
    connection.close()
    return render_template("delete.html", todos=todos)

@app.route("/deletion", methods=["POST"])
@login_required
def deletion():
    target = request.form.getlist("target")
    connection = sqlite3.connect("sqlite_db")
    cursor = connection.cursor()
    for todelete in target:
        cursor.execute("DELETE FROM todo WHERE id={}".format(todelete))
    connection.commit()
    connection.close()

    return redirect("/")
        
def get_google_provider_cfg():
    return requests.get(GOOGLE_DISCOVERY_URL).json()

@app.route("/login")
def login():
    # Find out what URL to hit for Google login
    google_provider_cfg = get_google_provider_cfg()
    authorization_endpoint = google_provider_cfg["authorization_endpoint"]

    # Use library to construct the request for Google login and provide
    # scopes that let you retrieve user's profile from Google
    request_uri = client.prepare_request_uri(
        authorization_endpoint,
        redirect_uri=request.base_url + "/callback",
        scope=["openid", "email", "profile"],
    )
    return redirect(request_uri)
    
@app.route("/login/callback")
def callback():
    # Get authorization code Google sent back to you
    code = request.args.get("code")
    
    # Find out what URL to hit to get tokens that allow you to ask for
    # things on behalf of a user
    google_provider_cfg = get_google_provider_cfg()
    token_endpoint = google_provider_cfg["token_endpoint"]
    
    # Prepare and send a request to get tokens! Yay tokens!
    token_url, headers, body = client.prepare_token_request(
        token_endpoint,
        authorization_response=request.url,
        redirect_url=request.base_url,
        code=code
    )
    token_response = requests.post(
        token_url,
        headers=headers,
        data=body,
        auth=(GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET),
    )

    # Parse the tokens!
    client.parse_request_body_response(json.dumps(token_response.json()))
    
    # Now that you have tokens (yay) let's find and hit the URL
    # from Google that gives you the user's profile information,
    # including their Google profile image and email
    userinfo_endpoint = google_provider_cfg["userinfo_endpoint"]
    uri, headers, body = client.add_token(userinfo_endpoint)
    userinfo_response = requests.get(uri, headers=headers, data=body)
    
    # You want to make sure their email is verified.
    # The user authenticated with Google, authorized your
    # app, and now you've verified their email through Google!
    if userinfo_response.json().get("email_verified"):
        unique_id = userinfo_response.json()["sub"]
        users_email = userinfo_response.json()["email"]
        users_name = userinfo_response.json()["name"]
    else:
        return "User email not available or not verified by Google.", 400
        
    # Create a user in your db with the information provided
    # by Google
    user = User(
        id_=unique_id, name=users_name, email=users_email
    )

    # Doesn't exist? Add it to the database.
    if not User.get(unique_id):
        User.create(unique_id, users_name, users_email)

    # Begin user session by logging the user in
    login_user(user)

    # Send user back to homepage
    return redirect(url_for("index"))
    
@app.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("index"))

if __name__ == "__main__":
    app.run(ssl_context="adhoc", debug=True)
