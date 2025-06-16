from datetime import datetime, timezone, timedelta
from secrets import token_hex
from werkzeug.security import generate_password_hash, check_password_hash
from sqlalchemy import text
from . import db

class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), index=True, unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    created_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc))
    telegram_chat_id = db.Column(db.String(64), nullable=True, unique=True, index=True)
    notify_telegram_movement = db.Column(db.Boolean, nullable=False, server_default=text('1'))
    notify_telegram_disappearance = db.Column(db.Boolean, nullable=False, server_default=text('1'))
    notify_push_movement = db.Column(db.Boolean, nullable=False, server_default=text('0'))
    notify_push_disappearance = db.Column(db.Boolean, nullable=False, server_default=text('0'))

    alarms = db.relationship('Alarm', backref='user', lazy='dynamic', cascade='all, delete-orphan')

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        if self.password_hash is None:
            return False
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f'<User {self.username}>'

class Alarm(db.Model):
    __tablename__ = 'alarms'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    vehicle_track_id = db.Column(db.Integer, nullable=False, index=True)

    set_at = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    unset_at = db.Column(db.DateTime, nullable=True)
    is_active = db.Column(db.Boolean, default=True, index=True)
    last_notification_at = db.Column(db.DateTime(timezone=True), nullable=True)

    events = db.relationship('AlarmEvent', backref='alarm', lazy='dynamic', cascade='all, delete-orphan')

    def __repr__(self):
        status = 'active' if self.is_active else 'inactive'
        return f'<Alarm id={self.id} user_id={self.user_id} vehicle_track_id={self.vehicle_track_id} status={status}'

class AlarmEvent(db.Model):
    __tablename__ = 'alarm_events'

    id = db.Column(db.Integer, primary_key=True)
    alarm_id = db.Column(db.Integer, db.ForeignKey('alarms.id'), nullable=False, index=True)
    event_type = db.Column(db.String(64), nullable=False, index=True)
    timestamp = db.Column(db.DateTime, default=lambda: datetime.now(timezone.utc), index=True)
    details_json = db.Column(db.Text, nullable=True)
    video_path = db.Column(db.String(512), nullable=True)

    def __repr__(self):
        return f'<AlarmEvent id={self.id} alarm_id={self.alarm_id} type={self.event_type} at {self.timestamp}>'

class TelegramVerificationCode(db.Model):
    __tablename__ = 'telegram_verification_codes'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False, index=True)
    code = db.Column(db.String(16), unique=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    expires_at = db.Column(db.DateTime(timezone=True), nullable=False)

    user = db.relationship('User', backref=db.backref('verification_codes', lazy=True))

    def __init__(self, user_id, code_length=6, lifetime_minutes=5):
        self.user_id = user_id
        self.code = token_hex(code_length // 2 + (code_length % 2))[:code_length].upper()
        self.expires_at = datetime.now(timezone.utc) + timedelta(minutes=lifetime_minutes)

    def is_expired(self):
        now_utc = datetime.now(timezone.utc)
        expires_at_from_db = self.expires_at
        if expires_at_from_db.tzinfo is None or expires_at_from_db.tzinfo.utcoffset(expires_at_from_db) is None:
            expires_at_aware = expires_at_from_db.replace(tzinfo=timezone.utc)
        else:
            expires_at_aware = expires_at_from_db
        return now_utc > expires_at_aware

    def __repr__(self):
        return f'<TelegramVerificationCode> user_id={self.user_id} code={self.code}>'
