from web_app import db, login
from flask_login import UserMixin
import datetime


@login.user_loader
def load_user(id):
    user = db.session.query(User).get(int(id))
    user.last_entry = datetime.datetime.utcnow()
    db.session.add(user)
    db.session.commit()
    return user


def create_user(name, password):
    user = User(name=name, password=password)
    db.session.add(user)
    db.session.commit()
    return user


def create_setting(user_id, copy_id = None):
    if copy_id == None:
        new_setting = Setting(name="New_setting", public=False, user=User.query.get(int(user_id)))
        db.session.add(new_setting)
        fandoms = ["Pokemons"]
        for fandom in fandoms:
            new_fandom = Fandom(name=fandom, setting=new_setting)
            db.session.add(new_fandom)
        db.session.commit()
    else:
        origin_setting = db.session.query(Setting).get(int(copy_id))
        new_setting = Setting(name=origin_setting.name, public=False, clean_empty=origin_setting.clean_empty,
                              convert_to_utf=origin_setting.convert_to_utf, use_coma_sep=origin_setting.use_coma_sep,
                              expect_feet=origin_setting.expect_feet, user=User.query.get(int(user_id)))
        db.session.add(new_setting)
        for fandom in origin_setting.fandoms:
            new_fandom = Fandom(name=fandom.name, active=fandom.active, separation=fandom.separation,
                                support_value_1=fandom.support_value_1, support_value_2=fandom.support_value_2,
                                setting=new_setting)
            db.session.add(new_fandom)
        for unit_conv in origin_setting.unit_convs:
            new_unit_conv = UnitConv(phrase_from=unit_conv.phrase_from, phrase_to=unit_conv.phrase_to,
                                     convertation=unit_conv.convertation, can_be_word=unit_conv.can_be_word,
                                     setting=new_setting)
            db.session.add(new_unit_conv)
        for phrase_conv in origin_setting.phrase_convs:
            new_phrase_conv = PhraseConv(phrase_from=phrase_conv.phrase_from, phrase_to=phrase_conv.phrase_to,
                                         mutations=phrase_conv.mutations, direct=phrase_conv.direct,
                                         setting=new_setting)
            db.session.add(new_phrase_conv)
        for image_conv in origin_setting.image_convs:
            new_image_conv = ImageConv(phrase=image_conv.phrase, separation=image_conv.separation,
                                       explanation=image_conv.explanation, mutations=image_conv.mutations,
                                       images=image_conv.images, setting=new_setting)
            db.session.add(new_image_conv)
        db.session.commit()


def reset_values(settings_id, name, public, clean_empty, to_utf, coma_used, expect_feet, fandoms, unit_convs, phrase_convs, image_convs):
    setting = db.session.query(Setting).get(int(settings_id))
    setting.name = name
    setting.public = public
    setting.clean_empty = clean_empty
    setting.convert_to_utf = to_utf
    setting.use_coma_sep = coma_used
    setting.expect_feet = expect_feet
    db.session.query(Fandom).filter(Fandom.setting_id == settings_id).delete()
    db.session.query(UnitConv).filter(UnitConv.setting_id == settings_id).delete()
    db.session.query(ImageConv).filter(ImageConv.setting_id == settings_id).delete()
    db.session.query(PhraseConv).filter(PhraseConv.setting_id == settings_id).delete()
    db.session.commit()
    db.session.add_all(fandoms)
    db.session.add_all(unit_convs)
    db.session.add_all(phrase_convs)
    db.session.add_all(image_convs)
    db.session.commit()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    last_entry = db.Column(db.TIMESTAMP, index=True, default=datetime.datetime.utcnow, nullable=False)
    set_limit = db.Column(db.Integer, default=5)
    name = db.Column(db.String(64), index=True, unique=True, nullable=False)
    password = db.Column(db.String(64), nullable=False)
    settings = db.relationship('Setting', backref='user', lazy='dynamic', cascade="all, delete-orphan", primaryjoin="and_(User.id==Setting.owner_id, " "User.name==Setting.owner_name)")
    thread = db.relationship('Thread', backref='user', lazy='dynamic', cascade="all, delete-orphan")

    def __repr__(self):
        return '<User {}>'.format(self.name)


class Setting(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), index=True, nullable=False)
    public = db.Column(db.Boolean, index=True, default=False)
    clean_empty = db.Column(db.Boolean, default=False)
    convert_to_utf = db.Column(db.Boolean, default=False)
    use_coma_sep = db.Column(db.Boolean, default=False)
    expect_feet = db.Column(db.Boolean, default=False)
    fandoms = db.relationship('Fandom', backref='setting', lazy='dynamic', cascade="all, delete-orphan")
    unit_convs = db.relationship('UnitConv', backref='setting', lazy='dynamic', cascade="all, delete-orphan")
    image_convs = db.relationship('ImageConv', backref='setting', lazy='dynamic', cascade="all, delete-orphan")
    phrase_convs = db.relationship('PhraseConv', backref='setting', lazy='dynamic', cascade="all, delete-orphan")
    owner_id = db.Column(db.Integer, db.ForeignKey("user.id"))
    owner_name = db.Column(db.String(64), db.ForeignKey("user.name"))

    def __repr__(self):
        return '<Setting {} and id {}>'.format(self.name, self.id)


class Fandom(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(64), index=True, nullable=False)  # Pokemons
    active = db.Column(db.Boolean, default=False)
    separation = db.Column(db.Integer, default=1)
    support_value_1 = db.Column(db.Boolean, default=False)
    support_value_2 = db.Column(db.Boolean, default=False)
    setting_id = db.Column(db.Integer, db.ForeignKey('setting.id'))

    def __repr__(self):
        return '<Fandom {}>'.format(self.name)


class UnitConv(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    setting_id = db.Column(db.Integer, db.ForeignKey('setting.id'))
    phrase_from = db.Column(db.String(64), nullable=False)
    phrase_to = db.Column(db.String(64), nullable=False)
    convertation = db.Column(db.Float)
    can_be_word = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return '<Unit convertation {}>'.format(self.id)


class ImageConv(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    setting_id = db.Column(db.Integer, db.ForeignKey('setting.id'))
    phrase = db.Column(db.String(64), nullable=False)
    separation = db.Column(db.Integer, default=1)
    explanation = db.Column(db.String(64), nullable=False)
    mutations = db.Column(db.Boolean, default=False)
    images = db.Column(db.String, nullable=False)  # images binaries separated by *

    def __repr__(self):
        return '<Image adder {}>'.format(self.id)


class PhraseConv(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    setting_id = db.Column(db.Integer, db.ForeignKey('setting.id'))
    direct = db.Column(db.Boolean, default=False)
    mutations = db.Column(db.Boolean, default=False)
    phrase_from = db.Column(db.String(64), nullable=False)
    phrase_to = db.Column(db.String(64), nullable=False)

    def __repr__(self):
        return '<Phrase convertation {}>'.format(self.id)


class Thread(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    ident = db.Column(db.Integer, index=True, unique=True)
    setting_id = db.Column(db.Integer)
    waits = db.Column(db.Boolean, default=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'))

    def __repr__(self):
        return '<Thread {}>'.format(self.ident)