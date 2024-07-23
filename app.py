from flask import Flask, request, jsonify, render_template, redirect, url_for, session, Response
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from datetime import datetime, timezone
from functools import wraps
import os
from sqlalchemy import func
from io import StringIO
import csv
import requests
import base64

app = Flask(__name__)

# Ensure the instance folder exists
if not os.path.exists('instance'):
    os.makedirs('instance')

# Use absolute path for database file
db_path = os.path.join(os.path.abspath(os.path.dirname(__file__)), 'instance', 'wigle_data.db')
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{db_path}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = os.urandom(24)  # for session management
db = SQLAlchemy(app)
migrate = Migrate(app, db)

class WigleData(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mac = db.Column(db.String(17), nullable=False)
    ssid = db.Column(db.String(32), nullable=False)
    auth_mode = db.Column(db.String(20), nullable=False)
    first_seen = db.Column(db.DateTime, nullable=False)
    channel = db.Column(db.Integer, nullable=False)
    rssi = db.Column(db.Integer, nullable=False)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)
    altitude = db.Column(db.Float, nullable=True)
    accuracy = db.Column(db.Float, nullable=True)
    uploaded_to_wigle = db.Column(db.Boolean, default=False)

class Heartbeat(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    mac = db.Column(db.String(17), nullable=False)
    timestamp = db.Column(db.DateTime, nullable=False)
    battery = db.Column(db.Float, nullable=False)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'logged_in' not in session:
            return redirect(url_for('login', next=request.url))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/')
def index():
    page = request.args.get('page', 1, type=int)
    per_page = 20
    networks = WigleData.query.order_by(WigleData.first_seen.desc()).paginate(page=page, per_page=per_page)
    
    latest_heartbeats = db.session.query(
        Heartbeat.mac,
        func.max(Heartbeat.timestamp).label('latest_timestamp')
    ).group_by(Heartbeat.mac).subquery()

    heartbeats = db.session.query(Heartbeat).join(
        latest_heartbeats,
        (Heartbeat.mac == latest_heartbeats.c.mac) &
        (Heartbeat.timestamp == latest_heartbeats.c.latest_timestamp)
    ).all()
    
    unique_entries = db.session.query(func.count(func.distinct(WigleData.mac))).scalar()
    total_entries = db.session.query(func.count(WigleData.id)).scalar()
    
    return render_template('index.html', networks=networks, heartbeats=heartbeats, 
                           unique_entries=unique_entries, total_entries=total_entries)

@app.route('/api/wigle_data', methods=['POST'])
def receive_wigle_data():
    data = request.json
    first_seen = datetime.now(timezone.utc)
    
    new_entry = WigleData(
        mac=data.get('mac', 'Unknown'),
        ssid=data.get('ssid', 'Unknown'),
        auth_mode=data.get('auth_mode', 'Unknown'),
        first_seen=first_seen,
        channel=data.get('channel', 0),
        rssi=data.get('rssi', 0),
        latitude=data.get('latitude'),
        longitude=data.get('longitude'),
        altitude=data.get('altitude'),
        accuracy=data.get('accuracy')
    )
    db.session.add(new_entry)
    db.session.commit()
    return jsonify({"status": "success"}), 201

@app.route('/api/heartbeat', methods=['POST'])
def receive_heartbeat():
    data = request.json
    timestamp = datetime.now(timezone.utc)
    
    new_heartbeat = Heartbeat(
        mac=data.get('mac'),
        timestamp=timestamp,
        battery=data.get('battery')
    )
    db.session.add(new_heartbeat)
    db.session.commit()
    return jsonify({"status": "success", "timestamp": timestamp.isoformat()}), 201

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['username'] == 'CHANGETHIS' and request.form['password'] == 'CHANGETHIS_PASSWORD':
            session['logged_in'] = True
            return redirect(url_for('index'))
        else:
            return render_template('login.html', error='Invalid credentials')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    return redirect(url_for('index'))

@app.route('/clear_database', methods=['POST'])
@login_required
def clear_database():
    try:
        num_deleted = db.session.query(WigleData).delete()
        db.session.commit()
        return jsonify({"status": "success", "deleted_records": num_deleted}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/api/network_data')
def network_data():
    networks = db.session.query(WigleData.ssid, WigleData.latitude, WigleData.longitude, WigleData.mac)\
        .group_by(WigleData.mac)\
        .all()
    return jsonify([{
        'ssid': network.ssid,
        'latitude': network.latitude,
        'longitude': network.longitude
    } for network in networks if network.latitude and network.longitude])

@app.route('/wigle_data')
def wigle_data():
    data = WigleData.query.all()
    csv_data = StringIO()
    csv_writer = csv.writer(csv_data)
    csv_writer.writerow(['WigleWifi-1.6,appRelease=2.78,model=X-37B,release=11.0.0,device=felgercarb,display=OPSPLS1.76-1-S,board=snodgrass,"brand=Moog, LLC",star=Sol,body=3,subBody=0'])
    csv_writer.writerow(['MAC', 'SSID', 'AuthMode', 'FirstSeen', 'Channel', 'RSSI', 'CurrentLatitude', 'CurrentLongitude', 'AltitudeMeters', 'AccuracyMeters', 'Type'])
    for row in data:
        csv_writer.writerow([
            row.mac, row.ssid, f'[{row.auth_mode}]', row.first_seen.strftime('%Y-%m-%d %H:%M:%S'),
            row.channel, row.rssi, row.latitude, row.longitude, row.altitude, row.accuracy, 'WIFI'
        ])
    response = Response(csv_data.getvalue(), mimetype='text/csv')
    response.headers['Content-Disposition'] = 'attachment; filename=wigle_data.csv'
    return response

@app.route('/upload_to_wigle', methods=['POST'])
@login_required
def upload_to_wigle():
    unuploaded_data = WigleData.query.filter_by(uploaded_to_wigle=False).all()
    if not unuploaded_data:
        return jsonify({"status": "info", "message": "No new data to upload"}), 200
    csv_data = StringIO()
    csv_writer = csv.writer(csv_data)
    csv_writer.writerow(['WigleWifi-1.6,appRelease=2.78,model=X-37B,release=11.0.0,device=felgercarb,display=OPSPLS1.76-1-S,board=RF_TRAIL_CAM,"brand=Lozaning",star=Sol,body=3,subBody=0'])
    csv_writer.writerow(['MAC', 'SSID', 'AuthMode', 'FirstSeen', 'Channel', 'RSSI', 'CurrentLatitude', 'CurrentLongitude', 'AltitudeMeters', 'AccuracyMeters', 'Type'])
    for row in unuploaded_data:
        csv_writer.writerow([
            row.mac, row.ssid, f'[{row.auth_mode}]', row.first_seen.strftime('%Y-%m-%d %H:%M:%S'),
            row.channel, row.rssi, row.latitude, row.longitude, row.altitude, row.accuracy, 'WIFI'
        ])
    csv_data.seek(0)
    wigle_api_url = "https://api.wigle.net/api/v2/file/upload"
    wigle_api_key = "CHANGETHIS_WIGGLE_API KEY"
    encoded_key = base64.b64encode(wigle_api_key.encode()).decode()
    headers = {"Authorization": f"Basic {encoded_key}"}
    files = {"file": ("wigle_data.csv", csv_data.getvalue(), "text/csv")}
    response = requests.post(wigle_api_url, headers=headers, files=files)
    if response.status_code == 200:
        for row in unuploaded_data:
            row.uploaded_to_wigle = True
        db.session.commit()
        return jsonify({"status": "success", "message": "Data uploaded to WiGLE successfully"}), 200
    else:
        return jsonify({"status": "error", "message": f"Failed to upload to WiGLE: {response.text}"}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000, debug=True)
