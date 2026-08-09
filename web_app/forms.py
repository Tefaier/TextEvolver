from flask_wtf import FlaskForm
from wtforms import StringField, PasswordField, BooleanField, SubmitField
from wtforms.validators import DataRequired, Length, EqualTo


class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=62)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=4, max=62)])
    remember_me = BooleanField('Remember Me')
    submit = SubmitField('Sign In', name='subm')


class RegisterForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=4, max=62)])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=4, max=62),
                                                     EqualTo('confirm', message='Passwords must match')])
    confirm = PasswordField('Repeat password')
    submit = SubmitField('Register', name='reg')


class SearchForm(FlaskForm):
    search = StringField('Search', validators=[DataRequired()])
    submit = SubmitField('Search')