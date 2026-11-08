from typing import Optional
import datetime
import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, Float, ForeignKey, Index, Integer, TIMESTAMP, Text, UniqueConstraint, Uuid, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class LoginFailure(Base):
    __tablename__ = 'login_failure'
    __table_args__ = (
        CheckConstraint('failure_count >= 1'),
        Index('ix_login_failure_window_started_at', 'window_started_at')
    )

    username: Mapped[str] = mapped_column(Text, primary_key=True)
    ip: Mapped[str] = mapped_column(Text, primary_key=True)
    failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    window_started_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)


class LoginFailureByAccount(Base):
    __tablename__ = 'login_failure_by_account'
    __table_args__ = (
        CheckConstraint('failure_score >= 1'),
        Index('ix_login_failure_by_account_last_failure_at', 'last_failure_at')
    )

    username: Mapped[str] = mapped_column(Text, primary_key=True)
    failure_score: Mapped[int] = mapped_column(Integer, nullable=False)
    last_failure_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)


class SettingImageDeletion(Base):
    __tablename__ = 'setting_image_deletion'

    object_key: Mapped[str] = mapped_column(Text, primary_key=True)


class UserAccount(Base):
    __tablename__ = 'user_account'
    __table_args__ = (
        CheckConstraint("language = 'ru' OR language = 'en'"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    last_entry: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    setting_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('5'))
    language: Mapped[Optional[str]] = mapped_column(Text)

    api_token: Mapped[list['ApiToken']] = relationship('ApiToken', back_populates='user')
    browser_session: Mapped[list['BrowserSession']] = relationship('BrowserSession', back_populates='user')
    google_identity: Mapped['GoogleIdentity'] = relationship('GoogleIdentity', uselist=False, back_populates='user')
    passkey: Mapped[list['Passkey']] = relationship('Passkey', back_populates='user')
    setting: Mapped[list['Setting']] = relationship('Setting', back_populates='owner')
    user_password: Mapped['UserPassword'] = relationship('UserPassword', uselist=False, back_populates='user')
    auth_flow: Mapped[list['AuthFlow']] = relationship('AuthFlow', back_populates='user')
    processing_job: Mapped[list['ProcessingJob']] = relationship('ProcessingJob', back_populates='user')


class ApiToken(Base):
    __tablename__ = 'api_token'
    __table_args__ = (
        CheckConstraint("permission = 'read' OR permission = 'read_write'"),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user_account.id'), primary_key=True)
    permission: Mapped[str] = mapped_column(Text, primary_key=True)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    issued_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)

    user: Mapped['UserAccount'] = relationship('UserAccount', back_populates='api_token')


class BrowserSession(Base):
    __tablename__ = 'browser_session'
    __table_args__ = (
        Index('ix_browser_session_expires_at', 'expires_at'),
        Index('ix_browser_session_user_id', 'user_id')
    )

    token_hash: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('user_account.id'))
    revoked_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    authenticated_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    authentication_method: Mapped[Optional[str]] = mapped_column(Text)

    user: Mapped[Optional['UserAccount']] = relationship('UserAccount', back_populates='browser_session')
    auth_flow: Mapped[list['AuthFlow']] = relationship('AuthFlow', back_populates='browser_session')
    browser_session_flash: Mapped[list['BrowserSessionFlash']] = relationship('BrowserSessionFlash', back_populates='browser_session')


class GoogleIdentity(Base):
    __tablename__ = 'google_identity'
    __table_args__ = (
        UniqueConstraint('issuer', 'subject', name='uq_google_identity'),
    )

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user_account.id'), primary_key=True)
    issuer: Mapped[str] = mapped_column(Text, nullable=False)
    subject: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped['UserAccount'] = relationship('UserAccount', back_populates='google_identity')


class Passkey(Base):
    __tablename__ = 'passkey'
    __table_args__ = (
        Index('ix_passkey_user_id', 'user_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user_account.id'), nullable=False)
    credential_id: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)
    sign_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text('0'))
    name: Mapped[str] = mapped_column(Text, nullable=False)
    transports: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'[]'"))
    backed_up: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)
    last_used_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)

    user: Mapped['UserAccount'] = relationship('UserAccount', back_populates='passkey')
    auth_flow: Mapped[list['AuthFlow']] = relationship('AuthFlow', back_populates='passkey')


class Setting(Base):
    __tablename__ = 'setting'
    __table_args__ = (
        Index('ix_setting_owner_id', 'owner_id'),
        Index('ix_setting_public_order', 'name', 'id')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user_account.id'), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    clean_empty: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    convert_to_utf: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    use_comma_separator: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    deletion_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    expect_feet: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

    owner: Mapped['UserAccount'] = relationship('UserAccount', back_populates='setting')
    fandom: Mapped[list['Fandom']] = relationship('Fandom', back_populates='setting')
    image_conversion: Mapped[list['ImageConversion']] = relationship('ImageConversion', back_populates='setting')
    phrase_conversion: Mapped[list['PhraseConversion']] = relationship('PhraseConversion', back_populates='setting')
    processing_job: Mapped[list['ProcessingJob']] = relationship('ProcessingJob', back_populates='setting')
    unit_conversion: Mapped[list['UnitConversion']] = relationship('UnitConversion', back_populates='setting')


class UserPassword(Base):
    __tablename__ = 'user_password'

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user_account.id'), primary_key=True)
    password_hash: Mapped[str] = mapped_column(Text, nullable=False)

    user: Mapped['UserAccount'] = relationship('UserAccount', back_populates='user_password')


class AuthFlow(Base):
    __tablename__ = 'auth_flow'
    __table_args__ = (
        Index('ix_auth_flow_browser_session_token_hash', 'browser_session_token_hash'),
        Index('ix_auth_flow_expires_at', 'expires_at'),
        Index('ix_auth_flow_user_id', 'user_id'),
        Index('uq_auth_flow_session_purpose', 'browser_session_token_hash', 'purpose', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    browser_session_token_hash: Mapped[str] = mapped_column(ForeignKey('browser_session.token_hash'), nullable=False)
    purpose: Mapped[str] = mapped_column(Text, nullable=False)
    token_hash: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    data: Mapped[str] = mapped_column(Text, nullable=False)
    expires_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False)
    user_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey('user_account.id'))
    passkey_id: Mapped[Optional[int]] = mapped_column(ForeignKey('passkey.id'))

    browser_session: Mapped['BrowserSession'] = relationship('BrowserSession', back_populates='auth_flow')
    passkey: Mapped[Optional['Passkey']] = relationship('Passkey', back_populates='auth_flow')
    user: Mapped[Optional['UserAccount']] = relationship('UserAccount', back_populates='auth_flow')


class BrowserSessionFlash(Base):
    __tablename__ = 'browser_session_flash'
    __table_args__ = (
        Index('ix_browser_session_flash_token_hash', 'browser_session_token_hash'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    browser_session_token_hash: Mapped[str] = mapped_column(ForeignKey('browser_session.token_hash'), nullable=False)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)

    browser_session: Mapped['BrowserSession'] = relationship('BrowserSession', back_populates='browser_session_flash')


class Fandom(Base):
    __tablename__ = 'fandom'
    __table_args__ = (
        Index('ix_fandom_setting_id', 'setting_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(ForeignKey('setting.id'), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    separation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    support_value_1: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    support_value_2: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

    setting: Mapped['Setting'] = relationship('Setting', back_populates='fandom')


class ImageConversion(Base):
    __tablename__ = 'image_conversion'
    __table_args__ = (
        Index('ix_image_conversion_setting_id', 'setting_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(ForeignKey('setting.id'), nullable=False)
    phrase: Mapped[str] = mapped_column(Text, nullable=False)
    separation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    mutations: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

    setting: Mapped['Setting'] = relationship('Setting', back_populates='image_conversion')
    image_conversion_file: Mapped[list['ImageConversionFile']] = relationship('ImageConversionFile', back_populates='image_conversion')


class PhraseConversion(Base):
    __tablename__ = 'phrase_conversion'
    __table_args__ = (
        Index('ix_phrase_conversion_setting_id', 'setting_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(ForeignKey('setting.id'), nullable=False)
    phrase_from: Mapped[str] = mapped_column(Text, nullable=False)
    phrase_to: Mapped[str] = mapped_column(Text, nullable=False)
    direct: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    mutations: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    regex: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

    setting: Mapped['Setting'] = relationship('Setting', back_populates='phrase_conversion')


class ProcessingJob(Base):
    __tablename__ = 'processing_job'
    __table_args__ = (
        CheckConstraint("status = 'queued' OR status = 'running' OR status = 'completed' OR status = 'failed' OR status = 'cancelled'"),
        Index('ix_processing_job_queued_order', 'created_at', 'id'),
        Index('ix_processing_job_setting_status', 'setting_id', 'status'),
        Index('ix_processing_job_user_id', 'user_id'),
        Index('ux_processing_job_active_user', 'user_id', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey('user_account.id'), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'queued'"))
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    setting_id: Mapped[Optional[int]] = mapped_column(ForeignKey('setting.id'))
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    heartbeat_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    setting: Mapped[Optional['Setting']] = relationship('Setting', back_populates='processing_job')
    user: Mapped['UserAccount'] = relationship('UserAccount', back_populates='processing_job')


class UnitConversion(Base):
    __tablename__ = 'unit_conversion'
    __table_args__ = (
        Index('ix_unit_conversion_setting_id', 'setting_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(ForeignKey('setting.id'), nullable=False)
    phrase_from: Mapped[str] = mapped_column(Text, nullable=False)
    phrase_to: Mapped[str] = mapped_column(Text, nullable=False)
    conversion: Mapped[float] = mapped_column(Float, nullable=False)
    can_be_word: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

    setting: Mapped['Setting'] = relationship('Setting', back_populates='unit_conversion')


class ImageConversionFile(Base):
    __tablename__ = 'image_conversion_file'
    __table_args__ = (
        CheckConstraint('position >= 0'),
        CheckConstraint('size_bytes > 0'),
        UniqueConstraint('image_conversion_id', 'position', name='uq_image_conversion_file_position'),
        UniqueConstraint('object_key', name='uq_image_conversion_file_object_key')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_conversion_id: Mapped[int] = mapped_column(ForeignKey('image_conversion.id'), nullable=False)
    object_key: Mapped[str] = mapped_column(Text, nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    image_conversion: Mapped['ImageConversion'] = relationship('ImageConversion', back_populates='image_conversion_file')
