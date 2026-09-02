from typing import Optional
import datetime

from sqlalchemy import Boolean, Enum, Float, ForeignKey, Index, Integer, String, TIMESTAMP, Text, text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

class Base(DeclarativeBase):
    pass


class UserAccount(Base):
    __tablename__ = 'user_account'

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(64), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    last_entry: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    setting_limit: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('5'))

    setting: Mapped[list['Setting']] = relationship('Setting', back_populates='owner')
    processing_job: Mapped[list['ProcessingJob']] = relationship('ProcessingJob', back_populates='user')


class Setting(Base):
    __tablename__ = 'setting'
    __table_args__ = (
        Index('ix_setting_owner_id', 'owner_id'),
        Index('ix_setting_public_name', 'public', 'name')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey('user_account.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    public: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    clean_empty: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    convert_to_utf: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    use_comma_separator: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    expect_feet: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

    owner: Mapped['UserAccount'] = relationship('UserAccount', back_populates='setting')
    fandom: Mapped[list['Fandom']] = relationship('Fandom', back_populates='setting')
    image_conversion: Mapped[list['ImageConversion']] = relationship('ImageConversion', back_populates='setting')
    phrase_conversion: Mapped[list['PhraseConversion']] = relationship('PhraseConversion', back_populates='setting')
    processing_job: Mapped[list['ProcessingJob']] = relationship('ProcessingJob', back_populates='setting')
    unit_conversion: Mapped[list['UnitConversion']] = relationship('UnitConversion', back_populates='setting')


class Fandom(Base):
    __tablename__ = 'fandom'
    __table_args__ = (
        Index('ix_fandom_setting_id', 'setting_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(ForeignKey('setting.id'), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
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
    phrase: Mapped[str] = mapped_column(String(64), nullable=False)
    separation: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('1'))
    explanation: Mapped[str] = mapped_column(String(64), nullable=False)
    mutations: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    images: Mapped[str] = mapped_column(Text, nullable=False)

    setting: Mapped['Setting'] = relationship('Setting', back_populates='image_conversion')


class PhraseConversion(Base):
    __tablename__ = 'phrase_conversion'
    __table_args__ = (
        Index('ix_phrase_conversion_setting_id', 'setting_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(ForeignKey('setting.id'), nullable=False)
    phrase_from: Mapped[str] = mapped_column(String(64), nullable=False)
    phrase_to: Mapped[str] = mapped_column(String(64), nullable=False)
    direct: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    mutations: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    regex: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

    setting: Mapped['Setting'] = relationship('Setting', back_populates='phrase_conversion')


class ProcessingJob(Base):
    __tablename__ = 'processing_job'
    __table_args__ = (
        Index('ix_processing_job_created_status', 'created_at', 'status'),
        Index('ix_processing_job_user_id', 'user_id'),
        Index('ux_processing_job_active_user', 'user_id', unique=True)
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey('user_account.id'), nullable=False)
    setting_id: Mapped[int] = mapped_column(ForeignKey('setting.id'), nullable=False)
    status: Mapped[str] = mapped_column(Enum('queued', 'running', 'completed', 'failed', 'cancelled'), nullable=False, server_default=text("'queued'"))
    cancellation_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))
    created_at: Mapped[datetime.datetime] = mapped_column(TIMESTAMP, nullable=False, server_default=text('CURRENT_TIMESTAMP'))
    started_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    finished_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    heartbeat_at: Mapped[Optional[datetime.datetime]] = mapped_column(TIMESTAMP)
    error_message: Mapped[Optional[str]] = mapped_column(Text)

    setting: Mapped['Setting'] = relationship('Setting', back_populates='processing_job')
    user: Mapped['UserAccount'] = relationship('UserAccount', back_populates='processing_job')


class UnitConversion(Base):
    __tablename__ = 'unit_conversion'
    __table_args__ = (
        Index('ix_unit_conversion_setting_id', 'setting_id'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    setting_id: Mapped[int] = mapped_column(ForeignKey('setting.id'), nullable=False)
    phrase_from: Mapped[str] = mapped_column(String(64), nullable=False)
    phrase_to: Mapped[str] = mapped_column(String(64), nullable=False)
    conversion: Mapped[float] = mapped_column(Float, nullable=False)
    can_be_word: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text('FALSE'))

    setting: Mapped['Setting'] = relationship('Setting', back_populates='unit_conversion')
